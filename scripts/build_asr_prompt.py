#!/usr/bin/env python3
"""Build a Whisper initial prompt from curated dictionaries.

Whisper biases decoding toward vocabulary supplied as an initial prompt. The
budget is hard and small: whisper.cpp uses

    max_prompt_ctx = min(n_max_text_ctx, n_text_ctx / 2)

which is 224 tokens for large-v3-turbo (n_text_ctx = 448). Anything beyond
that is silently truncated, so overrunning the budget does not merely waste
space -- it drops terms without telling you.

The prompt is therefore a *selection* problem, not a dump. Terms are taken in
the priority order given by config/specialties.yaml and the order within each
file, so the most valuable terms survive when the budget runs out.

Local-only. No network. Emits no patient information: input is limited to the
curated dictionaries named in the config.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

# whisper.cpp: min(n_max_text_ctx, n_text_ctx/2); n_text_ctx = 448.
WHISPER_PROMPT_TOKEN_LIMIT = 224

# Leave headroom. The estimator below is approximate, and truncation is
# silent, so it is better to under-fill than to have terms quietly vanish.
DEFAULT_BUDGET = 200

_PREAMBLE = {
    "infectious_diseases":
        "This is English medical dictation in infectious diseases and "
        "internal medicine.",
    "general_internal_medicine":
        "This is English medical dictation in internal medicine.",
    "research":
        "This is English dictation in infectious disease epidemiology and "
        "biostatistics.",
    "email":
        "This is English professional correspondence by a physician.",
}

_TRAILER = (
    "Preserve medication names, microorganism names, numbers, units, "
    "and negation."
)


def estimate_tokens(text: str) -> int:
    """Approximate Whisper BPE token count.

    Whisper uses a GPT-2-style byte-pair encoding that is not available here
    without pulling in the model's tokenizer, so this estimates conservatively
    from two signals and takes the larger:

      * words x 1.6 -- medical terms fragment into several tokens each
        ("piperacillin" is not one token), so a naive word count underestimates
        badly on exactly the vocabulary this file exists to carry.
      * characters / 3.2 -- a standard rough BPE ratio.

    Overestimating costs a few unused slots. Underestimating causes silent
    truncation, so the bias is deliberately toward overestimation.
    """
    words = len(text.split())
    return max(int(words * 1.6) + 1, int(len(text) / 3.2) + 1)


def load_terms(path: Path) -> list[str]:
    """Read a dictionary file, preserving order and dropping comments."""
    if not path.exists():
        sys.exit(f"error: dictionary not found: {path}")
    terms = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        terms.append(line)
    return terms


def load_specialties(path: Path) -> dict:
    """Minimal parser for the specialties config.

    Deliberately hand-rolled rather than requiring PyYAML: this runs on a
    machine that must work offline, and the file's structure is fixed and
    simple. Recognises `mode:` blocks each containing `prompt:` and
    `validation:` lists.
    """
    if not path.exists():
        sys.exit(f"error: config not found: {path}")

    modes: dict[str, dict[str, list[str]]] = {}
    current_mode = None
    current_section = None

    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.rstrip()
        if not line.strip() or line.strip().startswith("#"):
            continue
        indent = len(line) - len(line.lstrip())
        stripped = line.strip()

        if indent == 0 and stripped.endswith(":"):
            current_mode = stripped[:-1]
            modes[current_mode] = {"prompt": [], "validation": []}
            current_section = None
        elif stripped.endswith(":") and current_mode:
            key = stripped[:-1]
            current_section = key if key in ("prompt", "validation") else None
        elif stripped.startswith("- ") and current_mode and current_section:
            modes[current_mode][current_section].append(stripped[2:].strip())

    if not modes:
        sys.exit(f"error: no modes parsed from {path}")
    return modes


def build_prompt(terms: list[str], mode: str, budget: int) -> tuple[str, int, int]:
    """Assemble prompt text, adding terms until the budget is reached.

    Returns (prompt, terms_used, estimated_tokens).
    """
    preamble = _PREAMBLE.get(mode, _PREAMBLE["general_internal_medicine"])

    # Fixed overhead must be reserved first, or the trailing instruction --
    # the part carrying the negation/number guidance -- would be what gets cut.
    scaffold = f"{preamble} Possible terminology includes . {_TRAILER}"
    overhead = estimate_tokens(scaffold)
    available = budget - overhead
    if available <= 0:
        sys.exit("error: budget too small for the prompt scaffold")

    chosen: list[str] = []
    used = 0
    for term in terms:
        # ", " between terms.
        cost = estimate_tokens(term) + 1
        if used + cost > available:
            continue  # try shorter later terms rather than stopping outright
        chosen.append(term)
        used += cost

    if not chosen:
        sys.exit("error: no terms fit the budget")

    # Natural prose, not a word dump: CLAUDE.md section 2.3. Whisper's prompt
    # conditions a language model, so fluent text primes it better than a list.
    body = ", ".join(chosen)
    prompt = f"{preamble} Possible terminology includes {body}. {_TRAILER}"
    return prompt, len(chosen), estimate_tokens(prompt)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--mode", default="infectious_diseases",
                    help="specialty mode from config/specialties.yaml")
    ap.add_argument("--config", type=Path, default=Path("config/specialties.yaml"))
    ap.add_argument("--dictionaries", type=Path, default=Path("dictionaries"))
    ap.add_argument("--budget", type=int, default=DEFAULT_BUDGET,
                    help=f"token budget (hard engine limit {WHISPER_PROMPT_TOKEN_LIMIT})")
    ap.add_argument("--out", type=Path, help="write prompt here (default stdout)")
    ap.add_argument("--stats", action="store_true", help="report selection stats")
    args = ap.parse_args()

    if args.budget > WHISPER_PROMPT_TOKEN_LIMIT:
        print(f"warning: budget {args.budget} exceeds the engine limit "
              f"{WHISPER_PROMPT_TOKEN_LIMIT}; terms will be silently dropped "
              f"by whisper.cpp", file=sys.stderr)

    modes = load_specialties(args.config)
    if args.mode not in modes:
        sys.exit(f"error: unknown mode '{args.mode}'. "
                 f"Known: {', '.join(sorted(modes))}")

    files = modes[args.mode]["prompt"]
    if not files:
        sys.exit(f"error: mode '{args.mode}' lists no prompt dictionaries")

    # Preserve priority order across files; deduplicate case-insensitively
    # while keeping the first (highest priority) spelling.
    seen: set[str] = set()
    ordered: list[str] = []
    per_file: list[tuple[str, int]] = []
    for name in files:
        terms = load_terms(args.dictionaries / name)
        added = 0
        for t in terms:
            key = re.sub(r"\s+", " ", t.lower())
            if key in seen:
                continue
            seen.add(key)
            ordered.append(t)
            added += 1
        per_file.append((name, added))

    prompt, used, tokens = build_prompt(ordered, args.mode, args.budget)

    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(prompt + "\n", encoding="utf-8")
        print(f"wrote {args.out}", file=sys.stderr)
    else:
        print(prompt)

    if args.stats:
        print(f"\nmode           : {args.mode}", file=sys.stderr)
        for name, n in per_file:
            print(f"  {name:<28} {n} unique terms", file=sys.stderr)
        print(f"candidate terms: {len(ordered)}", file=sys.stderr)
        print(f"terms in prompt: {used}", file=sys.stderr)
        print(f"est. tokens    : {tokens} / {args.budget} budget "
              f"({WHISPER_PROMPT_TOKEN_LIMIT} engine limit)", file=sys.stderr)
        print(f"characters     : {len(prompt)}", file=sys.stderr)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
