#!/usr/bin/env python3
"""Polish a raw transcript with the local model. Standalone, local-only.

Reads a transcript from a file or stdin, normalizes it deterministically,
sends it to the local polishing model, and prints the structured result.

This command does NOT paste anything anywhere and does not validate protected
tokens -- that is Phase 4. It exists so polishing can be inspected in isolation
before anything depends on it.

Examples:
  scripts/polish_transcript.py note.txt --mode clinical_strict
  echo "..." | scripts/polish_transcript.py --json
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "src"))

from pipeline.normalize import normalization_diff, normalize_transcript  # noqa: E402
from pipeline.polisher import VALID_MODES, LocalOnlyError, LocalPolisher  # noqa: E402


def load_glossary(dictionaries: Path, config: Path, mode_name: str) -> list[str]:
    """Load prompt-eligible terms for a specialty as the polisher glossary.

    Uses the `prompt` file list, not `validation`: the validation lists hold
    over 20,000 terms, which would swamp the model's context to no benefit.
    The glossary tells the polisher which spellings are approved, not which
    words exist.
    """
    sys.path.insert(0, str(REPO_ROOT / "scripts"))
    from build_asr_prompt import load_specialties, load_terms  # noqa: E402

    modes = load_specialties(config)
    if mode_name not in modes:
        return []
    terms: list[str] = []
    seen: set[str] = set()
    for name in modes[mode_name]["prompt"]:
        for t in load_terms(dictionaries / name):
            key = t.lower()
            if key not in seen:
                seen.add(key)
                terms.append(t)
    return terms


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("transcript", nargs="?", type=Path,
                    help="transcript file (default: stdin)")
    ap.add_argument("--mode", default="clinical_strict", choices=sorted(VALID_MODES))
    ap.add_argument("--specialty", default="infectious_diseases",
                    help="glossary source from config/specialties.yaml")
    ap.add_argument("--endpoint", default="http://127.0.0.1:11434")
    ap.add_argument("--model", default="llama3.1:8b")
    ap.add_argument("--timeout", type=int, default=20)
    ap.add_argument("--retries", type=int, default=0)
    ap.add_argument("--json", action="store_true", help="emit JSON")
    ap.add_argument("--verbose", action="store_true")
    args = ap.parse_args()

    logging.basicConfig(
        level=logging.INFO if args.verbose else logging.WARNING,
        format="%(levelname)s %(message)s",
        stream=sys.stderr,
    )

    raw = (args.transcript.read_text(encoding="utf-8") if args.transcript
           else sys.stdin.read())
    if not raw.strip():
        print("error: empty transcript", file=sys.stderr)
        return 2

    normalized = normalize_transcript(raw)
    issues = normalization_diff(raw, normalized)
    if issues:
        # Normalization is meant to be meaning-preserving by construction, so
        # any complaint here is a defect and must not be silently tolerated.
        for i in issues:
            print(f"error: {i}", file=sys.stderr)
        return 4

    try:
        polisher = LocalPolisher(
            endpoint=args.endpoint,
            model=args.model,
            prompt_path=str(REPO_ROOT / "prompts" / "medical_polisher.md"),
            timeout_seconds=args.timeout,
            retries=args.retries,
        )
    except LocalOnlyError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 3

    glossary = load_glossary(
        REPO_ROOT / "dictionaries", REPO_ROOT / "config" / "specialties.yaml",
        args.specialty,
    )
    result = polisher.polish(normalized, glossary, args.mode)

    if args.json:
        print(json.dumps({
            "ok": result.ok,
            "error": result.error,
            "mode": args.mode,
            "raw_text": result.raw_text,
            "polished_text": result.polished_text,
            "flags": [vars(f) for f in result.flags],
            "changed_medical_terms": [vars(c) for c in result.changed_terms],
            "latency_ms": result.latency_ms,
        }, indent=2))
        return 0 if result.ok else 1

    if not result.ok:
        # Fail closed, visibly. The raw transcript is returned unchanged.
        print(f"POLISHING UNAVAILABLE: {result.error}", file=sys.stderr)
        print("Returning raw transcript unchanged.\n", file=sys.stderr)
        print(result.raw_text)
        return 1

    print("RAW:")
    print(f"  {result.raw_text}")
    print("\nPOLISHED:")
    print(f"  {result.polished_text}")
    if result.flags:
        print("\nFLAGS:")
        for f in result.flags:
            sug = f" -> {f.suggestion}" if f.suggestion else ""
            print(f"  [{f.reason}] {f.raw_span!r}{sug}")
    if result.changed_terms:
        print("\nCHANGED MEDICAL TERMS:")
        for c in result.changed_terms:
            print(f"  {c.raw!r} -> {c.polished!r}  ({c.basis})")
    print(f"\nlatency: {result.latency_ms} ms")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
