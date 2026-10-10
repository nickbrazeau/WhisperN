#!/usr/bin/env python3
"""Evaluate rewrite mode end to end: audio -> Whisper -> rewrite -> checks.

scripts/eval_polisher.py --rewrite feeds the rewriter clean typed text. Real
use feeds it a Whisper transcript, with Whisper's punctuation, numerals, and
errors. This closes that gap for each case in rewrite_cases.yaml:

  1. speak `raw` with the offline macOS synthesiser (`say`)
  2. transcribe it with the app's own pipeline (VAD gate, initial prompt)
  3. rewrite the transcript with the configured local model
  4. score the rewrite with the same checks as eval_polisher.py

Each failure is attributed: if the transcript already lacks a must_keep term,
the ASR lost it ("asr"); if the transcript has it and the rewrite does not,
the rewriter dropped it ("rewrite").

Synthetic voice is cleaner than a person (no accent, room, or disfluent
prosody), so this is an optimistic bound. For the user's own voice, pass
--audio-dir with recordings named <case id>.wav; they are read, never copied,
and never deleted by this script.

Audio this script synthesises lives in runtime/tmp and is deleted after
transcription (CLAUDE.md §3.1). Nothing is written to disk except an optional
--json report, which contains synthetic text only when run on synthetic audio.

Usage:
  scripts/eval_rewrite_audio.py
  scripts/eval_rewrite_audio.py --audio-dir ~/whispern-voice-cases
  scripts/eval_rewrite_audio.py --typed      # same pipeline, no ASR
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "src"))
sys.path.insert(0, str(REPO_ROOT / "scripts"))

from dictate import load_config  # noqa: E402
from eval_polisher import check, load_cases  # noqa: E402
from pipeline.dictation import DictationPipeline  # noqa: E402
from pipeline.polisher import LocalPolisher  # noqa: E402
from validation.validator import Validator  # noqa: E402


def synthesise(text: str, out: Path, voice: str) -> None:
    aiff = out.with_suffix(".aiff")
    try:
        subprocess.run(["say", "-v", voice, "-o", str(aiff), text], check=True)
        subprocess.run(["afconvert", "-f", "WAVE", "-d", "LEI16@16000", "-c", "1",
                        str(aiff), str(out)], check=True, capture_output=True)
    finally:
        aiff.unlink(missing_ok=True)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--cases", type=Path,
                    default=REPO_ROOT / "tests/fixtures/synthetic/rewrite_cases.yaml")
    ap.add_argument("--voice", default="Samantha")
    ap.add_argument("--audio-dir", type=Path,
                    help="use recordings <case id>.wav from here instead of `say`")
    ap.add_argument("--typed", action="store_true",
                    help="skip audio: feed the case text straight to the rewrite "
                         "pipeline (includes the repair pass, unlike eval_polisher.py)")
    ap.add_argument("--json", type=Path, help="write a report here")
    args = ap.parse_args()

    cfg = load_config(REPO_ROOT / "config/app.yaml")
    rewriter = LocalPolisher(
        endpoint=cfg.get("rewrite.endpoint", "http://127.0.0.1:11434"),
        model=cfg.get("rewrite.model", ""),
        prompt_path=str(REPO_ROOT / "prompts/eloquent_rewriter.md"),
        timeout_seconds=int(cfg.get("rewrite.timeout_seconds", "90")),
        keep_alive=cfg.get("rewrite.keep_alive", "30m"),
        num_ctx=int(cfg.get("rewrite.num_ctx", "4096")),
    )
    pipe = DictationPipeline(
        repo_root=REPO_ROOT,
        prompt_file=REPO_ROOT / "runtime/tmp/asr_prompt_id.txt",
        validator=Validator.from_dictionaries(REPO_ROOT / "dictionaries", "medical"),
        mode="medical",
        rewriter=rewriter,
    )

    tmp = REPO_ROOT / "runtime/tmp"
    tmp.mkdir(parents=True, exist_ok=True)
    report, passed = [], 0
    cases = load_cases(args.cases)

    for case in cases:
        if args.typed:
            asr = case["raw"]
        elif args.audio_dir:
            wav = args.audio_dir / f"{case['id']}.wav"
            if not wav.exists():
                print(f"SKIP  {case['id']}  (no {wav.name})")
                continue
            asr, _ = pipe.transcribe(wav)
        else:
            fd, name = tempfile.mkstemp(suffix=".wav", prefix="evalrw.", dir=tmp)
            Path(name).unlink()
            wav = Path(name)
            try:
                synthesise(case["raw"], wav, args.voice)
                asr, _ = pipe.transcribe(wav)
            finally:
                wav.unlink(missing_ok=True)

        res = pipe.rewrite_text(asr)
        rewrite = res.polished_text if res.rewritten else ""
        # Score against the transcript, which is what the rewriter actually saw.
        seen = dict(case, raw=asr)
        asr_lost = [v for v in check(seen, asr) if v.startswith("DROPPED")]
        problems = check(seen, rewrite) if rewrite else ["NO REWRITE: " + "; ".join(res.warnings[:1])]
        blamed = [f"{p} [{'asr' if p in asr_lost else 'rewrite'}]" for p in problems]
        ok = not problems
        passed += ok
        print(f"{'PASS' if ok else 'FAIL'}  {case['id']}  {res.polish_ms} ms")
        print(f"    heard    : {asr}")
        print(f"    rewrite  : {rewrite}")
        for b in blamed:
            print(f"    !! {b}")
        for c in res.protected_changes:
            print(f"    reader sees: {c.detail}")
        for w in res.warnings:
            if w.startswith("restored"):
                print(f"    repair   : {w}")
        report.append({"id": case["id"], "pass": ok, "asr": asr, "rewrite": rewrite,
                       "problems": blamed, "latency_ms": res.polish_ms})

    print(f"\n{passed}/{len(report)} passed")
    if args.json:
        args.json.write_text(json.dumps(report, indent=2), encoding="utf-8")
    return 0 if passed == len(report) else 1


if __name__ == "__main__":
    raise SystemExit(main())
