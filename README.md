# WhisperN — Local Medical Dictation

A fully local, privacy-preserving medical dictation pipeline. Audio, transcripts,
prompts, and dictionaries never leave the machine. There is no cloud fallback,
by design.

See [`CLAUDE.md`](CLAUDE.md) for the full specification, constraints, and phased
execution plan. **`CLAUDE.md` is the authority**; this README is orientation only.

> This is a dictation and editing tool. It is not a diagnostic system, clinical
> decision-support system, or autonomous medical documentation system. The user
> remains responsible for all text entered into any clinical record.

---

## Status

| Phase | Description | State |
|---|---|---|
| 0 | Inspect and document the machine | Complete — [`docs/environment.md`](docs/environment.md) |
| 1 | Local raw transcription | Complete — [`docs/baseline-errors.md`](docs/baseline-errors.md) |
| 2 | Medical terminology in ASR | Complete — in-sample only; [does not generalize](docs/phase2-results.md#8a-held-out-evaluation--the-prompt-does-not-generalize) |
| 3 | Constrained medical polishing | Built, **deferred** — [ADR 0001](docs/decisions/0001-defer-local-polishing-model.md) |
| 4 | Protected-token validator | Complete — [40/40 tests, 100% precision](docs/phase4-results.md) |
| 5 | Desktop integration | Complete — VAD gate, review interface, global ⌘FN hotkey, paste at cursor or anchor |
| 6 | Synthetic medical test suite | Complete — [gate passed](docs/phase6-results.md); named-entity accuracy 62.5% |
| 7 | Offline and privacy verification | Complete — [16/16 checks](docs/validation-report.md) |
| 8 | Deployment | Checklist complete — [guide](docs/deployment.md); glossary reviewed 2026-07-27 |

**Usable for raw-transcript dictation. Read what it pastes.**
`scripts/dictate.py` records, gates on voice activity, transcribes, validates,
and presents the result for review. Polishing is deliberately disabled
([ADR 0001](docs/decisions/0001-defer-local-polishing-model.md)).

Pasting is enabled only for `safe_to_paste` results, which is what CLAUDE.md
§4.2 permits — `review_required` and `rejected` open a modal dialog and take a
second, explicit click. "Safe" means no protected content changed; it does not
mean every word is right. Named-entity accuracy is 62.5% and ~1 dictation in 8
is flagged for review, so start with email and research dictation rather than
direct entry into a record (CLAUDE.md §6 Phase 8).

---

## Setup

Requires macOS on Apple Silicon.

```bash
brew install cmake

git clone --depth 1 https://github.com/ggml-org/whisper.cpp.git
cd whisper.cpp
sh ./models/download-ggml-model.sh large-v3-turbo
cmake -B build
cmake --build build -j --config Release
cd ..

python3 -m venv .venv
```

`whisper.cpp/`, `models/`, `.venv/`, and `runtime/` are excluded from git.

Setup needs network access to fetch the model and dependencies. **Runtime does
not** — that is the point, and it is verified in §Offline below.

---

## Usage

Desktop app — record, review, and paste:

```bash
scripts/whispern_app.py
```

Space to start/stop. Safe results paste at the cursor; anything needing review
is shown and must be pasted deliberately.

Global hotkey, no window — [`hammerspoon/`](hammerspoon/README.md):

| Key | Does |
|---|---|
| **⌘ FN** | Toggle recording: press to start, press again to stop |
| **⌘ ⌃ A** | Anchor the paste destination to the current app, window, and text field; press again there to clear |
| **⌘ ⌃ ⇧ A** | Clear the anchor |

With no anchor, text pastes into the application that was frontmost when
recording started — and only if it still is, otherwise the paste is blocked and
the text is left on the clipboard.

Terminal dictation:

```bash
scripts/dictate.py --copy
```

Transcribe an audio file. Any format macOS can decode; it is converted to
16 kHz mono internally and the temporary audio is deleted on exit.

```bash
scripts/transcribe_local.sh path/to/audio.m4a
```

Generate reproducible synthetic test audio (offline TTS, no human voice):

```bash
scripts/make_synthetic_audio.sh tests/fixtures/synthetic/baseline_phrases.txt runtime/tmp/baseline
```

Score transcripts against references:

```bash
.venv/bin/python scripts/score_transcripts.py runtime/tmp/baseline
```

Build the ASR context prompt and transcribe with terminology bias:

```bash
.venv/bin/python scripts/build_asr_prompt.py --mode infectious_diseases \
    --out runtime/tmp/asr_prompt.txt --stats
scripts/transcribe_local.sh audio.wav --prompt-file runtime/tmp/asr_prompt.txt
```

Validate polished text against a raw transcript (deterministic, no model):

```bash
scripts/validate_changes.py --raw raw.txt --polished polished.txt
# exit 0 = safe_to_paste, 1 = review_required, 2 = rejected
```

Verify offline operation and privacy (16 empirical checks):

```bash
scripts/offline_smoke_test.sh
```

Run the Phase 6 acceptance benchmark:

```bash
scripts/benchmark.py runtime/tmp/corpus_prompted2 runtime/tmp/heldout_syn
```

Evaluate a candidate polishing model against the adversarial cases:

```bash
scripts/eval_polisher.py --model qwen2.5:14b
```

Rebuild the generated dictionaries from downloaded sources (setup-time only,
needs network):

```bash
scripts/build_dictionaries.py --ndc product.txt --taxonomy taxdump/ --out dictionaries/
```

---

## Offline verification

Transcription is verified to work with network access denied to the process
itself, using `sandbox-exec` rather than merely turning off Wi-Fi:

```bash
printf '(version 1)\n(allow default)\n(deny network*)\n' > /tmp/no-network.sb

# confirm the profile actually blocks traffic
sandbox-exec -f /tmp/no-network.sb curl -s -m 5 https://example.com   # must fail

# transcribe with no network
sandbox-exec -f /tmp/no-network.sb bash scripts/transcribe_local.sh some.wav
```

Output is byte-identical to the networked run. A fuller privacy audit
(`scripts/offline_smoke_test.sh`) arrives in Phase 7.

---

## Privacy notes

- Temporary audio is removed via a `trap` on `EXIT INT TERM HUP`, so it is
  cleaned up on error and signal paths, not only on success.
- `runtime/` and all audio/transcript file patterns are gitignored.
- `dictionaries/institutions_and_people.local.txt` is gitignored — it may
  contain names or locally identifying vocabulary.
- Do not commit audio, transcripts, or patient-identifiable text. Treat
  clipboard history, crash reports, and shell history as leakage paths.

---

## Layout

```
config/         app + validation configuration
dictionaries/   curated medical terminology
prompts/        the constrained polishing prompt
scripts/        pipeline entry points
src/            pipeline, privacy, validation, integrations
tests/          synthetic fixtures and tests
runtime/        temp + audit working dirs (gitignored)
docs/           environment, baseline, validation reports
```
