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


def tokenize(text: str, *, normalize_numbers: bool = False) -> list[str]:
    """Lowercase, strip punctuation, split on whitespace."""
    text = unicodedata.normalize("NFKC", text).lower()
    # Keep intra-word hyphens (methicillin-susceptible) and decimal points (0.9).
    text = re.sub(r"[^\w\s.\-]", " ", text)
    text = re.sub(r"(?<!\d)\.(?!\d)", " ", text)  # sentence periods, not decimals
    tokens = text.split()
    tokens = [t.strip("-") for t in tokens if t.strip("-")]
    if normalize_numbers:
        tokens = [_NUMBER_WORDS.get(t, t) for t in tokens]
    return tokens


def levenshtein(ref: list[str], hyp: list[str]) -> tuple[int, int, int]:
    """Return (substitutions, deletions, insertions) via edit-distance backtrace."""
    n, m = len(ref), len(hyp)
    # dp[i][j] = (cost, op) where op is the edit taken to reach this cell.
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

    s = d = ins_count = 0
    i, j = n, m
    while i > 0 or j > 0:
        op = dp[i][j][1]
        if op == "=":
            i, j = i - 1, j - 1
        elif op == "s":
            s += 1
            i, j = i - 1, j - 1
        elif op == "d":
            d += 1
            i -= 1
        else:
            ins_count += 1
            j -= 1
    return s, d, ins_count


def score_pair(ref_text: str, hyp_text: str, *, normalize_numbers: bool) -> dict:
    ref = tokenize(ref_text, normalize_numbers=normalize_numbers)
    hyp = tokenize(hyp_text, normalize_numbers=normalize_numbers)
    s, d, i = levenshtein(ref, hyp)
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
    """Human-readable list of differing tokens, for the error report."""
    ref = tokenize(ref_text)
    hyp = tokenize(hyp_text)
    out = []
    for a, b in zip(ref, hyp):
        if a != b:
            out.append(f"{a} -> {b}")
    if len(ref) != len(hyp):
        out.append(f"(length {len(ref)} -> {len(hyp)})")
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
            "literal": score_pair(ref_text, hyp_text, normalize_numbers=False),
            "semantic": score_pair(ref_text, hyp_text, normalize_numbers=True),
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

    lit, sem = summary["literal"], summary["semantic"]
    print()
    print(f"utterances       : {summary['utterances']}")
    print(f"reference words  : {lit['ref_words']}")
    print(f"literal  WER     : {lit['wer']:.2%}  ({lit['errors']} errors)")
    print(f"semantic WER     : {sem['wer']:.2%}  ({sem['errors']} errors)  "
          f"[spelled numbers mapped to digits]")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
