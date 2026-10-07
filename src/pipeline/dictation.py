"""End-to-end dictation pipeline: audio in, reviewed text out.

Wires together the stages built in Phases 1-4 and adds the two things Phase 5
requires: a voice-activity gate, and disposal of everything sensitive when the
utterance is done.

Ordering matters and is deliberate:

    audio -> VAD gate -> transcribe -> normalize -> polish -> validate

The VAD gate comes first because Whisper hallucinates on non-speech: an empty
room transcribes as "you", "-", or "Thank you." (docs/phase4-results.md §5.2).
Nothing downstream can catch that. The validator compares polished text against
the raw transcript, so an ASR fabrication is already inside the trusted
baseline by the time validation runs. The only place to stop it is before
transcription happens at all.
"""

from __future__ import annotations

import logging
import re
import shutil
import subprocess
import tempfile
import time
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path

from .normalize import normalize_transcript
from .polisher import LocalPolisher, PolishResult

log = logging.getLogger(__name__)


class Outcome(str, Enum):
    SAFE = "safe_to_paste"
    REVIEW = "review_required"
    REJECTED = "rejected"
    NO_SPEECH = "no_speech"
    FAILED = "failed"


@dataclass
class DictationResult:
    outcome: Outcome
    raw_text: str = ""
    polished_text: str = ""
    protected_changes: list = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    error: str | None = None
    transcribe_ms: int = 0
    polish_ms: int = 0
    # True when polished_text is an email/research rewrite (ADR 0002). A
    # rewrite is always REVIEW, and is what gets pasted once approved.
    rewritten: bool = False

    @property
    def text_to_use(self) -> str:
        """The only text a caller should ever paste or display as the result."""
        if self.outcome is Outcome.SAFE:
            return self.polished_text
        if self.rewritten and self.outcome is Outcome.REVIEW:
            return self.polished_text
        return self.raw_text


class DictationPipeline:
    def __init__(
        self,
        repo_root: Path,
        whisper_bin: Path | None = None,
        model: Path | None = None,
        vad_model: Path | None = None,
        prompt_file: Path | None = None,
        polisher: LocalPolisher | None = None,
        validator=None,
        mode: str = "clinical_strict",
        use_vad: bool = True,
        rewriter: LocalPolisher | None = None,
    ) -> None:
        self.repo_root = repo_root
        self.whisper_bin = whisper_bin or repo_root / "whisper.cpp/build/bin/whisper-cli"
        self.model = model or repo_root / "whisper.cpp/models/ggml-large-v3-turbo.bin"
        self.vad_model = vad_model or repo_root / "whisper.cpp/models/ggml-silero-v5.1.2.bin"
        self.prompt_file = prompt_file
        self.polisher = polisher
        self.validator = validator
        self.mode = mode
        self.use_vad = use_vad
        self.rewriter = rewriter

    # -- stage 1: transcription with the VAD gate --------------------------

    def has_speech(self, wav: Path) -> bool:
        """Return True if the VAD model finds any speech segment.

        Used as a **gate**, not a filter. whisper-cli's own --vad flag trims
        the audio to detected segments before transcribing, which measurably
        degrades recognition: content WER on the real corpus rose from 13.68%
        to 15.31%, and piperacillin-tazobactam became "pipercyllum-tazoza-
        bactam". Padding the segments barely helped (14.98% at 400 ms).

        Detecting speech and then transcribing the *untrimmed* audio gets the
        hallucination protection without paying that cost.
        """
        vad_bin = self.repo_root / "whisper.cpp/build/bin/whisper-vad-speech-segments"
        if not vad_bin.exists() or not self.vad_model.exists():
            log.warning("VAD unavailable; proceeding without the speech gate")
            return True
        try:
            proc = subprocess.run(
                [str(vad_bin), "-vm", str(self.vad_model), "-f", str(wav)],
                capture_output=True, timeout=30,
            )
        except (subprocess.TimeoutExpired, OSError) as exc:
            # Fail open on gate failure: refusing to transcribe real speech
            # because a helper crashed would be worse than the hallucination
            # risk, and everything downstream still applies.
            log.warning("VAD gate failed (%s); proceeding", exc)
            return True

        out = proc.stdout.decode(errors="replace")
        m = re.search(r"Detected\s+(\d+)\s+speech segments", out)
        if not m:
            log.warning("could not parse VAD output; proceeding")
            return True
        return int(m.group(1)) > 0

    def transcribe(self, audio: Path) -> tuple[str, int]:
        """Transcribe `audio`. Returns (text, elapsed_ms).

        An empty string means the VAD gate found no speech. That is a normal
        outcome, not an error: the user pressed the key and did not speak, or
        the microphone captured only room noise.
        """
        started = time.monotonic()
        tmpdir = Path(tempfile.mkdtemp(prefix="asr.", dir=self.repo_root / "runtime/tmp"))
        try:
            wav = tmpdir / "input.wav"
            subprocess.run(
                ["afconvert", "-f", "WAVE", "-d", "LEI16@16000", "-c", "1",
                 str(audio), str(wav)],
                check=True, capture_output=True,
            )

            # Gate first. If there is no speech, do not run the model at all:
            # Whisper invents text from non-speech, and nothing downstream can
            # tell an invented phrase from a real one.
            if self.use_vad and not self.has_speech(wav):
                return "", int((time.monotonic() - started) * 1000)

            # Transcribe the FULL audio. No --vad here, deliberately.
            args = [str(self.whisper_bin), "-m", str(self.model), "-f", str(wav),
                    "-l", "en", "-nt", "-np",
                    "--output-txt", "--output-file", str(tmpdir / "out")]
            if self.prompt_file and self.prompt_file.exists():
                args += ["--prompt", self.prompt_file.read_text(encoding="utf-8").strip()]

            subprocess.run(args, check=True, capture_output=True)

            out = tmpdir / "out.txt"
            text = out.read_text(encoding="utf-8").strip() if out.exists() else ""
            text = "\n".join(line.strip() for line in text.splitlines() if line.strip())
            return text, int((time.monotonic() - started) * 1000)
        finally:
            # Temporary audio must not survive the utterance (CLAUDE.md §3.1).
            shutil.rmtree(tmpdir, ignore_errors=True)

    # -- full pipeline -----------------------------------------------------

    def run(self, audio: Path, glossary: list[str] | None = None,
            rewrite: bool = False) -> DictationResult:
        try:
            raw, t_ms = self.transcribe(audio)
        except subprocess.CalledProcessError as exc:
            stderr = (exc.stderr or b"").decode(errors="replace")[-400:]
            return DictationResult(Outcome.FAILED, error=f"transcription failed: {stderr}")
        except Exception as exc:  # noqa: BLE001
            return DictationResult(Outcome.FAILED, error=f"{type(exc).__name__}: {exc}")

        # VAD gate: no speech means no output. Emitting a hallucinated phrase
        # here would be worse than emitting nothing.
        if not raw.strip():
            return DictationResult(Outcome.NO_SPEECH, transcribe_ms=t_ms)

        raw = normalize_transcript(raw)

        if rewrite:
            return self._rewrite(raw, t_ms)

        # Polishing disabled or unavailable: the raw transcript is the result.
        # It still goes through validation so that unrecognised drug names in
        # the raw text are surfaced.
        if self.polisher is None:
            return self._validate_only(raw, t_ms)

        result: PolishResult = self.polisher.polish(raw, glossary or [], self.mode)
        if not result.ok:
            out = self._validate_only(raw, t_ms)
            out.warnings = [f"polishing unavailable: {result.error}"] + out.warnings
            return out

        if self.validator is None:
            return DictationResult(
                Outcome.REVIEW, raw_text=raw, polished_text=result.polished_text,
                warnings=["no validator configured; review required"],
                transcribe_ms=t_ms, polish_ms=result.latency_ms,
            )

        v = self.validator.validate(raw, result.polished_text)
        return DictationResult(
            outcome=Outcome(v.status.value),
            raw_text=raw,
            polished_text=result.polished_text,
            protected_changes=v.protected_changes,
            warnings=v.warnings,
            transcribe_ms=t_ms,
            polish_ms=result.latency_ms,
        )

    def _rewrite(self, raw: str, t_ms: int) -> DictationResult:
        """Email/research rewrite (ADR 0002). Every outcome needs a click.

        On any failure the raw transcript comes back as REVIEW rather than
        SAFE: the speaker asked for a rewrite, and auto-pasting the rambling
        original instead would be a surprise they did not approve.
        """
        def raw_for_review(why: str) -> DictationResult:
            out = self._validate_only(raw, t_ms)
            out.outcome = Outcome.REVIEW
            out.warnings = [why] + out.warnings
            return out

        if self.rewriter is None:
            return raw_for_review("rewrite unavailable: no rewrite model configured")

        result: PolishResult = self.rewriter.polish(raw, [], "rewrite")
        if not result.ok:
            out = raw_for_review(f"rewrite unavailable: {result.error}")
            out.polish_ms = result.latency_ms
            return out
        if self.validator is None:
            return DictationResult(
                Outcome.REVIEW, raw_text=raw, polished_text=result.polished_text,
                warnings=["no validator configured"], transcribe_ms=t_ms,
                polish_ms=result.latency_ms, rewritten=True,
            )

        v = self.validator.validate_rewrite(raw, result.polished_text)
        warnings = list(v.warnings)
        for f in result.flags:
            warnings.append(f"model was unsure about: {f.raw_span!r}")
        return DictationResult(
            outcome=Outcome(v.status.value),
            raw_text=raw,
            polished_text=result.polished_text,
            protected_changes=v.protected_changes,
            warnings=warnings,
            transcribe_ms=t_ms,
            polish_ms=result.latency_ms,
            rewritten=v.status.value == Outcome.REVIEW.value,
        )

    def _validate_only(self, raw: str, t_ms: int) -> DictationResult:
        """Validate the raw transcript against itself.

        Detects nothing about polishing -- there was none -- but does surface
        drug-like words the ASR produced that are not in the approved
        vocabulary, which is where the dangerous transcription errors live.
        """
        warnings: list[str] = []
        outcome = Outcome.SAFE
        if self.validator is not None:
            v = self.validator.validate(raw, raw)
            warnings = v.warnings
            if v.status.value != "safe_to_paste":
                outcome = Outcome(v.status.value)
        return DictationResult(
            outcome, raw_text=raw, polished_text=raw,
            warnings=warnings, transcribe_ms=t_ms,
        )
