#!/usr/bin/env python3
"""Record, transcribe, and review a dictation.

Implements the CLAUDE.md §6 Phase 5 tasks that do not require modifying an
upstream application: the post-transcription hook, structured validation, the
raw/polished review interface, temporary-audio deletion, and a visible mode
indicator.

PASTING IS NOT IMPLEMENTED AND IS NOT ENABLED. CLAUDE.md §11 says not to skip
to automatic pasting, and §6 Phase 8 requires the full deployment checklist
first. This command prints the result and, with --copy, can place it on the
clipboard as an explicit user action.

Usage:
  scripts/dictate.py                    # record until Enter, then process
  scripts/dictate.py --file audio.wav   # process an existing file
  scripts/dictate.py --copy             # also copy the result to the clipboard
"""

from __future__ import annotations

import argparse
import subprocess
import sys
import tempfile
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "src"))

from pipeline.dictation import DictationPipeline, Outcome  # noqa: E402
from pipeline.polisher import LocalOnlyError, LocalPolisher  # noqa: E402
from validation.validator import Validator  # noqa: E402

BOLD, DIM, RED, YEL, GRN, RST = (
    "\033[1m", "\033[2m", "\033[31m", "\033[33m", "\033[32m", "\033[0m"
)


def load_config(path: Path) -> dict:
    """Read the handful of app.yaml keys this command needs.

    Deliberately not a YAML parser: the file's shape is fixed, and adding a
    dependency to a tool that must run offline is not worth it.
    """
    cfg: dict[str, str] = {}
    if not path.exists():
        return cfg
    section = ""
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip() or line.strip().startswith("#"):
            continue
        if not line.startswith(" ") and line.rstrip().endswith(":"):
            section = line.strip()[:-1]
            continue
        if ":" in line:
            k, _, v = line.strip().partition(":")
            v = v.split("#")[0].strip()
            if v:
                cfg[f"{section}.{k.strip()}"] = v
    return cfg


def record_to(path: Path, device: str) -> bool:
    """Record until the user presses Enter. Returns False if nothing captured."""
    try:
        tty = open("/dev/tty")
    except OSError:
        print("error: no controlling terminal; use --file", file=sys.stderr)
        return False

    proc = subprocess.Popen(
        ["ffmpeg", "-nostdin", "-loglevel", "error", "-f", "avfoundation",
         "-i", f":{device}", "-ar", "16000", "-ac", "1", "-c:a", "pcm_s16le",
         "-y", str(path)],
        stderr=subprocess.PIPE,
    )
    time.sleep(0.5)
    if proc.poll() is not None:
        err = (proc.stderr.read() or b"").decode(errors="replace")
        print(f"error: recording failed to start:\n{err}", file=sys.stderr)
        return False

    print(f"  {RED}● RECORDING{RST}  — press Enter to stop.")
    tty.readline()
    proc.send_signal(subprocess.signal.SIGINT)
    proc.wait(timeout=10)
    tty.close()
    return path.exists() and path.stat().st_size > 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--file", type=Path, help="process an existing audio file")
    ap.add_argument("--device", default="0", help="avfoundation input index")
    ap.add_argument("--mode", help="override polishing mode")
    ap.add_argument("--copy", action="store_true",
                    help="copy the result to the clipboard (explicit user action)")
    ap.add_argument("--no-vad", action="store_true",
                    help="disable the voice-activity gate (not recommended)")
    ap.add_argument("--json", action="store_true",
                    help="emit a machine-readable result on stdout (for the "
                         "Hammerspoon shell); suppresses the human output")
    ap.add_argument("--config", type=Path, default=REPO_ROOT / "config/app.yaml")
    args = ap.parse_args()

    cfg = load_config(args.config)
    mode = args.mode or cfg.get("polishing.mode", "clinical_strict")
    polishing_on = cfg.get("polishing.enabled", "false").lower() == "true"
    model = cfg.get("polishing.model", "")
    send_glossary = cfg.get("polishing.send_glossary", "false").lower() == "true"
    use_vad = not args.no_vad

    # Visible mode indicator (CLAUDE.md §6 Phase 5.10). The user must be able
    # to tell at a glance whether text is being rewritten. In --json mode the
    # caller renders its own indicator; see hammerspoon/init.lua.
    quiet = args.json
    if not quiet:
        print(f"\n{BOLD}WhisperN{RST}  {DIM}local dictation{RST}")
    if not quiet:
        print(f"  mode      : {BOLD}{mode}{RST}")
    if not quiet:
        if polishing_on and model and not model.startswith("REPLACE"):
            print(f"  polishing : {YEL}ON{RST} ({model})")
        else:
            print(f"  polishing : {GRN}OFF{RST}  {DIM}raw transcript only{RST}")
    if not quiet:
        print(f"  VAD gate  : {GRN}ON{RST}" if use_vad else f"  VAD gate  : {RED}OFF{RST}")
    if not quiet:
        print(f"  paste     : {GRN}DISABLED{RST}  {DIM}(review only){RST}\n")

    polisher = None
    if polishing_on and model and not model.startswith("REPLACE"):
        try:
            polisher = LocalPolisher(
                endpoint=cfg.get("polishing.endpoint", "http://127.0.0.1:11434"),
                model=model,
                prompt_path=str(REPO_ROOT / "prompts/medical_polisher.md"),
                timeout_seconds=int(cfg.get("polishing.timeout_seconds", "20")),
                retries=int(cfg.get("polishing.retries", "0")),
            )
        except LocalOnlyError as exc:
            print(f"{RED}error: {exc}{RST}", file=sys.stderr)
            return 3

    pipeline = DictationPipeline(
        repo_root=REPO_ROOT,
        prompt_file=REPO_ROOT / "runtime/tmp/asr_prompt_id.txt",
        polisher=polisher,
        validator=Validator.from_dictionaries(REPO_ROOT / "dictionaries", mode),
        mode=mode,
        use_vad=use_vad,
    )

    tmp_audio: Path | None = None
    if args.file:
        audio = args.file
        if not audio.exists():
            print(f"error: no such file: {audio}", file=sys.stderr)
            return 2
    else:
        (REPO_ROOT / "runtime/tmp").mkdir(parents=True, exist_ok=True)
        fd, name = tempfile.mkstemp(suffix=".wav", prefix="rec.",
                                    dir=REPO_ROOT / "runtime/tmp")
        import os
        os.close(fd)
        tmp_audio = audio = Path(name)
        if not record_to(audio, args.device):
            audio.unlink(missing_ok=True)
            return 3

    try:
        glossary: list[str] = []
        if send_glossary:
            sys.path.insert(0, str(REPO_ROOT / "scripts"))
            from build_asr_prompt import load_specialties, load_terms
            modes = load_specialties(REPO_ROOT / "config/specialties.yaml")
            for n in modes.get(cfg.get("transcription.specialty",
                                       "infectious_diseases"), {}).get("prompt", []):
                glossary.extend(load_terms(REPO_ROOT / "dictionaries" / n))

        result = pipeline.run(audio, glossary)
    finally:
        if tmp_audio is not None:
            tmp_audio.unlink(missing_ok=True)

    return present(result, args.copy, args.json)


def present(result, copy: bool, as_json: bool = False) -> int:
    """Render the result, including the review interface for unsafe outcomes."""
    if as_json:
        import json as _json
        text = result.text_to_use
        if copy and text:
            subprocess.run(["pbcopy"], input=text.encode("utf-8"), check=False)
        print(_json.dumps({
            "outcome": result.outcome.value,
            "text": text,
            "raw_text": result.raw_text,
            "polished_text": result.polished_text,
            "warnings": result.warnings,
            "protected_changes": [c.detail for c in result.protected_changes],
            "copied": bool(copy and text),
            "transcribe_ms": result.transcribe_ms,
            "polish_ms": result.polish_ms,
            "error": result.error,
        }))
        return {Outcome.SAFE: 0, Outcome.REVIEW: 1, Outcome.REJECTED: 2,
                Outcome.NO_SPEECH: 0, Outcome.FAILED: 4}[result.outcome]

    if result.outcome is Outcome.NO_SPEECH:
        # Not an error. Saying nothing must produce nothing -- the alternative
        # is a hallucinated phrase, which is what the VAD gate exists to stop.
        print(f"{YEL}No speech detected.{RST} Nothing was transcribed.")
        print(f"{DIM}  ({result.transcribe_ms} ms){RST}")
        return 0

    if result.outcome is Outcome.FAILED:
        print(f"{RED}FAILED{RST}: {result.error}", file=sys.stderr)
        return 4

    banner = {
        Outcome.SAFE: f"{GRN}SAFE{RST}",
        Outcome.REVIEW: f"{YEL}REVIEW REQUIRED{RST}",
        Outcome.REJECTED: f"{RED}REJECTED{RST}",
    }[result.outcome]
    print(f"  status    : {banner}")
    timing = f"{result.transcribe_ms} ms transcribe"
    if result.polish_ms:
        timing += f" + {result.polish_ms} ms polish"
    print(f"  {DIM}{timing}{RST}\n")

    changed = result.polished_text and result.polished_text != result.raw_text
    if result.outcome is Outcome.SAFE and not changed:
        print(f"{BOLD}{result.raw_text}{RST}")
    elif result.outcome is Outcome.SAFE:
        print(f"{DIM}raw     :{RST} {result.raw_text}")
        print(f"{BOLD}polished:{RST} {result.polished_text}")
    else:
        # Review interface: raw, polished, and every protected change with its
        # reason (CLAUDE.md §6 Phase 5.5).
        print(f"{DIM}raw     :{RST} {result.raw_text}")
        if changed:
            print(f"{DIM}polished:{RST} {result.polished_text}")
        if result.protected_changes:
            print(f"\n{BOLD}Protected changes:{RST}")
            for c in result.protected_changes:
                print(f"  {RED}!{RST} [{c.category}] {c.detail}")
        if result.warnings:
            print(f"\n{BOLD}Warnings:{RST}")
            for w in result.warnings:
                print(f"  {YEL}~{RST} {w}")
        print(f"\n{DIM}Auto-paste is blocked. Use the raw text, edit it, "
              f"or discard.{RST}")

    text = result.text_to_use
    if copy and text:
        subprocess.run(["pbcopy"], input=text.encode("utf-8"), check=False)
        which = "polished" if result.outcome is Outcome.SAFE and changed else "raw"
        print(f"\n{DIM}Copied {which} text to clipboard. Clipboard history is a "
              f"leakage path -- clear it after pasting into a record.{RST}")

    return {Outcome.SAFE: 0, Outcome.REVIEW: 1, Outcome.REJECTED: 2}[result.outcome]


if __name__ == "__main__":
    raise SystemExit(main())
