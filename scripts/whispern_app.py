#!/usr/bin/env python3
"""WhisperN — minimal desktop GUI for local medical dictation.

A window around the same pipeline `scripts/dictate.py` uses. Nothing here
transcribes, edits, or validates anything: it starts a recording, hands the
audio to `DictationPipeline`, and renders what comes back.

PASTE BEHAVIOUR, and why it is not uniform:

  safe_to_paste    pasted automatically (CLAUDE.md §6 Phase 5.4)
  review_required  NEVER pasted automatically (CLAUDE.md §4.2). The text is
                   shown with every warning, and pasting requires a second,
                   explicit click after reading.
  rejected         never pasted automatically; the raw transcript is offered
  no_speech        nothing happens

"Safe" means no protected content changed. It does NOT mean every word is
correct: named-entity accuracy is 62.5%, so roughly one distinctive drug or
organism name in three is wrong (docs/validation-report.md §3.3). Read what you
paste.

Pasting synthesises ⌘V, which requires Accessibility permission for whatever
process runs this. The app detects that and says so rather than failing
silently.

Usage:
  scripts/whispern_app.py
"""

from __future__ import annotations

import queue
import subprocess
import sys
import tempfile
import threading
import time
import tkinter as tk
from pathlib import Path
from tkinter import font as tkfont

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "src"))
sys.path.insert(0, str(REPO_ROOT / "scripts"))

from dictate import load_config  # noqa: E402
from pipeline.dictation import DictationPipeline, Outcome  # noqa: E402
from pipeline.polisher import LocalOnlyError, LocalPolisher  # noqa: E402
from validation.validator import Validator  # noqa: E402

BG      = "#1c1f26"
PANEL   = "#252932"
FG      = "#e6e8ec"
MUTED   = "#8b91a0"
GREEN   = "#4caf7d"
AMBER   = "#d9a441"
RED     = "#d15b5b"
BLUE    = "#5b8fd1"


class WhisperNApp:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.events: queue.Queue = queue.Queue()
        self.recording = False
        self.busy = False
        self.proc: subprocess.Popen | None = None
        self.audio_path: Path | None = None
        self.result = None

        cfg = load_config(REPO_ROOT / "config/app.yaml")
        self.mode = cfg.get("polishing.mode", "clinical_strict")
        self.polishing_on = cfg.get("polishing.enabled", "false").lower() == "true"
        self.model = cfg.get("polishing.model", "")
        self.device = "0"
        self.auto_paste = tk.BooleanVar(value=True)

        polisher = None
        if self.polishing_on and self.model and not self.model.startswith("REPLACE"):
            try:
                polisher = LocalPolisher(
                    endpoint=cfg.get("polishing.endpoint", "http://127.0.0.1:11434"),
                    model=self.model,
                    prompt_path=str(REPO_ROOT / "prompts/medical_polisher.md"),
                    timeout_seconds=int(cfg.get("polishing.timeout_seconds", "20")),
                )
            except LocalOnlyError:
                polisher = None

        self.pipeline = DictationPipeline(
            repo_root=REPO_ROOT,
            prompt_file=REPO_ROOT / "runtime/tmp/asr_prompt_id.txt",
            polisher=polisher,
            validator=Validator.from_dictionaries(REPO_ROOT / "dictionaries", self.mode),
            mode=self.mode,
        )

        self._build_ui()
        self.root.after(80, self._drain)

    # ------------------------------------------------------------ ui

    def _build_ui(self) -> None:
        r = self.root
        r.title("WhisperN")
        r.configure(bg=BG)
        r.geometry("620x460")
        r.minsize(520, 400)

        mono = tkfont.Font(family="SF Mono", size=13)
        ui = tkfont.Font(family="SF Pro Text", size=12)
        small = tkfont.Font(family="SF Pro Text", size=11)

        # --- mode indicator (CLAUDE.md §6 Phase 5.10) ---------------------
        bar = tk.Frame(r, bg=PANEL, height=34)
        bar.pack(fill="x", side="top")
        bar.pack_propagate(False)

        pol = f"polish {self.model}" if self.polishing_on else "polish OFF"
        pol_col = AMBER if self.polishing_on else GREEN
        for text, col in (
            (self.mode, FG), ("·", MUTED), (pol, pol_col),
            ("·", MUTED), ("VAD ON", GREEN),
        ):
            tk.Label(bar, text=text, bg=PANEL, fg=col, font=small).pack(side="left", padx=6, pady=7)

        self.paste_lbl = tk.Label(bar, text="", bg=PANEL, fg=MUTED, font=small)
        self.paste_lbl.pack(side="right", padx=10)
        self._update_paste_label()

        # --- status -------------------------------------------------------
        self.status = tk.Label(r, text="Ready", bg=BG, fg=MUTED, font=ui)
        self.status.pack(pady=(14, 6))

        self.button = tk.Button(
            r, text="●  Record", command=self.toggle,
            bg=PANEL, fg=FG, font=ui, relief="flat", bd=0,
            activebackground=BLUE, activeforeground="#ffffff",
            highlightthickness=0, padx=26, pady=10,
        )
        self.button.pack(pady=(0, 12))

        # --- result -------------------------------------------------------
        wrap = tk.Frame(r, bg=BG)
        wrap.pack(fill="both", expand=True, padx=14)

        self.text = tk.Text(
            wrap, bg=PANEL, fg=FG, font=mono, relief="flat", bd=0,
            wrap="word", padx=12, pady=10, height=7,
            insertbackground=FG, highlightthickness=0,
        )
        self.text.pack(fill="both", expand=True)
        self.text.tag_configure("warn", foreground=AMBER)
        self.text.tag_configure("bad", foreground=RED)
        self.text.tag_configure("dim", foreground=MUTED)
        self.text.insert("1.0", "Press Record, or hit Space.\n", "dim")
        self.text.configure(state="disabled")

        # --- actions ------------------------------------------------------
        act = tk.Frame(r, bg=BG)
        act.pack(fill="x", padx=14, pady=12)

        tk.Checkbutton(
            act, text="Auto-paste safe results", variable=self.auto_paste,
            command=self._update_paste_label, bg=BG, fg=MUTED, font=small,
            selectcolor=PANEL, activebackground=BG, activeforeground=FG,
            highlightthickness=0, bd=0,
        ).pack(side="left")

        self.paste_btn = tk.Button(
            act, text="Paste", command=self.paste_now, bg=PANEL, fg=FG,
            font=small, relief="flat", bd=0, padx=16, pady=6,
            activebackground=BLUE, activeforeground="#ffffff",
            highlightthickness=0, state="disabled",
        )
        self.paste_btn.pack(side="right", padx=(6, 0))

        self.copy_btn = tk.Button(
            act, text="Copy", command=self.copy_now, bg=PANEL, fg=FG,
            font=small, relief="flat", bd=0, padx=16, pady=6,
            activebackground=BLUE, activeforeground="#ffffff",
            highlightthickness=0, state="disabled",
        )
        self.copy_btn.pack(side="right")

        r.bind("<space>", lambda e: self.toggle())
        r.bind("<Escape>", lambda e: self.root.quit())

    def _update_paste_label(self) -> None:
        on = self.auto_paste.get()
        self.paste_lbl.configure(
            text="auto-paste: safe only" if on else "auto-paste: off",
            fg=AMBER if on else GREEN,
        )

    def _show(self, chunks: list[tuple[str, str]]) -> None:
        self.text.configure(state="normal")
        self.text.delete("1.0", "end")
        for body, tag in chunks:
            self.text.insert("end", body, tag)
        self.text.configure(state="disabled")

    # ------------------------------------------------------------ record

    def toggle(self) -> None:
        if self.busy:
            return
        self.stop() if self.recording else self.start()

    def start(self) -> None:
        (REPO_ROOT / "runtime/tmp").mkdir(parents=True, exist_ok=True)
        fd, name = tempfile.mkstemp(suffix=".wav", prefix="gui.",
                                    dir=REPO_ROOT / "runtime/tmp")
        import os
        os.close(fd)
        self.audio_path = Path(name)

        self.proc = subprocess.Popen(
            ["ffmpeg", "-nostdin", "-loglevel", "error", "-f", "avfoundation",
             "-i", f":{self.device}", "-ar", "16000", "-ac", "1",
             "-c:a", "pcm_s16le", "-y", str(self.audio_path)],
            stderr=subprocess.PIPE,
        )
        time.sleep(0.4)
        if self.proc.poll() is not None:
            err = (self.proc.stderr.read() or b"").decode(errors="replace")
            self._show([("Recording failed to start.\n\n", "bad"), (err[-400:], "dim")])
            self.audio_path.unlink(missing_ok=True)
            return

        self.recording = True
        self.button.configure(text="■  Stop", bg=RED)
        self.status.configure(text="Recording…", fg=RED)
        self._show([("Listening.\n", "dim")])
        self.copy_btn.configure(state="disabled")
        self.paste_btn.configure(state="disabled")

    def stop(self) -> None:
        if not self.proc:
            return
        # SIGINT so ffmpeg finalises the WAV header; kill would truncate it.
        self.proc.send_signal(subprocess.signal.SIGINT)
        try:
            self.proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            self.proc.kill()
        self.proc = None
        self.recording = False
        self.busy = True

        self.button.configure(text="●  Record", bg=PANEL, state="disabled")
        self.status.configure(text="Transcribing…", fg=BLUE)
        self._show([("Working…\n", "dim")])

        # ffmpeg buffers the WAV until close; give it a moment, then run the
        # pipeline off the UI thread.
        threading.Thread(target=self._work, args=(self.audio_path,), daemon=True).start()

    def _work(self, path: Path) -> None:
        time.sleep(0.4)
        try:
            result = self.pipeline.run(path)
            self.events.put(("done", result))
        except Exception as exc:  # noqa: BLE001
            self.events.put(("error", f"{type(exc).__name__}: {exc}"))
        finally:
            path.unlink(missing_ok=True)

    # ------------------------------------------------------------ results

    def _drain(self) -> None:
        try:
            while True:
                kind, payload = self.events.get_nowait()
                if kind == "done":
                    self._render(payload)
                else:
                    self.busy = False
                    self.button.configure(state="normal")
                    self.status.configure(text="Error", fg=RED)
                    self._show([(str(payload), "bad")])
        except queue.Empty:
            pass
        self.root.after(80, self._drain)

    def _render(self, result) -> None:
        self.busy = False
        self.result = result
        self.button.configure(state="normal")

        if result.outcome is Outcome.NO_SPEECH:
            self.status.configure(text="No speech detected", fg=AMBER)
            self._show([("Nothing was transcribed.\n\n", "warn"),
                        ("The voice-activity gate found no speech, so the model "
                         "was not run. This is deliberate: Whisper invents text "
                         "from silence.\n", "dim")])
            return

        if result.outcome is Outcome.FAILED:
            self.status.configure(text="Failed", fg=RED)
            self._show([(result.error or "unknown error", "bad")])
            return

        text = result.text_to_use
        self.copy_btn.configure(state="normal")
        self.paste_btn.configure(state="normal")

        if result.outcome is Outcome.SAFE:
            self.status.configure(text=f"Safe · {result.transcribe_ms} ms", fg=GREEN)
            chunks = [(text + "\n", "")]
            if self.auto_paste.get():
                chunks.append(("\nPasted.\n", "dim"))
            self._show(chunks)
            if self.auto_paste.get():
                self.root.after(60, lambda: self._paste(text))
            else:
                self._copy(text)
            return

        # review_required / rejected: never auto-pasted, whatever the setting.
        label = ("Review required" if result.outcome is Outcome.REVIEW
                 else "Rejected")
        self.status.configure(text=label, fg=AMBER if result.outcome is Outcome.REVIEW else RED)

        chunks: list[tuple[str, str]] = [(text + "\n", "")]
        if result.protected_changes:
            chunks.append(("\nProtected changes\n", "bad"))
            for c in result.protected_changes:
                chunks.append((f"  ! [{c.category}] {c.detail}\n", "bad"))
        if result.warnings:
            chunks.append(("\nWarnings\n", "warn"))
            for w in result.warnings:
                chunks.append((f"  ~ {w}\n", "warn"))
        chunks.append(("\nNot pasted. Read it, then Paste or Copy if correct.\n", "dim"))
        self._show(chunks)
        self._copy(text)

    # ------------------------------------------------------------ clipboard

    @staticmethod
    def _copy(text: str) -> None:
        subprocess.run(["pbcopy"], input=text.encode("utf-8"), check=False)

    def copy_now(self) -> None:
        if self.result:
            self._copy(self.result.text_to_use)
            self.status.configure(text="Copied", fg=GREEN)

    def paste_now(self) -> None:
        if self.result:
            self._paste(self.result.text_to_use)

    def _paste(self, text: str) -> None:
        """Copy, yield focus, then synthesise ⌘V.

        Hiding the window is what makes this work: macOS returns focus to the
        previously active application, so the keystroke lands where the user
        was typing rather than in this window.
        """
        self._copy(text)
        self.root.withdraw()
        self.root.update()
        time.sleep(0.25)

        r = subprocess.run(
            ["osascript", "-e",
             'tell application "System Events" to keystroke "v" using command down'],
            capture_output=True,
        )
        self.root.deiconify()

        if r.returncode != 0:
            err = r.stderr.decode(errors="replace")
            if "not allowed" in err or "1002" in err:
                self.status.configure(text="Paste blocked — grant Accessibility", fg=RED)
                self._show([
                    (text + "\n\n", ""),
                    ("Pasting needs Accessibility permission.\n", "bad"),
                    ("System Settings → Privacy & Security → Accessibility, "
                     "then add whatever runs this app (Terminal, or Python).\n\n", "dim"),
                    ("The text is on the clipboard; ⌘V works.\n", "dim"),
                ])
            else:
                self.status.configure(text="Paste failed", fg=RED)
        else:
            self.status.configure(text="Pasted", fg=GREEN)


def main() -> int:
    root = tk.Tk()
    WhisperNApp(root)
    root.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
