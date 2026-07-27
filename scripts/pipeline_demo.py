#!/usr/bin/env python3
"""End-to-end demo: raw transcript -> normalize -> polish -> validate.

Shows what the full pipeline would do with a transcript, including the
review interface for anything that is not safe to paste. Nothing is pasted:
that is Phase 5, and it must not happen until a polishing model is approved.

Local-only. No network beyond loopback.

Examples:
  scripts/pipeline_demo.py --text "there is no evidence of endocarditis"
  scripts/pipeline_demo.py transcript.txt --model qwen2.5:14b
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "src"))

from pipeline.normalize import normalization_diff, normalize_transcript  # noqa: E402
from pipeline.polisher import LocalOnlyError, LocalPolisher  # noqa: E402
from validation.validator import Status, Validator  # noqa: E402

_BANNER = {
    Status.SAFE: "SAFE TO PASTE",
    Status.REVIEW: "REVIEW REQUIRED",
    Status.REJECTED: "REJECTED",
}


def show_diff(raw: str, polished: str) -> None:
    """Word-level diff so a reviewer can see what actually changed."""
    import difflib

    r, p = raw.split(), polished.split()
    out = []
    for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(None, r, p).get_opcodes():
        if tag == "equal":
            out.extend(r[i1:i2])
        elif tag == "delete":
            out.append(f"[-{' '.join(r[i1:i2])}-]")
        elif tag == "insert":
            out.append(f"[+{' '.join(p[j1:j2])}+]")
        else:
            out.append(f"[-{' '.join(r[i1:i2])}-][+{' '.join(p[j1:j2])}+]")
    print("  " + " ".join(out))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("transcript", nargs="?", type=Path)
    ap.add_argument("--text")
    ap.add_argument("--model", default="qwen2.5:14b")
    ap.add_argument("--endpoint", default="http://127.0.0.1:11434")
    ap.add_argument("--mode", default="clinical_strict")
    ap.add_argument("--timeout", type=int, default=120)
    args = ap.parse_args()

    raw = args.text if args.text else (
        args.transcript.read_text(encoding="utf-8") if args.transcript
        else sys.stdin.read())
    if not raw.strip():
        print("error: empty transcript", file=sys.stderr)
        return 3

    print("=" * 68)
    print("1. RAW TRANSCRIPT")
    print("=" * 68)
    print(f"  {raw.strip()}")

    normalized = normalize_transcript(raw)
    issues = normalization_diff(raw, normalized)
    print("\n" + "=" * 68)
    print("2. DETERMINISTIC NORMALIZATION")
    print("=" * 68)
    if issues:
        for i in issues:
            print(f"  ERROR: {i}")
        return 4
    print(f"  {normalized}")
    if normalized != raw.strip():
        print("  (whitespace/typography only)")

    print("\n" + "=" * 68)
    print("3. LOCAL POLISHING")
    print("=" * 68)
    try:
        polisher = LocalPolisher(
            endpoint=args.endpoint, model=args.model,
            prompt_path=str(REPO_ROOT / "prompts" / "medical_polisher.md"),
            timeout_seconds=args.timeout, retries=0,
        )
    except LocalOnlyError as exc:
        print(f"  REFUSED: {exc}")
        return 3

    # send_glossary defaults false: measured to worsen both models tested.
    result = polisher.polish(normalized, [], args.mode)
    if not result.ok:
        print(f"  UNAVAILABLE: {result.error}")
        print("  Falling back to the raw transcript (fail closed).")
        print(f"\n  USE THIS: {normalized}")
        return 1

    print(f"  {result.polished_text}")
    print(f"  ({result.latency_ms} ms, {len(result.flags)} flags)")
    for f in result.flags:
        print(f"    [{f.reason}] {f.raw_span!r}")

    print("\n" + "=" * 68)
    print("4. PROTECTED-TOKEN VALIDATION")
    print("=" * 68)
    validator = Validator.from_dictionaries(REPO_ROOT / "dictionaries", args.mode)
    v = validator.validate(normalized, result.polished_text)

    print(f"  STATUS: {_BANNER[v.status]}")
    if normalized != result.polished_text:
        print("\n  CHANGES:")
        show_diff(normalized, result.polished_text)
    for c in v.protected_changes:
        print(f"    ! [{c.category}] {c.detail}")
    for w in v.warnings:
        print(f"    ~ {w}")

    print("\n" + "=" * 68)
    print("5. OUTCOME")
    print("=" * 68)
    if v.status is Status.SAFE:
        print("  Polished text would be eligible to paste.")
        print(f"\n  {result.polished_text}")
    elif v.status is Status.REVIEW:
        print("  Auto-paste BLOCKED. Human review required.")
        print(f"\n  raw      : {normalized}")
        print(f"  polished : {result.polished_text}")
        print("\n  Choose the raw text, edit it yourself, or cancel.")
    else:
        print("  Polished output REJECTED. Use the raw transcript.")
        print(f"\n  {normalized}")

    print("\n  NOTE: nothing is pasted. Phase 5 is not implemented, and paste")
    print("  must stay disabled until a polishing model is approved.")
    return {Status.SAFE: 0, Status.REVIEW: 1, Status.REJECTED: 2}[v.status]


if __name__ == "__main__":
    raise SystemExit(main())
