#!/usr/bin/env python3
"""Evaluate a local polishing model against the adversarial case set.

Answers one question: does this model obey the constraints in CLAUDE.md §3.2
well enough to be an approved polisher?

A case fails if the polished text drops something in `must_keep` or introduces
something in `must_not_add`. Both checks are substring-based and deliberately
crude -- they under-detect rather than over-detect, so a reported violation is
a real one.

Per CLAUDE.md §9, cases are never relaxed to let a model pass. A model that
fails is not approved.

With --rewrite it evaluates the email/research rewriter instead (ADR 0002):
prompts/eloquent_rewriter.md against rewrite_cases.yaml, and additionally
reports what validate_rewrite would show the reader, and how much shorter the
rewrite is.

Usage:
  scripts/eval_polisher.py --model llama3.1:8b
  scripts/eval_polisher.py --model qwen2.5:14b --json
  scripts/eval_polisher.py --rewrite --model qwen2.5:14b
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "src"))

from pipeline.polisher import LocalPolisher  # noqa: E402


def load_cases(path: Path) -> list[dict]:
    """Minimal parser for the case file.

    Hand-rolled to avoid a PyYAML dependency on a machine that must run
    offline. Handles the fixed shape of polisher_cases.yaml only.
    """
    text = path.read_text(encoding="utf-8")
    cases: list[dict] = []
    current: dict | None = None
    list_key: str | None = None

    for raw_line in text.splitlines():
        if not raw_line.strip() or raw_line.lstrip().startswith("#"):
            continue
        stripped = raw_line.strip()

        if stripped.startswith("- id:"):
            if current:
                cases.append(current)
            current = {"id": stripped.split(":", 1)[1].strip(),
                       "must_keep": [], "must_not_add": [], "must_drop": []}
            list_key = None
            continue
        if current is None:
            continue

        m = re.match(r"^(\w+):\s*(.*)$", stripped)
        if m:
            key, val = m.group(1), m.group(2).strip()
            if key in ("must_keep", "must_not_add", "must_drop"):
                if val.startswith("["):
                    items = [i.strip().strip('"\'')
                             for i in val.strip("[]").split(",") if i.strip()]
                    current[key] = items
                    list_key = None
                else:
                    current[key] = []
                    list_key = key
            elif key in ("raw", "category", "note"):
                if val.startswith(">"):
                    list_key = None
                    current[key] = ""
                else:
                    current[key] = val.strip('"\'')
                    list_key = None
            continue

        if stripped.startswith("- ") and list_key:
            current[list_key].append(stripped[2:].strip().strip('"\''))

    if current:
        cases.append(current)
    return [c for c in cases if c.get("raw")]


def check(case: dict, polished: str) -> list[str]:
    """Return violation descriptions for one case."""
    p = polished.lower()
    raw = case["raw"].lower()
    violations = []

    for needle in case.get("must_keep", []):
        if needle.lower() not in p:
            violations.append(f"DROPPED {needle!r}")

    for needle in case.get("must_not_add", []):
        n = needle.lower()
        # Only a violation if the model introduced it.
        if n in p and n not in raw:
            violations.append(f"ADDED {needle!r}")

    # Retracted by the speaker, so present in raw but must not survive.
    for needle in case.get("must_drop", []):
        if needle.lower() in p:
            violations.append(f"KEPT RETRACTED {needle!r}")

    return violations


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--model", default="llama3.1:8b")
    ap.add_argument("--endpoint", default="http://127.0.0.1:11434")
    ap.add_argument("--mode", default="clinical_strict")
    ap.add_argument("--timeout", type=int, default=120)
    ap.add_argument("--cases", type=Path,
                    default=REPO_ROOT / "tests/fixtures/synthetic/polisher_cases.yaml")
    ap.add_argument("--glossary", metavar="SPECIALTY",
                    help="supply the glossary for this specialty (default: none). "
                         "Worth testing both ways: a glossary can induce the "
                         "model to 'correct' text toward nearby approved terms.")
    ap.add_argument("--rewrite", action="store_true",
                    help="evaluate the email/research rewriter (ADR 0002)")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    validator = None
    if args.rewrite:
        args.mode = "rewrite"
        if args.cases.name == "polisher_cases.yaml":
            args.cases = REPO_ROOT / "tests/fixtures/synthetic/rewrite_cases.yaml"
        from validation.validator import Validator
        validator = Validator.from_dictionaries(REPO_ROOT / "dictionaries")

    glossary: list[str] = []
    if args.glossary:
        sys.path.insert(0, str(REPO_ROOT / "scripts"))
        from build_asr_prompt import load_specialties, load_terms
        modes = load_specialties(REPO_ROOT / "config" / "specialties.yaml")
        for name in modes.get(args.glossary, {}).get("prompt", []):
            glossary.extend(load_terms(REPO_ROOT / "dictionaries" / name))

    cases = load_cases(args.cases)
    if not cases:
        print(f"error: no cases parsed from {args.cases}", file=sys.stderr)
        return 2

    polisher = LocalPolisher(
        endpoint=args.endpoint, model=args.model,
        prompt_path=str(REPO_ROOT / "prompts" /
                        ("eloquent_rewriter.md" if args.rewrite else "medical_polisher.md")),
        timeout_seconds=args.timeout, retries=0,
    )

    results = []
    n_pass = n_fail = n_error = 0
    total_flags = 0

    for case in cases:
        res = polisher.polish(case["raw"], glossary, args.mode)
        if not res.ok:
            n_error += 1
            results.append({"id": case["id"], "status": "error",
                            "error": res.error, "violations": []})
            print(f"[ERROR] {case['id']}: {res.error}")
            continue

        total_flags += len(res.flags)
        violations = check(case, res.polished_text)
        status = "pass" if not violations else "fail"
        if violations:
            n_fail += 1
        else:
            n_pass += 1

        results.append({
            "id": case["id"], "category": case.get("category", ""),
            "status": status, "raw": case["raw"],
            "polished": res.polished_text, "violations": violations,
            "flags": len(res.flags), "latency_ms": res.latency_ms,
        })

        mark = "PASS" if status == "pass" else "FAIL"
        ratio = len(res.polished_text.split()) / max(1, len(case["raw"].split()))
        results[-1]["word_ratio"] = round(ratio, 2)
        extra = f"  words x{ratio:.2f}  {res.latency_ms} ms" if args.rewrite else ""
        print(f"[{mark}] {case['id']}  ({case.get('category','')}){extra}")
        if validator is not None:
            v = validator.validate_rewrite(case["raw"], res.polished_text)
            results[-1]["validator"] = [c.detail for c in v.protected_changes] + v.warnings
            print(f"    rewrite  : {res.polished_text}")
            for c in v.protected_changes:
                print(f"    !  {c.detail}")
            for w in v.warnings:
                print(f"    ~  {w}")
        if violations:
            print(f"    raw      : {case['raw']}")
            print(f"    polished : {res.polished_text}")
            for v in violations:
                print(f"    -> {v}")

    total = n_pass + n_fail
    print()
    print(f"model      : {args.model}   mode: {args.mode}   glossary: {args.glossary or 'none'} ({len(glossary)} terms)")
    print(f"cases      : {len(cases)}  ({n_error} errored)")
    if total:
        print(f"passed     : {n_pass}/{total} = {n_pass/total:.1%}")
        print(f"FAILED     : {n_fail}/{total} = {n_fail/total:.1%}")
    print(f"flags emitted by model: {total_flags}")
    if total_flags == 0 and total:
        print("  NOTE: the model never used the flag mechanism. Rule 10 asks it to")
        print("  flag uncertain spans rather than guess; silence here means edits")
        print("  arrive with no indication of doubt.")

    if args.json:
        Path("runtime/tmp").mkdir(parents=True, exist_ok=True)
        out = Path("runtime/tmp") / f"polisher_eval_{args.model.replace(':','_')}.json"
        out.write_text(json.dumps(results, indent=2))
        print(f"\nwrote {out}")

    # Non-zero exit when any case fails: this gates model approval.
    return 0 if n_fail == 0 and n_error == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
