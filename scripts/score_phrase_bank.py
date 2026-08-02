#!/usr/bin/env python3
"""Score a phrase-bank corpus for named-entity recognition, with intervals.

scripts/benchmark.py reports the CLAUDE.md Phase 6 metrics over a corpus. This
reports the one metric that currently cannot be read -- whether a drug or
organism name survived transcription -- broken down the two ways that change
what the answer means:

  by kind      drug names and organism names fail differently
  by prompt    Whisper's initial prompt biases sixteen terms, and
               docs/phase2-results.md found the benefit does not generalize.
               Sixteen of 13,789 medications is not a realistic operating
               point, so the out-of-prompt figure is the one to plan around.

Every rate is printed with a 95% Wilson interval, because the number this
replaces (62.5%, five of eight) was acted on for weeks without anyone noticing
it spanned 31% to 86%. A rate without an interval invites exactly that.

Scores whatever has been transcribed so far, so a corpus can be checked
part-way through recording.

Local-only. No network, no model calls.

Usage:
  scripts/score_phrase_bank.py runtime/tmp/bank
  scripts/score_phrase_bank.py runtime/tmp/bank --json
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import unicodedata
from math import sqrt
from pathlib import Path


def norm(text: str) -> str:
    """Fold to the form used for comparison.

    Matches scripts/benchmark.py's normalisation so the two agree on what
    counts as the same word, then collapses whitespace so a multi-word term
    can be matched as a substring.
    """
    text = unicodedata.normalize("NFKC", text).lower()
    text = re.sub(r"[^a-z0-9\- ]", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def wilson(k: int, n: int, z: float = 1.96) -> tuple[float, float]:
    if n == 0:
        return (0.0, 0.0)
    p = k / n
    d = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / d
    half = z * sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return (max(0.0, centre - half) * 100, min(1.0, centre + half) * 100)


def score(manifest: dict, corpus: Path) -> tuple[list[dict], int, int]:
    """Check each target term against its hypothesis.

    A term counts as recognised when its normalised form appears in the
    normalised hypothesis. Substring rather than token matching, because the
    terms are multi-word ("Staphylococcus aureus") and hyphenation is exactly
    what the ASR gets wrong ("piperacillin tazobactam").
    """
    results: list[dict] = []
    scored = missing = 0

    for phrase in manifest["phrases"]:
        hyp_path = corpus / f"{phrase['id']}.hyp.txt"
        if not hyp_path.exists():
            missing += 1
            continue
        scored += 1
        hyp = norm(hyp_path.read_text(encoding="utf-8"))

        for target in phrase["targets"]:
            results.append({
                "id": phrase["id"],
                "term": target["term"],
                "kind": target["kind"],
                "section": target["section"],
                "in_prompt": target["in_prompt"],
                "correct": norm(target["term"]) in hyp,
                "heard": hyp,
            })

    return results, scored, missing


def rate_line(label: str, rows: list[dict]) -> str:
    n = len(rows)
    if n == 0:
        return f"  {label:<26}      --  (no observations)"
    k = sum(1 for r in rows if r["correct"])
    lo, hi = wilson(k, n)
    return (f"  {label:<26} {k:>4}/{n:<4} {k / n * 100:6.1f} %   "
            f"[{lo:5.1f}, {hi:5.1f}]  +/-{(hi - lo) / 2:.0f}")


def report(results: list[dict], scored: int, missing: int) -> None:
    print(f"\n{scored} phrase(s) scored"
          + (f", {missing} not yet transcribed" if missing else ""))

    if not results:
        print("\nNothing to score. Record and transcribe first:")
        print("  scripts/record_baseline.sh <bank>/phrases.txt <bank>")
        print("  scripts/transcribe_all.sh <bank>\n")
        return

    print(f"\nnamed-entity accuracy        correct    rate      95% CI")
    print("  " + "-" * 62)
    for kind in ("drug", "organism"):
        rows = [r for r in results if r["kind"] == kind]
        if not rows:
            continue
        print(rate_line(f"{kind} names, all", rows))
        print(rate_line(f"  in Whisper's prompt",
                        [r for r in rows if r["in_prompt"]]))
        print(rate_line(f"  NOT in the prompt",
                        [r for r in rows if not r["in_prompt"]]))
    print("  " + "-" * 62)
    print(rate_line("all named entities", results))

    print("\n  The out-of-prompt rows are the realistic operating point:")
    print("  sixteen terms are biased, and the dictionaries hold 13,789.")

    # By section, so a failure can be attributed to a class of term rather
    # than left as a single aggregate nobody can act on.
    print("\nby term class")
    sections = sorted({r["section"] for r in results})
    for s in sections:
        print(rate_line(f"  {s}", [r for r in results if r["section"] == s]))

    misses = [r for r in results if not r["correct"]]
    if misses:
        print(f"\nmissed terms ({len(misses)}) -- the actionable part")
        for r in sorted(misses, key=lambda r: (r["kind"], r["term"])):
            flag = "  [prompted]" if r["in_prompt"] else ""
            print(f"  {r['kind']:<9} {r['term']:<34}{flag}")
            print(f"            heard: {r['heard'][:88]}")
    else:
        print("\nNo missed terms.")
    print()


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("corpus", type=Path,
                    help="directory holding manifest.json and phrase_*.hyp.txt")
    ap.add_argument("--manifest", type=Path,
                    help="manifest location (default <corpus>/manifest.json)")
    ap.add_argument("--json", action="store_true",
                    help="emit machine-readable results")
    args = ap.parse_args()

    manifest_path = args.manifest or args.corpus / "manifest.json"
    if not manifest_path.exists():
        sys.exit(f"error: no manifest at {manifest_path}\n"
                 "This scorer needs the manifest written by "
                 "scripts/build_phrase_bank.py; it records which terms each\n"
                 "phrase was built to exercise, which is what makes the result "
                 "attributable.")

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    results, scored, missing = score(manifest, args.corpus)

    if args.json:
        payload: dict = {"scored": scored, "not_transcribed": missing,
                         "observations": results, "rates": {}}
        for kind in ("drug", "organism"):
            for label, sel in (("all", lambda r: True),
                               ("in_prompt", lambda r: r["in_prompt"]),
                               ("out_of_prompt", lambda r: not r["in_prompt"])):
                rows = [r for r in results if r["kind"] == kind and sel(r)]
                k, n = sum(1 for r in rows if r["correct"]), len(rows)
                lo, hi = wilson(k, n)
                payload["rates"][f"{kind}_{label}"] = {
                    "correct": k, "total": n,
                    "rate": (k / n * 100) if n else None,
                    "ci95": [lo, hi],
                }
        print(json.dumps(payload, indent=2))
        return 0

    report(results, scored, missing)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
