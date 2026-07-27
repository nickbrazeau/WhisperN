#!/usr/bin/env python3
"""Validate polished text against a raw transcript.

Deterministic. No model, no network. Returns the structured result defined in
CLAUDE.md §4.2 and exits non-zero when the result is not safe to paste, so it
can gate an automated pipeline.

Exit codes:
  0  safe_to_paste
  1  review_required
  2  rejected
  3  usage error

Examples:
  scripts/validate_changes.py --raw raw.txt --polished polished.txt
  scripts/validate_changes.py --raw-text "..." --polished-text "..." --json
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "src"))

from validation.validator import Status, Validator  # noqa: E402

_EXIT = {Status.SAFE: 0, Status.REVIEW: 1, Status.REJECTED: 2}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--raw", type=Path)
    ap.add_argument("--polished", type=Path)
    ap.add_argument("--raw-text")
    ap.add_argument("--polished-text")
    ap.add_argument("--mode", default="clinical_strict")
    ap.add_argument("--dictionaries", type=Path, default=REPO_ROOT / "dictionaries")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    raw = args.raw_text if args.raw_text is not None else (
        args.raw.read_text(encoding="utf-8") if args.raw else None)
    pol = args.polished_text if args.polished_text is not None else (
        args.polished.read_text(encoding="utf-8") if args.polished else None)

    if raw is None or pol is None:
        print("error: supply --raw/--polished or --raw-text/--polished-text",
              file=sys.stderr)
        return 3

    validator = Validator.from_dictionaries(args.dictionaries, args.mode)
    result = validator.validate(raw.strip(), pol.strip())

    if args.json:
        print(json.dumps(result.to_dict(), indent=2))
        return _EXIT[result.status]

    banner = {
        Status.SAFE: "SAFE TO PASTE",
        Status.REVIEW: "REVIEW REQUIRED",
        Status.REJECTED: "REJECTED",
    }[result.status]
    print(f"=== {banner} ===\n")
    print(f"RAW      : {result.raw_text}")
    print(f"POLISHED : {result.polished_text}")

    if result.protected_changes:
        print("\nPROTECTED CHANGES:")
        for c in result.protected_changes:
            print(f"  [{c.category}] {c.detail}")
    if result.warnings:
        print("\nWARNINGS:")
        for w in result.warnings:
            print(f"  - {w}")

    if result.status is Status.REVIEW:
        print("\nAuto-paste is blocked in clinical_strict mode.")
        print("Choose the raw text, edit it yourself, or cancel.")
    elif result.status is Status.REJECTED:
        print("\nOutput rejected. Use the raw transcript.")

    return _EXIT[result.status]


if __name__ == "__main__":
    raise SystemExit(main())
