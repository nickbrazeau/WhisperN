# Deployment and Rollback Guide

CLAUDE.md §11 deliverable 13. Covers what to deploy, how to verify it, how to
roll back, and what to do when a stage fails.

Last updated: 2026-08-02.

---

## 1. What is being deployed

A **raw-transcript dictation pipeline with review**. Polishing is disabled
(ADR 0001), so no model rewrites clinical text.

```
microphone → VAD gate → whisper.cpp → normalize → validate → review
```

Three front ends:

- `scripts/dictate.py` — **clipboard only**, never pastes. The pipeline all
  three share; run it directly to score or debug without a target application.
- **Hammerspoon global hotkey** — ⌘FN toggles recording, ⌘⌃A anchors the paste
  destination. Pastes `safe_to_paste` results at the cursor or the anchor;
  `review_required` and `rejected` open an **editable** dialog needing an
  explicit click. `AUTO_PASTE = false` in `init.lua` reverts it to
  clipboard-only.
- `WhisperN.app` / `scripts/whispern_app.py` (desktop GUI) — pastes at the
  cursor, only `safe_to_paste`, only when auto-paste is ticked. See §6.3.

The bundle is built by `scripts/build_app.sh`; it is a launcher that runs the
Python from the repo, so it must be rebuilt if the repo moves. Privacy grants
(Microphone, Accessibility, Automation) attach to `WhisperN.app` rather than to
a terminal.

**Verifying the hotkey is live.** Hammerspoon logs nothing externally, so
`init.lua` writes `runtime/tmp/hammerspoon-status.txt` on load, carrying the
load time, the active keys, `AUTO_PASTE`, and the resolved microphone. After
editing the config, read that file: a stale timestamp means the reload did not
take. It never contains transcript text.

---

## 2. Deployment checklist (CLAUDE.md §6 Phase 8)

| # | Item | Status |
|---|---|---|
| 1 | Local transcription works with the network disabled | **Yes** — verified via `sandbox-exec`, `docs/validation-report.md` §1 |
| 2 | Local polishing works with the network disabled | **N/A** — polishing disabled (ADR 0001) |
| 3 | Cloud fallback is disabled | **Yes** — no cloud code path; non-loopback endpoints refused at construction |
| 4 | Temporary audio is deleted | **Yes** — verified, including after SIGTERM |
| 5 | Transcript logging is disabled | **Yes** — verified no source path logs transcript text |
| 6 | Protected-token validator is active | **Yes** — 43 tests passing |
| 7 | `clinical_strict` requires review on protected change | **Yes** — default mode; never returns `safe_to_paste` on a protected change |
| 8 | Synthetic adversarial tests pass | **Yes** — 43 validator tests; 21/21 polisher cases (unused) |
| 9 | **The user has reviewed the glossary** | **Yes — reviewed 2026-07-27** |
| 10 | The user has reviewed all exact replacements | **Vacuously satisfied** — all 4 rules are `enabled: false`; none active. See §6 before enabling any |
| 11 | Active model and configuration documented | **Yes** — §3 below |
| 12 | A rollback procedure exists | **Yes** — §5 below |
| 13 | The application visibly indicates its active mode | **Yes** — `dictate.py` prints mode, polishing, VAD, and paste state on every run |

**All items are satisfied.** Item 10 is satisfied by there being nothing
enabled to review, which is a weaker statement than a review having happened —
enabling any rule later re-opens it.

---

## 3. Configuration of record

Pin these. CLAUDE.md §10.6 asks for exact names and checksums.

| Component | Value |
|---|---|
| ASR engine | `whisper.cpp`, commit `080bbbe85230f624f0b52127f1ae1218247989f9` |
| ggml version | 0.16.0 |
| ASR model | `ggml-large-v3-turbo.bin`, 1.5 GB, sha256 `1fc70f774d38eb16…` |
| VAD model | `ggml-silero-v5.1.2.bin`, 868 KB, sha256 `29940d98d42b91fb…` |
| Backends | Metal (GPU), Accelerate BLAS, NEON, ARM_FMA, FP16_VA, DOTPROD |
| Decoding | 5 beams, best-of 5, language forced to `en` |
| ASR prompt | `infectious_diseases`, 16 terms, ~183 of 224 token budget |
| Polishing model | **none** (`polishing.enabled: false`) |
| Medications dictionary | 13,784 terms — FDA NDC Directory |
| Organisms dictionary | 7,583 terms — NCBI Taxonomy, curated genus whitelist |
| Exact replacements | 4 defined, **0 enabled** |
| Default mode | `clinical_strict` |
| Platform | macOS 15.5, Apple M1 Pro, 16 GB |

Record the git commit of this repository alongside any deployment:

```bash
git rev-parse --short HEAD
```

---

## 4. Deploying

### 4.1 Verify before use

Run all three. Any failure blocks deployment.

```bash
scripts/offline_smoke_test.sh                # expect 16 pass, 0 fail
.venv/bin/python -m pytest tests/ -q         # expect 43 passed
scripts/record_baseline.sh --check           # expect microphone OK
```

### 4.2 Build the ASR prompt

```bash
.venv/bin/python scripts/build_asr_prompt.py \
    --mode infectious_diseases --out runtime/tmp/asr_prompt_id.txt --stats
```

Regenerate this whenever a prompt-eligible dictionary changes. It is written
to gitignored `runtime/`, so it does not survive a clean checkout.

### 4.3 Use

```bash
scripts/dictate.py            # record, gate, transcribe, validate, review
scripts/dictate.py --copy     # and copy the result to the clipboard
```

Confirm the mode banner before dictating. If it does not say
`polishing : OFF`, the configuration has drifted from this document.

### 4.4 Start where the stakes are lowest

CLAUDE.md §6 Phase 8: begin with nonclinical email and research dictation. Do
not begin with direct entry into a medical record.

Given 62.5 % named-entity accuracy (`docs/validation-report.md` §3.3), **read
every dictation containing a drug or organism name before using it.** Roughly
one such name in three is wrong.

---

## 5. Rollback

Rollback is per-component. Nothing here requires reinstalling the system.

### 5.1 Disable everything, keep transcription

```bash
scripts/dictate.py --no-vad          # bypass the speech gate
```

For a fuller stand-down, use the Phase 1 path directly — no VAD, no validator:

```bash
scripts/transcribe_local.sh audio.wav
```

This is the minimal known-good configuration and depends only on
`whisper-cli`, the model, and `afconvert`.

### 5.2 Roll back a dictionary change

Dictionaries are committed separately from code (CLAUDE.md §8), so:

```bash
git log --oneline -- dictionaries/
git checkout <commit> -- dictionaries/
.venv/bin/python scripts/build_asr_prompt.py --mode infectious_diseases \
    --out runtime/tmp/asr_prompt_id.txt
```

Re-run `scripts/benchmark.py` afterwards. A dictionary change alters the ASR
prompt, and prompt changes have been measured to affect *unrelated* utterances
(`docs/phase2-results.md` §5.1).

### 5.3 Roll back a model

The previous model is not deleted by `download-ggml-model.sh`. To revert:

```bash
export WHISPER_MODEL=whisper.cpp/models/ggml-medium.en.bin
scripts/transcribe_local.sh audio.wav
```

`transcribe_local.sh` honours `WHISPER_MODEL` and `WHISPER_BIN`, so a model
can be swapped without editing code.

### 5.4 Disable the VAD gate

If the gate is suspected of discarding speech:

```bash
scripts/dictate.py --no-vad
```

Expect Whisper to emit invented text on silence again — `you`, `-`, or
`Thank you.` This is a real regression in safety, so treat it as temporary and
capture the audio that was wrongly rejected.

### 5.5 Roll back the hotkey paste

The paste path is the newest and least-exercised component. To stand it down
without losing dictation, edit `hammerspoon/init.lua`:

```lua
local AUTO_PASTE = false     -- clipboard only; review dialogs still work
```

Then reload Hammerspoon and confirm `runtime/tmp/hammerspoon-status.txt` shows
`auto_paste  false` with a current timestamp. To disable the hotkey entirely,
quit Hammerspoon; `scripts/dictate.py` and the GUI are unaffected.

### 5.6 Full revert

```bash
git log --oneline
git checkout <last-known-good-commit>
scripts/offline_smoke_test.sh
.venv/bin/python -m pytest tests/ -q
```

`runtime/` is gitignored and untouched by a checkout, so recorded corpora and
audit reports survive a revert.

---

## 6. Before enabling anything currently disabled

### 6.1 Enabling polishing

Currently `enabled: false`. Before changing it:

1. Read `docs/decisions/0001-defer-local-polishing-model.md`.
2. Run `scripts/eval_polisher.py --model <model>`. **Require 21/21.** Do not
   relax a case to make a model pass (CLAUDE.md §9).
3. Keep `send_glossary: false`. Supplying the glossary made both models tested
   measurably worse.
4. Re-run `scripts/benchmark.py`. The unsupported-content rate must remain
   zero, and with a polisher active that metric becomes meaningful rather than
   true by construction.
5. Re-run `scripts/offline_smoke_test.sh` — the fail-closed check exercises a
   real code path once a model is configured.

### 6.2 Enabling an exact replacement

Higher risk than a dictionary term: it rewrites clinical text deterministically
with no model and no review. Per CLAUDE.md §8, require a reproducible ASR
error, a narrow match, no plausible non-medical reading, no effect on numbers,
negation, dose, units, or laterality, human approval, and a rollback entry.

`config/exact_replacements.yaml` recommends **rejecting the
`septipine` → `cefepime` rule permanently**: it asserts which antibiotic was
spoken, and a different cephalosporin misheard the same way would be silently
replaced with the wrong drug.

### 6.3 The paste path

Implemented in the desktop GUI only (`scripts/whispern_app.py`). The FN-hold
Hammerspoon path remains clipboard-only and does not paste.

**What it will paste.** `safe_to_paste` results, and only when the
"Auto-paste safe results" box is ticked. `review_required` and `rejected` are
never pasted automatically under any setting; pasting those takes a second,
deliberate click after reading. This is enforced by a source-level test
(`test_gui_never_auto_pastes_unsafe_results`) because the guarantee is a
branch that a refactor could silently move.

**The standing caveat.** "Safe" means no protected token changed. It does not
mean the words are right: pasted `safe_to_paste` output still contains a
mis-transcribed drug or organism name roughly one time in three, because the
validator cannot detect a *real* word used wrongly
(`docs/validation-report.md` §3.2). Read what you paste.

**Where it pastes.** ⌘V goes to whatever macOS considers frontmost, so the app
samples which application was frontmost before it took focus, activates that
app explicitly, confirms it really is frontmost, and only then sends the
keystroke. The target is shown in the title bar (`→ Notes`).

It **fails closed**: no known target, the target having quit, or any other app
being frontmost at the instant of the keystroke means nothing is typed
anywhere and the text is left on the clipboard. That asymmetry is deliberate —
a transcript pasted into the wrong window is worse than one not pasted, and
during development a best-effort implementation did exactly that, typing test
markers into a terminal.

**Do not "fix" this by hiding the window.** An earlier implementation called
`withdraw()` to yield focus. macOS does not transfer frontmost status that
way: the keystroke reached the hidden window and was discarded, 0/9 at delays
from 250 ms to 1200 ms, while `osascript` still exited 0 so the UI reported a
green "Pasted" for a paste that never happened. Because the text is copied
first, a manual ⌘V made it look like it worked. Guarded by
`test_gui_paste_does_not_hide_the_window`.

**Measured** (2026-07-31, 10 trials pinned to TextEdit): 10/10 delivered,
0 misdirected, ~380 ms per paste. Separately verified through the real
`WhisperNApp._render` auto-paste path: status `Pasted → TextEdit`, text
delivered, and the window sampled `normal` on every tick — it is never
hidden. `offline_smoke_test.sh` still passes 16/16 with the paste path in
place, and the audit report contains no clinical terms.

### 6.4 Microphone selection and music

**The device is resolved by name, never by index.** avfoundation indices are
positional and renumber whenever a device connects. Every entry point used to
hardcode index `"0"`, and on this machine index 0 is the **Microsoft Teams
virtual driver**, which captures digital silence:

| idx | device | 2 s capture |
|---|---|---|
| 0 | Microsoft Teams Audio | `-91.0 dB` — digital zeros |
| 1 | Bose QuietComfort 35 | `mean -28.8 dB` — real signal |
| 2 | MacBook Pro Microphone | `-91.0 dB` while the headset held input |

The Teams driver is listed even when Teams is not running, so this was not a
transient. It fails safe rather than silently — the VAD gate returns "No
speech detected" rather than letting Whisper invent text from silence — but
every dictation would have been lost.

Virtual drivers (Teams, Zoom, BlackHole, Loopback, Soundflower, OBS, Krisp,
VB-Cable) are never selected. If every input is virtual, recording is refused
rather than falling back to index 0.

Inspect what would be used:

```bash
scripts/dictate.py --list-devices
```

`recording.prefer_microphone` in `config/app.yaml` chooses between `headset`
(external mic wins) and `builtin`. The GUI shows the resolved device in the
title bar, because the previous failure was invisible until a dictation came
back empty.

**Unmeasured caveat on `headset` with Bluetooth.** A Bluetooth headset
negotiates its microphone over HFP at 16 kHz narrowband — a compressed voice
codec, not a clean downsample — and drops out of A2DP while recording. It wins
on proximity and room noise and loses on codec quality. Which effect dominates
has **not** been measured against this corpus, and named-entity accuracy is
already 62.5% (`docs/validation-report.md` §3.3). If drug names degrade after
switching to a headset, try `prefer_microphone: builtin` before concluding
anything about the model. Measuring it properly means recording the corpus on
both and scoring with `scripts/score_transcripts.py`.

**Music.** `recording.pause_music` pauses Spotify and Music.app while
recording and resumes exactly those that were playing. It reads each player's
state first, so it never starts music that was already stopped, and never
launches a player that is not running. Resume runs on every exit path,
including a failed or too-short take, so a discarded recording cannot leave
music paused. Browser audio is deliberately not covered: it can only be
nudged with a media key, which cannot be read back, so resume would be a
guess.

---

## 7. Failure handling

Matches CLAUDE.md §9.

| Failure | Behaviour | What to do |
|---|---|---|
| Transcription fails | Error returned; no cloud attempt; temporary audio deleted | Retry once; check `scripts/record_baseline.sh --check` |
| Transcription fails with `libwhisper.1.dylib (no such file)` | Error returned; no cloud attempt | The repo moved, or whisper.cpp was rebuilt, and the binaries' `LC_RPATH` is stale. Re-run the `install_name_tool` repair in `docs/environment.md` §3 |
| Every dictation returns "No speech detected" | VAD gate rejects the audio; nothing transcribed | The selected input is probably a virtual driver capturing silence. Run `scripts/dictate.py --list-devices`; see §6.4 |
| Music stays paused after a dictation | — | Should not happen; resume runs on every exit path. Report it, and resume manually |
| VAD gate unavailable | **Fails open** — transcription proceeds with a warning | Confirm the VAD model is present; hallucination risk returns until fixed |
| No speech detected | `No speech detected`, nothing emitted | Normal. Do not disable the gate to "fix" it without checking the audio |
| Polishing unavailable | Raw transcript returned unchanged, marked unavailable | Nothing; polishing is disabled by default anyway |
| Validation returns `review_required` | Auto-paste blocked; raw, polished, and every protected change shown | Read it. Choose raw, edit, or discard |
| Validation returns `rejected` | Raw transcript is the result | Use the raw text |
| Model hallucination observed | — | Reject the result; add a synthetic regression test; **do not weaken the validator** (CLAUDE.md §9) |

---

## 8. Ongoing operation

- **Audio and transcripts must never be committed.** `.gitignore` covers the
  known patterns; `offline_smoke_test.sh` checks git tracking on every run.
- **Clipboard history is a leakage path.** `--copy` warns about this. Clear it
  after pasting into a record.
- **Re-run the smoke test after any dependency change** — a Homebrew upgrade
  already broke `ffmpeg` once during development.
- **Benchmark before and after every model, prompt, or dictionary change**
  (CLAUDE.md §10.4), and never combine an engine upgrade with a prompt change
  in the same measurement (§10.3).
- The glossary was reviewed on 2026-07-27. Re-review after any substantive
  dictionary change.
