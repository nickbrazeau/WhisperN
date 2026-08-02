# Hammerspoon hotkey shell

**⌘ + FN** toggles recording — press to start, press again to stop. The result
is validated, put on the clipboard, and pasted where your cursor is, or into a
destination you anchored with **⌘ ⌃ A**.

This shell is intentionally thin: it starts and stops a recording and calls
`scripts/dictate.py`, which owns every decision about clinical text.

---

## Keys

| Key | Does |
|---|---|
| **⌘ FN** | Start recording; press again to stop. Always armed |
| **⌘ ⌃ A** | Anchor the paste destination to the current app, window, and text field. Pressing it again in the same app clears the anchor |
| **⌘ ⌃ ⇧ A** | Clear the anchor from anywhere |

⌘ FN is passed through, so other applications still see both keys. ⌘⌃A and
⌘⌃⇧A are swallowed.

---

## Where the text goes

| State | Destination |
|---|---|
| No anchor | The application that was frontmost when recording started — **and only if it is still frontmost** when the text is ready |
| Anchored | The anchored application, window, and text field, whatever is frontmost now |

Both are shown live in the recording indicator (`→ Mail`, `⚓ Notes`), so a
wrong destination is visible before you speak. An active anchor also shows a
badge in the top-right corner, because with an anchor set the text lands
somewhere you are not looking.

**Why "still frontmost" matters.** ⌘V goes wherever macOS thinks the front
application is. If you start dictating in Mail and switch to Slack while it
transcribes, the paste is blocked rather than delivered to Slack. Nothing is
typed anywhere it was not confirmed; the text stays on the clipboard and ⌘V
works.

An anchor is the deliberate exception: it activates its target and restores the
text field first, which is the whole point of anchoring.

Set `AUTO_PASTE = false` in `init.lua` for clipboard-only behaviour. The review
dialogs and their explicit paste button still work.

---

## Outcomes

| Outcome | Behaviour |
|---|---|
| `safe_to_paste` | Copied, then pasted at the destination |
| `review_required` | **Modal dialog, editable.** Correct the text, then Paste or Copy only |
| `rejected` | Same editable dialog, showing the raw transcript |
| `no_speech` | `No speech detected`; nothing copied |

The review dialog's text is **editable**. A flagged result is usually right
except for one word — the validator stops on a single unrecognised term, not on
a broken sentence — so fix it there and carry on. Whatever is in the field when
you press a button is what gets pasted *and* what lands on the clipboard.

It is a single-line field, which suits a one-word correction and is cramped for
a long paragraph. For those, take **Copy only** and edit in the destination.

Only `safe_to_paste` is ever pasted without being read (CLAUDE.md §4.2).

"Safe" means **no protected content changed** — not that every word is right.
Named-entity accuracy is 62.5 %, so roughly one distinctive drug or organism
name in three is wrong, and ~1 dictation in 8 is flagged for review
(`docs/phase6-results.md`, `docs/validation-report.md` §3.2). **Read what
lands.** The validator cannot detect a real word used wrongly.

Text is delivered as one ⌘V, never as synthesised keystrokes — typing a
transcript character by character mangles it in anything with autocorrect.

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

Then set FN to do nothing on its own:

**System Settings → Keyboard → "Press 🌐 key to" → Do Nothing.**

### 3. Grant Accessibility to Hammerspoon

**System Settings → Privacy & Security → Accessibility → enable Hammerspoon.**

Needed three times over: to watch FN (it arrives as a `flagsChanged` event
rather than a key press, so it cannot go through Carbon `RegisterEventHotKey`,
and event taps require Accessibility), to send ⌘V, and to read the focused text
field when you anchor.

Accessibility lets Hammerspoon observe all keyboard events. `init.lua` reads
only the ⌘ and FN flags and returns `false` so events are never swallowed, but
the permission itself is broad. That is a real trade, made deliberately.

### 4. Grant Microphone to Hammerspoon

macOS will prompt on the first recording, because `ffmpeg` runs as its child.

### 5. Reload

Hammerspoon menu bar → **Reload Config**. Expect
`WhisperN ready — ⌘FN to record, ⌘⌃A to anchor`.

---

## Configuration

Edit `init.lua`:

| Setting | Default | Note |
|---|---|---|
| `AUTO_PASTE` | `true` | `false` = clipboard only; explicit pastes still work |
| `MIN_RECORD` | `0.35` s | Below this the take is discarded as a double-tap |
| `MAX_RECORD` | `300` s | Hard stop. A toggle can be left on by accident |
| `ACTIVATE_TIMEOUT` | `1.5` s | How long to wait for an anchored app to come forward |
| `REPO` | `~/AgentWorkspaces/WhisperN` | Change if the repo moves |

`AUDIO_DEVICE` is deliberately **not** configurable here: the microphone is
resolved by name through `dictate.py --print-device-index`, because
avfoundation indices renumber whenever a device connects. Set
`recording.prefer_microphone` in `config/app.yaml` instead, and inspect with
`scripts/dictate.py --list-devices`.

---

## Privacy

The paste path holds an application reference, a numeric window id, an
accessibility handle to a text field, and a caret offset. No window title,
document name, or field content is read, stored, or logged — the window is
identified by id precisely so no title is touched. All in memory, discarded on
reload (CLAUDE.md §3.1).

The clipboard is **not** cleared after pasting. Clipboard history is a leakage
path; clear it after pasting into a record.

---

## Alternatives to FN

FN costs Accessibility and requires disabling Apple dictation. To avoid the
second (not the first — pasting needs Accessibility regardless), replace the
`hotkeyWatcher` block with:

```lua
hs.hotkey.bind({"alt"}, "space", toggleRecording)
```

`hs.hotkey.bind` uses Carbon `RegisterEventHotKey`, which needs no event tap.

---

## Troubleshooting

| Symptom | Cause |
|---|---|
| ⌘FN does nothing | Accessibility not granted, or config not reloaded |
| macOS dictation opens too | Apple Dictation still on (step 2) |
| Recording starts by itself | Should not happen: only a press of ⌘ or FN itself is considered, so ⌘← and ⌘↑ are ignored. Check the Hammerspoon Console |
| "Too short" every time | Double-tapping the toggle, or `MIN_RECORD` too high |
| "Copied, not pasted: focus is on X" | You switched apps mid-transcription. Working as intended — ⌘V, or anchor next time |
| "Not pasted: could not bring X forward" | The anchored app would not activate within `ACTIVATE_TIMEOUT` |
| Anchor pastes into the wrong field | The app exposes no focused element; the alert says `(app only — no text field found)` when this happens at anchor time |
| Recording fails immediately | `scripts/dictate.py --list-devices` |
| Nothing transcribes | Check `REPO`; run `scripts/dictate.py --file x.wav --json` directly |

Hammerspoon Console (menu bar → Console) shows Lua errors.
