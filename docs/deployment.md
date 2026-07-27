# Deployment and Rollback Guide

CLAUDE.md §11 deliverable 13. Covers what to deploy, how to verify it, how to
roll back, and what to do when a stage fails.

Last updated: 2026-07-27.

---

## 1. What is being deployed

A **raw-transcript dictation pipeline with review**. Polishing is disabled
(ADR 0001), so no model rewrites clinical text.

```
microphone → VAD gate → whisper.cpp → normalize → validate → review
```

There is **no paste-at-cursor path**. Output is displayed, and optionally
copied to the clipboard as an explicit user action.

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

### 5.5 Full revert

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

### 6.3 Adding a paste path

Not implemented, deliberately. CLAUDE.md §11 says not to skip to automatic
pasting. Before building one, note that pasting `safe_to_paste` output would
still paste text containing a mis-transcribed drug name roughly one time in
three, because the validator cannot detect a *real* word used wrongly
(`docs/validation-report.md` §3.2).

---

## 7. Failure handling

Matches CLAUDE.md §9.

| Failure | Behaviour | What to do |
|---|---|---|
| Transcription fails | Error returned; no cloud attempt; temporary audio deleted | Retry once; check `scripts/record_baseline.sh --check` |
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
