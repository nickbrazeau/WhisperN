# Hammerspoon hotkey shell

Hold **FN** to record, release to transcribe. The result is validated and
copied to the clipboard. **Nothing is ever pasted automatically.**

This shell is intentionally thin: it starts and stops a recording and calls
`scripts/dictate.py`, which owns every decision about clinical text.

---

## Setup

### 1. Link the config

```bash
mkdir -p ~/.hammerspoon
ln -sf ~/AgentWorkspaces/WhisperN/hammerspoon/init.lua ~/.hammerspoon/init.lua
```

A symlink, so edits in the repo take effect on reload.

### 2. Turn off Apple's dictation

Required. Otherwise macOS acts on FN at the same time.

**System Settings → Keyboard → Dictation → off.**

Currently on (`com.apple.assistant.support "Dictation Enabled" = 1`).

Then set FN to do nothing on its own:

**System Settings → Keyboard → "Press 🌐 key to" → Do Nothing.**

### 3. Grant Accessibility to Hammerspoon

**System Settings → Privacy & Security → Accessibility → enable Hammerspoon.**

Unavoidable for FN. FN arrives as a `flagsChanged` event rather than a key
press, so it cannot be bound through Carbon `RegisterEventHotKey`; watching it
needs an event tap, and event taps require Accessibility. A conventional chord
such as ⌥Space would not — see §Alternatives.

Accessibility lets Hammerspoon observe all keyboard events. `init.lua` reads
only the FN flag and returns `false` so events are never swallowed, but the
permission itself is broad. That is a real trade, made deliberately.

### 4. Grant Microphone to Hammerspoon

macOS will prompt on the first recording, because `ffmpeg` runs as its child.

### 5. Reload

Hammerspoon menu bar → **Reload Config**. Expect
`WhisperN ready — hold FN to dictate`.

---

## Use

| Step | What happens |
|---|---|
| Hold FN | Red `● Recording` indicator |
| Speak | — |
| Release FN | Blue `Transcribing…`, ~2 s |
| Result | Outcome below |

| Outcome | Behaviour |
|---|---|
| `safe_to_paste` | Brief alert with the text; copied to clipboard |
| `review_required` | **Modal dialog** with raw text and every warning; copied |
| `rejected` | Modal dialog; raw transcript used |
| `no_speech` | `No speech detected`; nothing copied |

"Safe" means **no protected content changed** — not that every word is right.
Named-entity accuracy is 62.5 %, so roughly one distinctive drug or organism
name in three is wrong. Read what you paste.

---

## Configuration

Edit `init.lua`:

| Setting | Default | Note |
|---|---|---|
| `AUDIO_DEVICE` | `"0"` | avfoundation index. **Not stable** — renumbers when devices connect. Check with `scripts/record_baseline.sh --list` |
| `MIN_HOLD` | `0.35` | Below this, the recording is discarded as a key tap |
| `REPO` | `~/AgentWorkspaces/WhisperN` | Change if the repo moves |

---

## Alternatives to FN

FN costs Accessibility and requires disabling Apple dictation. To avoid both,
replace the `fnWatcher` block with:

```lua
hs.hotkey.bind({"alt"}, "space", startRecording, stopRecording)
```

`hs.hotkey.bind` uses Carbon `RegisterEventHotKey`, which needs no
Accessibility. Its third argument fires on release, giving the same
hold-to-talk behaviour.

---

## Why there is no paste

Deliberate, and it is the whole reason this shell is thin.

- CLAUDE.md §11: do not skip to automatic pasting.
- CLAUDE.md §4.2: never auto-paste a `review_required` result in
  `clinical_strict`.
- ~1 dictation in 8 is flagged for review (`docs/phase6-results.md`).
- The validator cannot detect a *real* word used wrongly, so even a
  `safe_to_paste` result can carry a wrong drug name
  (`docs/validation-report.md` §3.2).

`hs.eventtap.keyStrokes` does not appear in `init.lua`. Before adding it, read
`docs/hotkey-integration-options.md` §5 — the staged plan exists so the review
rate can be measured in real use first.

---

## Troubleshooting

| Symptom | Cause |
|---|---|
| FN does nothing | Accessibility not granted, or config not reloaded |
| macOS dictation opens too | Apple Dictation still on (step 2) |
| "Too short" every time | `MIN_HOLD` too high, or FN not held |
| Recording fails immediately | Wrong `AUDIO_DEVICE`; run `scripts/record_baseline.sh --list` |
| Nothing transcribes | Check `REPO` path; run `scripts/dictate.py --file x.wav --json` directly |

Hammerspoon Console (menu bar → Console) shows Lua errors.
