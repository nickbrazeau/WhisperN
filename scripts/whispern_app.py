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

REWRITE MODE (ADR 0002, email and research only) sends the transcript to a
local model that rewrites it as concise prose. Every rewrite is
review_required: it is shown above the original transcript with any protected
facts that moved, and pastes only on a click. "Paste original" is always
offered beside it. The Rewrite button rewrites whatever is in the draft, so a
Raw take can be rewritten without dictating again.

The draft (transcript or rewrite) is editable. Copy and Paste send the draft
as edited; auto-paste of a safe result sends it as transcribed.

"Safe" means no protected content changed. It does NOT mean every word is
correct: named-entity accuracy is 62.5%, so roughly one distinctive drug or
organism name in three is wrong (docs/validation-report.md §3.3). Read what you
paste.

Pasting synthesises ⌘V, which requires Accessibility permission for whatever
process runs this. The app detects that and says so rather than failing
silently.

Prefer launching through the bundle built by `scripts/build_app.sh`: macOS
attributes privacy grants to the application, so running this from a terminal
grants Microphone, Accessibility, and Automation to that terminal and to every
command anyone ever runs in it. The bundle owns those grants alone.

⌘V goes wherever macOS thinks the front application is, so the app tracks
which application you came from and activates it explicitly before typing.
That target is shown in the title bar. If it is unknown, or if the app is not
actually frontmost when the keystroke is about to be sent, nothing is typed
anywhere and the text is left on the clipboard -- misdirecting clinical text
into the wrong window is worse than not pasting at all.

Usage:
  scripts/whispern_app.py
"""

from __future__ import annotations

import os
import queue
import subprocess
import sys
import tempfile
import threading
import time
import tkinter as tk
from pathlib import Path
from tkinter import font as tkfont
from tkinter import ttk

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "src"))
sys.path.insert(0, str(REPO_ROOT / "scripts"))

from dictate import load_config  # noqa: E402
from integrations import audio as audio_devices  # noqa: E402
from integrations import clipboard  # noqa: E402
from integrations import media  # noqa: E402
from pipeline.dictation import DictationPipeline, Outcome  # noqa: E402
from pipeline.polisher import LocalOnlyError, LocalPolisher  # noqa: E402
from validation.validator import Validator  # noqa: E402

# Duke palette (Duke University brand colours). Navy carries the chrome, Royal
# the actions, and the work area is light so a transcript reads like a page.
# Every text colour below clears WCAG AA (4.5:1) on the background it sits on.
NAVY     = "#012169"   # Duke Navy Blue: header
ROYAL    = "#00539B"   # Duke Royal Blue: primary action, accents
SHALE    = "#0577B1"   # Shale Blue: hover on primary
HATTERAS = "#E2E6ED"   # Hatteras: info strip, card borders, text on navy
WHISPER  = "#F3F2F1"   # Whisper Gray: window background
GINGER   = "#FCF7E5"   # Ginger Beer: warning wash
DANDELION = "#FFD960"  # Dandelion: problem text on navy
BAD_WASH = "#FBEDEB"

# Semantic names the rest of the file uses.
BG      = WHISPER
PANEL   = "#FFFFFF"
FG      = "#262626"
MUTED   = "#5F6368"
GREEN   = "#1D6363"    # Duke Magnolia: success text
AMBER   = "#9A5200"    # darkened Persimmon: warning text
RED     = "#B3261E"
BLUE    = ROYAL

BLUE_BTN     = ROYAL
BLUE_BTN_DIM = "#9DB3CE"

# How often to sample which application is frontmost. Each sample costs one
# osascript process (~60-80 ms of subprocess, not of this thread). Lower is
# fresher; higher is cheaper. 1 s keeps the target correct even if you switch
# apps immediately before recording.
POLL_SECONDS = 1.0

# How long dictated text stays on the clipboard (CLAUDE.md §3.1: clipboard is a
# leakage path). After a confirmed paste the target app has already read it;
# otherwise it is left long enough to ⌘V by hand. Cleared only if nothing else
# was copied since, so the user's own later copy is never wiped.
CLIPBOARD_AFTER_PASTE_MS = 1500
CLIPBOARD_TTL_MS = 120_000

_FRONTMOST = 'tell application "System Events" to get name of first process whose frontmost is true'


def _osa(script: str) -> subprocess.CompletedProcess:
    return subprocess.run(["osascript", "-e", script], capture_output=True)


def _as_literal(s: str) -> str:
    """Escape a string for interpolation into an AppleScript literal."""
    return s.replace("\\", "\\\\").replace('"', '\\"')


class FlatButton(tk.Label):
    """A Label drawn as a flat button, in "primary" or "secondary" style.

    macOS Aqua ignores -bg on tk.Button and substitutes its own native
    control, which rendered pale grey buttons with near-invisible text. A
    Label honours its colours, so press, hover, and disabled behaviour are
    wired here. `configure(state=..., bg=...)` keeps working as callers
    expect: bg sets the enabled colour, and disabled always draws dimmed.
    """

    STYLES = {
        "primary":   dict(bg=ROYAL, fg="#FFFFFF", hover=SHALE,
                          dis_bg=BLUE_BTN_DIM, dis_fg="#FFFFFF", border=ROYAL),
        "secondary": dict(bg="#FFFFFF", fg=NAVY, hover=HATTERAS,
                          dis_bg="#FFFFFF", dis_fg="#A8ADB4", border="#C4CBD6"),
    }

    def __init__(self, master, text: str, command, style: str = "primary", **kw) -> None:
        st = dict(self.STYLES[style])
        if "bg" in kw:
            st["bg"] = kw.pop("bg")
        self._st = st
        self._enabled_flag = kw.pop("state", "normal") != "disabled"
        super().__init__(master, text=text, cursor="hand2",
                         highlightthickness=1, bd=0, **kw)
        self._command = command
        self.bind("<Button-1>", self._on_press)
        self.bind("<ButtonRelease-1>", self._on_release)
        self.bind("<Enter>", lambda e: self._paint(hover=True))
        self.bind("<Leave>", lambda e: self._paint())
        self._paint()

    def _paint(self, hover: bool = False) -> None:
        st = self._st
        if not self._enabled_flag:
            bg, fg = st["dis_bg"], st["dis_fg"]
        else:
            bg, fg = (st["hover"] if hover else st["bg"]), st["fg"]
        super().configure(bg=bg, fg=fg, activebackground=bg,
                          highlightbackground=st["border"], highlightcolor=st["border"])

    def configure(self, cnf=None, **kw):
        if "bg" in kw:
            self._st["bg"] = self._st["hover"] = kw.pop("bg")
        if "state" in kw:
            self._enabled_flag = kw.pop("state") != "disabled"
        if cnf or kw:
            super().configure(cnf, **kw)
        self._paint()

    config = configure

    def cget(self, key):
        if key == "state":
            return "normal" if self._enabled_flag else "disabled"
        return super().cget(key)

    def _on_press(self, _evt) -> None:
        if self._enabled_flag:
            super().configure(bg=self._st["border"] if self._st["bg"] == "#FFFFFF" else NAVY)

    def _on_release(self, evt) -> None:
        if not self._enabled_flag:
            return
        self._paint(hover=True)
        # Fire only if released inside, as a real button does -- dragging off
        # the widget is how you cancel a mis-click, and starting a recording
        # by accident is worth guarding against.
        if 0 <= evt.x < self.winfo_width() and 0 <= evt.y < self.winfo_height():
            self._command()


class FocusTracker:
    """Remembers the last application that was frontmost other than this one.

    ⌘V is delivered to whatever macOS considers frontmost, so pasting at the
    cursor requires knowing which application the user came from. macOS offers
    no way to ask for "the previous app": System Events reports only the
    current frontmost process, and its process list is not in front-to-back
    order (verified: our own process appeared second-to-last while frontmost).
    So the answer has to be sampled before we take focus.

    PRIVACY (CLAUDE.md §3.1): this records application *names* only -- never a
    window title, document name, or any content. In memory only. Never
    logged, never written to disk, never placed in the audit trail. It is
    discarded when the app exits.
    """

    def __init__(self, poll_seconds: float = POLL_SECONDS,
                 self_name: str | None = None) -> None:
        self.poll_seconds = poll_seconds
        self._target: str | None = None
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        if self_name is None:
            # Identify ourselves by PID, not by guessing "python" vs
            # "python3" -- both appear in a normal process list.
            r = _osa('tell application "System Events" to get name of first '
                     f'process whose unix id is {os.getpid()}')
            self_name = r.stdout.decode(errors="replace").strip()
        self.self_name = self_name or "python"

    @property
    def target(self) -> str | None:
        return self._target

    def consider(self, name: str | None) -> None:
        """Record `name` as the paste target unless it is us or empty.

        Split out from the poll loop so it can be tested without a GUI.
        """
        if name and name != self.self_name:
            self._target = name

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def _run(self) -> None:
        while not self._stop.is_set():
            r = _osa(_FRONTMOST)
            if r.returncode == 0:
                self.consider(r.stdout.decode(errors="replace").strip())
            self._stop.wait(self.poll_seconds)


def paste_into(process_name: str) -> tuple[str, str]:
    """Activate `process_name`, confirm it really is frontmost, then send ⌘V.

    Activation and verification happen inside a single osascript so nothing can
    steal focus between the two, and so the whole handoff costs one process
    rather than three.

    The verification is the safety property, not a nicety. Without it a stale
    or wrong target sends clinical text into whatever happens to be frontmost
    -- during development this pasted test markers into a terminal. Returns
    ("pasted"|"abort"|"error", detail).
    """
    lit = _as_literal(process_name)
    script = f'''
    tell application "System Events"
      set procs to every process whose name is "{lit}"
      if (count of procs) = 0 then return "abort:gone"
      set frontmost of item 1 of procs to true
      delay 0.08
      set f to name of first process whose frontmost is true
      if f is not "{lit}" then return "abort:" & f
      keystroke "v" using command down
      return "pasted"
    end tell
    '''
    r = _osa(script)
    if r.returncode != 0:
        return "error", r.stderr.decode(errors="replace").strip()
    out = r.stdout.decode(errors="replace").strip()
    if out == "pasted":
        return "pasted", process_name
    if out.startswith("abort:"):
        return "abort", out[len("abort:"):]
    return "error", out or "no result"


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
        self.prefer_mic = cfg.get("recording.prefer_microphone", "headset")
        self.pause_music = cfg.get("recording.pause_music", "true").lower() == "true"
        self.auto_paste = tk.BooleanVar(value=True)
        self.rewrite_model = cfg.get("rewrite.model", "")
        self.rewrite_on = (cfg.get("rewrite.enabled", "false").lower() == "true"
                           and cfg.get("rewrite.default_on", "false").lower() == "true")

        # Resolved by name, never cached as a bare index. Enumerating costs
        # 0.24-1.03 s, too variable to sit in front of a recording without
        # clipping speech onset, so it is resolved in the background and
        # refreshed after each take.
        self.device: audio_devices.InputDevice | None = None
        self._paused_players: list[str] = []
        threading.Thread(target=self._refresh_device, daemon=True).start()

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

        rewriter = None
        if cfg.get("rewrite.enabled", "false").lower() == "true" and self.rewrite_model:
            try:
                rewriter = LocalPolisher(
                    endpoint=cfg.get("rewrite.endpoint", "http://127.0.0.1:11434"),
                    model=self.rewrite_model,
                    prompt_path=str(REPO_ROOT / "prompts/eloquent_rewriter.md"),
                    timeout_seconds=int(cfg.get("rewrite.timeout_seconds", "90")),
                    keep_alive=cfg.get("rewrite.keep_alive", "30m"),
                    num_ctx=int(cfg.get("rewrite.num_ctx", "4096")),
                )
            except LocalOnlyError:
                rewriter = None
        if rewriter is None:
            self.rewrite_on = False
        else:
            # Load the model now so the first dictation does not pay for it.
            threading.Thread(target=rewriter.warm, daemon=True).start()

        self.pipeline = DictationPipeline(
            repo_root=REPO_ROOT,
            prompt_file=REPO_ROOT / "runtime/tmp/asr_prompt_id.txt",
            polisher=polisher,
            validator=Validator.from_dictionaries(REPO_ROOT / "dictionaries", self.mode),
            mode=self.mode,
            rewriter=rewriter,
        )

        # Must exist before _build_ui, which renders the current target.
        self.focus = FocusTracker()
        self.focus.start()

        self._build_ui()
        self.root.after(80, self._drain)

    # ------------------------------------------------------------ ui

    def _build_ui(self) -> None:
        r = self.root
        r.title("WhisperN")
        r.configure(bg=BG)
        r.geometry("700x640")
        r.minsize(580, 500)

        body = tkfont.Font(family="SF Pro Text", size=15)
        ui = tkfont.Font(family="SF Pro Text", size=13)
        ui_bold = tkfont.Font(family="SF Pro Text", size=13, weight="bold")
        small = tkfont.Font(family="SF Pro Text", size=11)
        head = tkfont.Font(family="SF Pro Text", size=11, weight="bold")
        brand = tkfont.Font(family="SF Pro Display", size=17, weight="bold")
        big = tkfont.Font(family="SF Pro Text", size=15, weight="bold")

        # --- header: brand, mode switch, where audio comes from and goes ----
        bar = tk.Frame(r, bg=NAVY, height=52)
        bar.pack(fill="x", side="top")
        bar.pack_propagate(False)
        tk.Label(bar, text="WhisperN", bg=NAVY, fg="#FFFFFF", font=brand).pack(
            side="left", padx=(16, 14))

        # Rewrite switch. Lives in the header because it IS the mode: what
        # comes back is a different kind of text, so it must be visible before
        # recording, not discovered after. Two segments, so the current mode
        # is always named rather than implied by one label's colour.
        seg = tk.Frame(bar, bg=HATTERAS, padx=1, pady=1)
        seg.pack(side="left", pady=12)
        self.seg_raw = tk.Label(seg, text="  Raw  ", font=small, cursor="hand2", padx=8, pady=3)
        self.seg_rw = tk.Label(seg, text="  Rewrite  ", font=small, cursor="hand2", padx=8, pady=3)
        self.seg_raw.pack(side="left")
        self.seg_rw.pack(side="left")
        self.seg_raw.bind("<Button-1>", lambda e: self.set_rewrite(False))
        self.seg_rw.bind("<Button-1>", lambda e: self.set_rewrite(True))

        # Where a paste would land, and which microphone is live. Both visible
        # before recording: a wrong target or a silent virtual driver must be
        # caught before any text exists to misdirect.
        self.target_lbl = tk.Label(bar, text="", bg=NAVY, font=small)
        self.target_lbl.pack(side="right", padx=(6, 16))
        self.mic_lbl = tk.Label(bar, text="", bg=NAVY, font=small)
        self.mic_lbl.pack(side="right", padx=6)

        # --- mode strip (CLAUDE.md §6 Phase 5.10) --------------------------
        strip = tk.Frame(r, bg=HATTERAS, height=28)
        strip.pack(fill="x")
        strip.pack_propagate(False)
        self.mode_lbl = tk.Label(strip, text="", bg=HATTERAS, fg=NAVY, font=small)
        self.mode_lbl.pack(side="left", padx=16)
        self.paste_lbl = tk.Label(strip, text="", bg=HATTERAS, font=small)
        self.paste_lbl.pack(side="right", padx=16)

        # --- record ---------------------------------------------------------
        top = tk.Frame(r, bg=BG)
        top.pack(fill="x", pady=(16, 10))
        self.status = tk.Label(top, text="Ready", bg=BG, fg=MUTED, font=ui)
        self.status.pack()
        self.button = FlatButton(
            top, text="●   Record", command=self.toggle, style="primary",
            font=big, padx=34, pady=12,
        )
        self.button.pack(pady=(8, 4))
        tk.Label(top, text="⌘W to start or stop", bg=BG, fg=MUTED, font=small).pack()

        # --- result card ----------------------------------------------------
        # Built before the action row but packed after it, so the buttons keep
        # their place at the bottom when the window is short.
        card = tk.Frame(r, bg=PANEL, highlightthickness=1,
                        highlightbackground="#D5DAE3", highlightcolor="#D5DAE3")
        # The draft: the only text Paste and Copy ever use. Editable, so a
        # wrong word is fixed here rather than in the target app.
        self.draft_lbl = tk.Label(card, text="", bg=PANEL, fg=ROYAL, font=head,
                                  anchor="w", padx=20)
        self.draft_lbl.pack(fill="x", pady=(12, 0))
        draft_box = tk.Frame(card, bg=PANEL)
        self.draft = tk.Text(
            draft_box, bg=PANEL, fg=FG, font=body, relief="flat", bd=0,
            wrap="word", padx=20, pady=8, height=5, spacing2=3,
            insertbackground=FG, highlightthickness=0, undo=True,
            selectbackground=HATTERAS, selectforeground=FG,
        )
        # Status, warnings, and the original beside a rewrite. Read-only.
        self.text = tk.Text(
            card, bg=PANEL, fg=FG, font=body, relief="flat", bd=0,
            wrap="word", padx=20, pady=10, height=6, spacing2=3,
            insertbackground=FG, highlightthickness=0,
            selectbackground=HATTERAS, selectforeground=FG,
        )
        # The native Aqua scrollbar follows the system appearance, so in dark
        # mode it drew a black bar down a white card. A styled clam scrollbar
        # follows this palette instead.
        style = ttk.Style(r)
        style.theme_use("clam")
        style.configure("Card.Vertical.TScrollbar", troughcolor=PANEL,
                        background="#C4CBD6", bordercolor=PANEL, lightcolor="#C4CBD6",
                        darkcolor="#C4CBD6", arrowcolor=MUTED, gripcount=0, arrowsize=12)
        style.map("Card.Vertical.TScrollbar", background=[("active", ROYAL)])
        dscroll = ttk.Scrollbar(draft_box, orient="vertical", command=self.draft.yview,
                                style="Card.Vertical.TScrollbar")
        self.draft.configure(yscrollcommand=dscroll.set)
        dscroll.pack(side="right", fill="y")
        self.draft.pack(side="left", fill="both", expand=True)
        draft_box.pack(fill="both", expand=True)
        tk.Frame(card, bg="#D5DAE3", height=1).pack(fill="x", padx=20)
        scroll = ttk.Scrollbar(card, orient="vertical", command=self.text.yview,
                               style="Card.Vertical.TScrollbar")
        self.text.configure(yscrollcommand=scroll.set)
        scroll.pack(side="right", fill="y")
        self.text.pack(side="left", fill="both", expand=True)
        self.draft.bind("<<Modified>>", self._on_draft_modified)

        t = self.text.tag_configure
        t("h", font=head, foreground=ROYAL, spacing1=4, spacing3=6)
        t("h_bad", font=head, foreground=RED, spacing1=14, spacing3=4)
        t("h_warn", font=head, foreground=AMBER, spacing1=14, spacing3=4)
        t("h_dim", font=head, foreground=MUTED, spacing1=14, spacing3=4)
        t("bad", foreground=RED, font=ui, lmargin1=4, lmargin2=20, spacing3=3)
        t("warn", foreground=AMBER, font=ui, lmargin1=4, lmargin2=20, spacing3=3)
        t("dim", foreground=MUTED, font=ui)
        t("orig", foreground="#3C4043", font=ui, spacing2=2)
        t("note", foreground=MUTED, font=small, spacing1=16)
        self._show([("Press Record, or ⌘W, and speak.\n", "dim")])

        # --- actions --------------------------------------------------------
        act = tk.Frame(r, bg=BG)
        act.pack(fill="x", side="bottom", padx=16, pady=(10, 14))
        card.pack(fill="both", expand=True, padx=16)

        tk.Checkbutton(
            act, text="Auto-paste safe results", variable=self.auto_paste,
            command=self._update_paste_label, bg=BG, fg=FG, font=small,
            activebackground=BG, activeforeground=FG,
            highlightthickness=0, bd=0,
        ).pack(side="left")

        self.paste_btn = FlatButton(act, text="Paste", command=self.paste_now,
                                    style="primary", font=ui_bold, padx=20, pady=7,
                                    state="disabled")
        self.paste_btn.pack(side="right", padx=(8, 0))
        self.copy_btn = FlatButton(act, text="Copy", command=self.copy_now,
                                   style="secondary", font=ui, padx=16, pady=7,
                                   state="disabled")
        self.copy_btn.pack(side="right")
        # Only enabled for a rewrite: the way back to exactly what was said.
        self.orig_btn = FlatButton(act, text="Paste original", command=self.paste_original,
                                   style="secondary", font=ui, padx=16, pady=7,
                                   state="disabled")
        self.orig_btn.pack(side="right", padx=(0, 8))
        # Rewrite whatever is in the draft now, whichever mode it came from.
        self.rw_btn = FlatButton(act, text="Rewrite", command=self.rewrite_now,
                                 style="secondary", font=ui, padx=16, pady=7,
                                 state="disabled")
        self.rw_btn.pack(side="right", padx=(0, 8))

        self._set_draft("", "")
        self._update_rewrite_label()
        self._update_paste_label()
        self._update_target_label()
        self._update_mic_label()

        # Cmd+W, replacing macOS's usual close-window meaning in this app only.
        #
        # A key binding alone is not enough: Tk's default Aqua menu bar owns
        # Cmd+W as "Close" and handles it natively, before any binding runs,
        # so the window closed and the binding never fired. Installing our own
        # menu bar removes that default. The accelerator shown on the menu item
        # is a label only in Tk, so the binding below still does the work and
        # a keystroke toggles exactly once (verified with a synthetic Cmd+W).
        menubar = tk.Menu(r)
        dictation = tk.Menu(menubar, tearoff=0)
        dictation.add_command(label="Start / Stop Recording",
                              accelerator="Command-W", command=self.toggle)
        dictation.add_command(label="Rewrite On / Off", command=self.toggle_rewrite)
        menubar.add_cascade(label="Dictation", menu=dictation)
        r.config(menu=menubar)
        for seq in ("<Command-w>", "<Command-W>"):
            r.bind(seq, lambda e: (self.toggle(), "break")[1])
        r.bind("<Escape>", lambda e: self.root.quit())

    def toggle_rewrite(self) -> None:
        self.set_rewrite(not self.rewrite_on)

    def set_rewrite(self, on: bool) -> None:
        if on and self.pipeline.rewriter is None:
            self.status.configure(text="Rewrite unavailable — no model configured", fg=AMBER)
            return
        self.rewrite_on = on
        self._update_rewrite_label()

    def _update_rewrite_label(self) -> None:
        on = self.rewrite_on
        sel = dict(bg="#FFFFFF", fg=NAVY)
        off = dict(bg=NAVY, fg=HATTERAS)
        self.seg_raw.configure(**(off if on else sel))
        self.seg_rw.configure(**(sel if on else off))
        if self.pipeline.rewriter is None:
            self.seg_rw.configure(fg="#6F7FA8", cursor="arrow")
        pol = f"polish {self.model}" if self.polishing_on else "polish off"
        if on:
            text = f"Rewrite · {self.rewrite_model} · email and research only"
        else:
            text = f"Raw transcript · {self.mode} · {pol} · VAD on"
        self.mode_lbl.configure(text=text)

    def _update_paste_label(self) -> None:
        on = self.auto_paste.get()
        self.paste_lbl.configure(
            text="auto-paste: safe only" if on else "auto-paste: off",
            fg=AMBER if on else GREEN,
        )

    def _update_target_label(self) -> None:
        t = self.focus.target
        self.target_lbl.configure(
            text=f"→ {t}" if t else "→ no target",
            fg=HATTERAS if t else DANDELION,
        )

    def _update_mic_label(self) -> None:
        d = self.device
        self.mic_lbl.configure(
            text=f"🎙 {d.name}" if d else "🎙 resolving…",
            fg=HATTERAS if d else DANDELION,
        )

    def _show(self, chunks: list[tuple[str, str]]) -> None:
        """Replace the read-only notes pane. Never touches the draft."""
        self.text.configure(state="normal")
        self.text.delete("1.0", "end")
        for body, tag in chunks:
            self.text.insert("end", body, tag)
        self.text.configure(state="disabled")

    def _set_draft(self, text: str, label: str, editable: bool = True) -> None:
        self.draft.configure(state="normal")
        self.draft.delete("1.0", "end")
        self.draft.insert("1.0", text)
        self.draft.edit_reset()
        self.draft.edit_modified(False)
        self.draft.configure(state="normal" if editable else "disabled")
        self.draft_lbl.configure(text=f"{label}  ·  click to edit" if label and editable else label)
        self._update_action_buttons()

    def _draft_text(self) -> str:
        return self.draft.get("1.0", "end-1c").strip()

    def _on_draft_modified(self, _evt=None) -> None:
        if self.draft.edit_modified():
            self.draft.edit_modified(False)
            self._update_action_buttons()

    def _update_action_buttons(self) -> None:
        has = bool(self._draft_text()) and not (self.recording or self.busy)
        for b in (self.copy_btn, self.paste_btn):
            b.configure(state="normal" if has else "disabled")
        rw_ok = has and self.pipeline.rewriter is not None
        self.rw_btn.configure(state="normal" if rw_ok else "disabled")

    # ------------------------------------------------------------ record

    def toggle(self) -> None:
        if self.busy:
            return
        self.stop() if self.recording else self.start()

    def _refresh_device(self) -> None:
        """Re-resolve the microphone by name. Safe to call off the UI thread."""
        self.device = audio_devices.resolve(self.prefer_mic)

    def _resume_music(self) -> None:
        """Resume exactly what we paused. Idempotent, so it is safe on every
        exit path -- a take that fails must not leave music stopped."""
        if self._paused_players:
            media.resume(self._paused_players)
            self._paused_players = []

    def start(self) -> None:
        # Resolve now if the background pass has not landed yet. Recording
        # against a stale or unknown index is what selected a silent virtual
        # driver before.
        if self.device is None:
            self._refresh_device()
        if self.device is None:
            self.status.configure(text="No microphone", fg=RED)
            self._show([
                ("No usable microphone found.\n\n", "bad"),
                ("Every avfoundation input is a virtual driver, or ffmpeg "
                 "could not list devices. Nothing was recorded.\n", "dim"),
            ])
            return

        (REPO_ROOT / "runtime/tmp").mkdir(parents=True, exist_ok=True)
        fd, name = tempfile.mkstemp(suffix=".wav", prefix="gui.",
                                    dir=REPO_ROOT / "runtime/tmp")
        os.close(fd)
        self.audio_path = Path(name)

        # Before capture, not after: on a Bluetooth headset, opening the
        # microphone drops the link out of A2DP anyway, and music playing into
        # the first half-second of a recording is transcribable.
        if self.pause_music:
            self._paused_players = media.pause_playing()

        self.proc = subprocess.Popen(
            ["ffmpeg", "-nostdin", "-loglevel", "error", "-f", "avfoundation",
             "-i", f":{self.device.index}", "-ar", "16000", "-ac", "1",
             "-c:a", "pcm_s16le", "-y", str(self.audio_path)],
            stderr=subprocess.PIPE,
        )
        time.sleep(0.4)
        if self.proc.poll() is not None:
            err = (self.proc.stderr.read() or b"").decode(errors="replace")
            self._show([("Recording failed to start.\n\n", "bad"), (err[-400:], "dim")])
            self.audio_path.unlink(missing_ok=True)
            self._resume_music()   # never leave music paused on a failed take
            return

        self.recording = True
        self.button.configure(text="■  Stop", bg=RED)
        self.status.configure(text="Recording…", fg=RED)
        self._set_draft("", "", editable=False)
        self._show([("Listening.\n", "dim")])
        self.orig_btn.configure(state="disabled")

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

        # Resume as soon as capture ends, not after transcription -- there is
        # no reason to sit in silence while whisper runs.
        self._resume_music()
        # Devices may have changed while recording; refresh for the next take.
        threading.Thread(target=self._refresh_device, daemon=True).start()

        self.button.configure(text="●   Record", bg=BLUE_BTN_DIM, state="disabled")
        rewrite = self.rewrite_on
        self.status.configure(
            text="Transcribing and rewriting…" if rewrite else "Transcribing…", fg=BLUE)
        self._show([("Working…\n", "dim")])
        self._update_action_buttons()

        # ffmpeg buffers the WAV until close; give it a moment, then run the
        # pipeline off the UI thread. The rewrite choice is fixed here, at
        # stop, so toggling while it works cannot change what comes back.
        threading.Thread(target=self._work, args=(self.audio_path, rewrite),
                         daemon=True).start()

    def rewrite_now(self) -> None:
        """Rewrite the draft as it stands, including any edits (ADR 0002).

        Lets a Raw take be rewritten without re-dictating. The result is a
        rewrite like any other: shown for review, pasted only on a click.
        """
        source = self._draft_text()
        if self.busy or self.recording or not source or self.pipeline.rewriter is None:
            return
        self.busy = True
        self.button.configure(bg=BLUE_BTN_DIM, state="disabled")
        self.status.configure(text="Rewriting…", fg=BLUE)
        self._show([("Rewriting…\n", "dim")])
        self._set_draft(source, "", editable=False)

        def work() -> None:
            try:
                self.events.put(("done", self.pipeline.rewrite_text(source)))
            except Exception as exc:  # noqa: BLE001
                self.events.put(("error", f"{type(exc).__name__}: {exc}"))

        threading.Thread(target=work, daemon=True).start()

    def _work(self, path: Path, rewrite: bool = False) -> None:
        time.sleep(0.4)
        try:
            result = self.pipeline.run(path, rewrite=rewrite)
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
                    self.button.configure(state="normal", bg=BLUE_BTN)
                    self.status.configure(text="Error", fg=RED)
                    self.draft.configure(state="normal")
                    self._update_action_buttons()
                    self._show([(str(payload), "bad")])
        except queue.Empty:
            pass
        self._update_target_label()
        self._update_mic_label()
        self.root.after(80, self._drain)

    def _render(self, result) -> None:
        self.busy = False
        self.result = result
        self.button.configure(state="normal", bg=BLUE_BTN)

        if result.outcome in (Outcome.NO_SPEECH, Outcome.FAILED):
            self._set_draft("", "")
        if result.outcome is Outcome.NO_SPEECH:
            self.status.configure(text="No speech detected", fg=AMBER)
            self._show([("NOTHING TRANSCRIBED\n", "h_warn"),
                        ("The voice-activity gate found no speech, so the model "
                         "was not run. This is deliberate: Whisper invents text "
                         "from silence.\n", "dim")])
            return

        if result.outcome is Outcome.FAILED:
            self.status.configure(text="Failed", fg=RED)
            self._show([(result.error or "unknown error", "bad")])
            return

        if result.rewritten:
            self._render_rewrite(result)
            return

        text = result.text_to_use
        self._set_draft(text, "TRANSCRIPT")

        if result.outcome is Outcome.SAFE:
            self.status.configure(text=f"Safe · {result.transcribe_ms} ms", fg=GREEN)
            chunks = []
            if result.warnings:
                chunks.append(("NOTES\n", "h_warn"))
                chunks += [(f"•  {w}\n", "warn") for w in result.warnings]
            chunks.append(("Pasted. Edit above and Paste again if needed.\n"
                           if self.auto_paste.get() else
                           "On the clipboard. Edit above, then Paste or Copy.\n", "note"))
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

        chunks: list[tuple[str, str]] = []
        if result.protected_changes:
            chunks.append(("PROTECTED CHANGES\n", "h_bad"))
            for c in result.protected_changes:
                chunks.append((f"•  [{c.category}] {c.detail}\n", "bad"))
        if result.warnings:
            chunks.append(("WARNINGS\n", "h_warn"))
            for w in result.warnings:
                chunks.append((f"•  {w}\n", "warn"))
        chunks.append(("Not pasted. Read it, edit above if needed, then Paste "
                       "or Copy.\n", "note"))
        self._show(chunks)
        self._copy(text)

    def _render_rewrite(self, result) -> None:
        """Rewrite first, original beneath, then what moved. Never pasted here."""
        secs = (result.transcribe_ms + result.polish_ms) / 1000
        self.status.configure(text=f"Rewrite ready — review · {secs:.1f} s", fg=AMBER)
        self.orig_btn.configure(state="normal")

        # Rewrite mode is for email and research only (ADR 0002). When the
        # dictation reads like a clinical note, say so before anything else.
        clinical = [w for w in result.warnings if w.startswith("looks clinical")]
        notes = [w for w in result.warnings if not w.startswith("looks clinical")]
        chunks: list[tuple[str, str]] = []
        if clinical:
            self.status.configure(text="Looks clinical — use Raw", fg=RED)
            chunks.append(("LOOKS CLINICAL — USE RAW FOR CLINICAL NOTES\n", "h_bad"))
            for w in clinical:
                chunks.append((f"•  {w}\n", "bad"))
        self._set_draft(result.polished_text, "REWRITE")
        if result.protected_changes:
            chunks.append(("CHECK AGAINST WHAT YOU SAID\n", "h_bad"))
            for c in result.protected_changes:
                chunks.append((f"•  {c.detail}\n", "bad"))
        if notes:
            chunks.append(("NOTES\n", "h_warn"))
            for w in notes:
                chunks.append((f"•  {w}\n", "warn"))
        chunks.append(("WHAT YOU SAID\n", "h_dim"))
        chunks.append((result.raw_text + "\n", "orig"))
        chunks.append(("Not pasted. Read the rewrite, edit above if needed, then "
                       "Paste — or Paste original.\n", "note"))
        self._show(chunks)
        self._copy(result.polished_text)

    # ------------------------------------------------------------ clipboard

    def _copy(self, text: str) -> None:
        """Copy marked concealed/transient, and schedule its removal."""
        self._clip_count = clipboard.copy(text)
        self._schedule_clipboard_clear(CLIPBOARD_TTL_MS)

    def _schedule_clipboard_clear(self, delay_ms: int) -> None:
        if getattr(self, "_clip_job", None):
            self.root.after_cancel(self._clip_job)
        count = getattr(self, "_clip_count", None)
        self._clip_job = self.root.after(
            delay_ms, lambda: clipboard.clear_if_unchanged(count)) if count else None

    def clear_clipboard_now(self) -> None:
        clipboard.clear_if_unchanged(getattr(self, "_clip_count", None))

    # Copy and Paste use the draft as it stands, edits included: what the
    # user sees in the box is exactly what lands in the target app.

    def copy_now(self) -> None:
        text = self._draft_text()
        if text:
            self._copy(text)
            self.status.configure(text="Copied", fg=GREEN)

    def paste_now(self) -> None:
        text = self._draft_text()
        if text:
            self._paste(text)

    def paste_original(self) -> None:
        if self.result and self.result.raw_text:
            self._paste(self.result.raw_text)

    def _paste(self, text: str) -> None:
        """Copy, then send ⌘V to the application the user came from.

        The window is NOT hidden. A previous implementation called withdraw()
        to yield focus, on the theory that macOS would hand frontmost status
        back to the previous app. It does not: measured 0/9 delivery at sleeps
        from 250 ms to 1200 ms, because the keystroke went to our own hidden
        window and was discarded. osascript still exited 0, so the UI reported
        a green "Pasted" for a paste that never happened. Activating the
        target explicitly is what actually delivers, and it removes the
        window-blink at the same time.

        The text is always on the clipboard first, so every failure path below
        still leaves the user a working ⌘V.
        """
        self._copy(text)

        target = self.focus.target
        if not target:
            # Fail closed. Sending ⌘V to an unknown frontmost app is how
            # clinical text ends up in the wrong window.
            self.status.configure(text="Not pasted — no target app", fg=AMBER)
            self._show([
                ("Not pasted: no target application known yet.\n", "warn"),
                ("Click into the app you want this in, then press Paste.\n"
                 "The text is on the clipboard for 2 minutes; ⌘V works.\n", "dim"),
            ])
            return

        status, detail = paste_into(target)

        if status == "pasted":
            self.status.configure(text=f"Pasted → {target}", fg=GREEN)
            self._schedule_clipboard_clear(CLIPBOARD_AFTER_PASTE_MS)
        elif status == "abort":
            where = "it quit" if detail == "gone" else f"focus was {detail}"
            self.status.configure(text=f"Not pasted — {where}", fg=AMBER)
            self._show([
                (f"Not pasted: expected {target} to be frontmost but {where}.\n", "warn"),
                ("Nothing was typed anywhere. The text is on the clipboard "
                 "for 2 minutes; ⌘V works.\n", "dim"),
            ])
        elif "not allowed" in detail or "1002" in detail:
            self.status.configure(text="Paste blocked — grant Accessibility", fg=RED)
            self._show([
                ("Pasting needs Accessibility permission.\n", "bad"),
                ("System Settings → Privacy & Security → Accessibility, then "
                 "add WhisperN — or, if you launched this from a terminal, "
                 "that terminal.\n\n", "dim"),
                ("The text is on the clipboard for 2 minutes; ⌘V works.\n", "dim"),
            ])
        else:
            self.status.configure(text="Paste failed", fg=RED)


def main() -> int:
    root = tk.Tk()
    app = WhisperNApp(root)
    try:
        root.mainloop()
    finally:
        app.focus.stop()
        # Quitting mid-recording must not leave music paused for good.
        app._resume_music()
        # Do not leave dictated text behind on the clipboard after quitting.
        app.clear_clipboard_now()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
