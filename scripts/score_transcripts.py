#!/usr/bin/env python3
"""Score hypothesis transcripts against references.

Reports word error rate two ways:

  * ``literal``  -- surface comparison after punctuation/case stripping.
  * ``semantic`` -- the same, but with spelled-out numbers mapped to digits.

The distinction matters. Whisper emits "2 grams" for spoken "two grams". That
is a formatting difference, not a clinical error, and scoring it as an error
overstates the real error rate. Conversely it must never be silently ignored
downstream: the Phase 4 validator has to treat "two" and "2" as the same
*value* while still refusing to let the value itself change.

Reads pairs of ``<stem>.ref.txt`` / ``<stem>.hyp.txt`` from a directory.
Local-only; no network use.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import unicodedata
from pathlib import Path

# Spoken forms Whisper commonly renders as digits. Deliberately small and
# exact -- this table exists for *scoring*, not for transforming clinical text.
_NUMBER_WORDS = {
    "zero": "0", "one": "1", "two": "2", "three": "3", "four": "4",
    "five": "5", "six": "6", "seven": "7", "eight": "8", "nine": "9",
    "ten": "10", "eleven": "11", "twelve": "12", "thirteen": "13",
    "fourteen": "14", "fifteen": "15", "sixteen": "16", "seventeen": "17",
    "eighteen": "18", "nineteen": "19", "twenty": "20", "thirty": "30",
    "forty": "40", "fifty": "50", "sixty": "60", "seventy": "70",
    "eighty": "80", "ninety": "90", "hundred": "100",
}


def tokenize(
    text: str,
    *,
    normalize_numbers: bool = False,
    normalize_hyphens: bool = False,
) -> list[str]:
    """Lowercase, strip punctuation, split on whitespace.

    ``normalize_hyphens`` splits hyphenated compounds into separate tokens on
    both sides, so ``whole-genome`` and ``whole genome`` compare equal. Whisper
    is inconsistent about compound hyphenation; treated as a substitution it
    also shifts the token count, which inflates the error rate well beyond the
    single real difference.
    """
    text = unicodedata.normalize("NFKC", text).lower()
    # Keep intra-word hyphens (methicillin-susceptible) and decimal points (0.9).
    text = re.sub(r"[^\w\s.\-]", " ", text)
    text = re.sub(r"(?<!\d)\.(?!\d)", " ", text)  # sentence periods, not decimals
    if normalize_hyphens:
        text = text.replace("-", " ")
    tokens = text.split()
    tokens = [t.strip("-") for t in tokens if t.strip("-")]
    if normalize_numbers:
        tokens = [_NUMBER_WORDS.get(t, t) for t in tokens]
    return tokens


def align(ref: list[str], hyp: list[str]) -> list[tuple[str, str | None, str | None]]:
    """Align two token sequences by edit distance.

    Returns a list of (op, ref_token, hyp_token) where op is one of
    ``=``, ``s`` (substitution), ``d`` (deletion), ``i`` (insertion).

    Both scoring and the human-readable diff are derived from this single
    alignment. Comparing the sequences positionally instead would misreport
    every utterance after a token-count change -- one hyphen split would
    cascade into an apparent error on every following word.
    """
    n, m = len(ref), len(hyp)
    dp = [[(0, "")] * (m + 1) for _ in range(n + 1)]
    for i in range(1, n + 1):
        dp[i][0] = (i, "d")
    for j in range(1, m + 1):
        dp[0][j] = (j, "i")
    for i in range(1, n + 1):
        for j in range(1, m + 1):
            if ref[i - 1] == hyp[j - 1]:
                dp[i][j] = (dp[i - 1][j - 1][0], "=")
                continue
            sub = dp[i - 1][j - 1][0] + 1
            dele = dp[i - 1][j][0] + 1
            ins = dp[i][j - 1][0] + 1
            best = min(sub, dele, ins)
            op = "s" if best == sub else ("d" if best == dele else "i")
            dp[i][j] = (best, op)

    ops: list[tuple[str, str | None, str | None]] = []
    i, j = n, m
    while i > 0 or j > 0:
        op = dp[i][j][1]
        if op in ("=", "s"):
            ops.append((op, ref[i - 1], hyp[j - 1]))
            i, j = i - 1, j - 1
        elif op == "d":
            ops.append((op, ref[i - 1], None))
            i -= 1
        else:
            ops.append((op, None, hyp[j - 1]))
            j -= 1
    ops.reverse()
    return ops


def counts(ops: list[tuple[str, str | None, str | None]]) -> tuple[int, int, int]:
    """Return (substitutions, deletions, insertions) from an alignment."""
    s = sum(1 for o, _, _ in ops if o == "s")
    d = sum(1 for o, _, _ in ops if o == "d")
    i = sum(1 for o, _, _ in ops if o == "i")
    return s, d, i


def score_pair(
    ref_text: str,
    hyp_text: str,
    *,
    normalize_numbers: bool = False,
    normalize_hyphens: bool = False,
) -> dict:
    opts = {
        "normalize_numbers": normalize_numbers,
        "normalize_hyphens": normalize_hyphens,
    }
    ref = tokenize(ref_text, **opts)
    hyp = tokenize(hyp_text, **opts)
    s, d, i = counts(align(ref, hyp))
    errors = s + d + i
    return {
        "ref_words": len(ref),
        "substitutions": s,
        "deletions": d,
        "insertions": i,
        "errors": errors,
        "wer": errors / len(ref) if ref else 0.0,
    }


def diff_tokens(ref_text: str, hyp_text: str) -> list[str]:
    """Human-readable differences, derived from the edit alignment.

    Only the actual edits are listed. A token-count change no longer cascades
    into spurious differences on every following word.
    """
    ops = align(tokenize(ref_text), tokenize(hyp_text))
    out = []
    for op, a, b in ops:
        if op == "s":
            out.append(f"{a} -> {b}")
        elif op == "d":
            out.append(f"{a} -> (deleted)")
        elif op == "i":
            out.append(f"(inserted) -> {b}")
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("directory", type=Path, help="dir of *.ref.txt / *.hyp.txt")
    ap.add_argument("--json", action="store_true", help="emit JSON")
    args = ap.parse_args()

    refs = sorted(args.directory.glob("*.ref.txt"))
    if not refs:
        print(f"no *.ref.txt found in {args.directory}", file=sys.stderr)
        return 1

    results = []
    for ref_path in refs:
        stem = ref_path.name[: -len(".ref.txt")]
        hyp_path = ref_path.with_name(f"{stem}.hyp.txt")
        if not hyp_path.exists():
            print(f"warning: missing {hyp_path.name}", file=sys.stderr)
            continue
        ref_text = ref_path.read_text().strip()
        hyp_text = hyp_path.read_text().strip()
        results.append({
            "id": stem,
            "reference": ref_text,
            "hypothesis": hyp_text,
            "literal": score_pair(ref_text, hyp_text),
            "semantic": score_pair(ref_text, hyp_text, normalize_numbers=True),
            "content": score_pair(ref_text, hyp_text,
                                  normalize_numbers=True, normalize_hyphens=True),
            "differences": diff_tokens(ref_text, hyp_text),
        })

    def aggregate(key: str) -> dict:
        words = sum(r[key]["ref_words"] for r in results)
        errs = sum(r[key]["errors"] for r in results)
        return {
            "ref_words": words,
            "errors": errs,
            "wer": errs / words if words else 0.0,
            "substitutions": sum(r[key]["substitutions"] for r in results),
            "deletions": sum(r[key]["deletions"] for r in results),
            "insertions": sum(r[key]["insertions"] for r in results),
        }

    summary = {
        "utterances": len(results),
        "literal": aggregate("literal"),
        "semantic": aggregate("semantic"),
        "content": aggregate("content"),
    }

    if args.json:
        print(json.dumps({"summary": summary, "results": results}, indent=2))
        return 0

    for r in results:
        mark = "OK  " if not r["differences"] else "DIFF"
        print(f"[{mark}] {r['id']}")
        if r["differences"]:
            print(f"       ref: {r['reference']}")
            print(f"       hyp: {r['hypothesis']}")
            for d in r["differences"]:
                print(f"       - {d}")

    lit, sem, con = summary["literal"], summary["semantic"], summary["content"]
    print()
    print(f"utterances       : {summary['utterances']}")
    print(f"reference words  : {lit['ref_words']}")
    print(f"literal  WER     : {lit['wer']:.2%}  ({lit['errors']} errors)   "
          f"surface comparison")
    print(f"semantic WER     : {sem['wer']:.2%}  ({sem['errors']} errors)   "
          f"+ spelled numbers as digits")
    print(f"content  WER     : {con['wer']:.2%}  ({con['errors']} errors)   "
          f"+ hyphenation ignored")
    print()
    print("content WER is the closest proxy for clinically meaningful error.")
    print("It is NOT a safety measure: a single wrong organism or dose counts")
    print("the same as a stray article. Read the per-utterance diffs.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
