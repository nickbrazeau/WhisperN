# Baseline Transcription Errors — Phase 1

Measured: 2026-07-25
Purpose: record raw `whisper.cpp` behaviour **before** any medical terminology,
dictionary, prompt, or polishing work. Per CLAUDE.md §6 Phase 1, the text was
not manually improved before measuring.

---

## 1. Configuration under test

| Property | Value |
|---|---|
| Engine | `whisper.cpp`, ggml version 0.16.0, commit `080bbbe` |
| Model | `ggml-large-v3-turbo.bin` (1 549 MiB) |
| Model source | `https://huggingface.co/ggerganov/whisper.cpp` via `models/download-ggml-model.sh` |
| Language | `en` (forced) |
| Decoding | 5 beams, best-of 5 (whisper.cpp defaults) |
| Initial prompt | **none** — this is the unassisted baseline |
| Backends | Metal (GPU), Accelerate BLAS, NEON, ARM_FMA, FP16_VA, DOTPROD |
| Threads | 4 of 8 |
| Hardware | Apple M1 Pro, 16 GB (see `docs/environment.md`) |

---

## 2. Audio source and its limitations

Audio was generated with the macOS offline speech synthesiser (`say`, voice
`Samantha`), then converted to 16 kHz mono PCM with `afconvert`. Generation is
reproducible via:

```bash
scripts/make_synthetic_audio.sh tests/fixtures/synthetic/baseline_phrases.txt runtime/tmp/baseline
```

**This baseline is optimistic and must not be quoted as real-world accuracy.**
Synthesised speech has no disfluency, no accent, no hesitation, no variable
pacing, no room noise, and no microphone or codec loss. It also pronounces
medical terms from a general-purpose lexicon, which is itself a source of
mismatch in either direction.

Its value is as a **deterministic regression harness**: identical input every
run, so a change in output is attributable to a change we made.

A real baseline on the user's own voice is still outstanding. It should be
recorded on the **built-in microphone**, not the Bluetooth headset — see
`docs/environment.md` §2 for why the Bluetooth path is expected to be worse.

---

## 3. Results

5 utterances, 42 reference words.

| Metric | Value |
|---|---|
| Literal WER (surface comparison) | **7.14 %** (3 errors) |
| Semantic WER (spelled numbers mapped to digits) | **2.38 %** (1 error) |
| Utterances transcribed exactly | 3 of 5 |
| Medical-term errors | **1** |

Scored with `scripts/score_transcripts.py`.

### Per-utterance

| # | Result | Note |
|---|---|---|
| 001 | exact | — |
| 002 | exact | `methicillin-susceptible Staphylococcus aureus` correct, unassisted |
| 003 | **1 substitution** | medical term error |
| 004 | 2 substitutions | numeral formatting only |
| 005 | exact | negation (`no evidence of`) preserved |

---

## 4. Error analysis

### 4.1 Medical term error — the real defect

```
ref: The differential includes pyelonephritis, nephrolithiasis, and musculoskeletal pain.
hyp: The differential includes pylonephritis,   nephrolithiasis, and musculoskeletal pain.
                                  ^^^^
```

`pyelonephritis` → `pylonephritis`. The `ye` syllable was dropped.

This is a genuine speech-recognition error on a common medical term and is
exactly the class of problem Phase 2 targets. Notably the model got the *harder*
neighbouring terms right (`nephrolithiasis`, `musculoskeletal`) and got
`methicillin-susceptible Staphylococcus aureus` right in utterance 002 with no
assistance at all — so the failure is specific, not a general weakness.

`pylonephritis` is not a word. That makes it a comparatively safe candidate for
a Phase 2 dictionary entry or exact replacement, since there is no plausible
alternative interpretation to collide with. It still requires the evidence and
approval process in CLAUDE.md §8 before activation.

### 4.2 Numeral formatting — not an error, but consequential

```
ref: Continue cefazolin two grams intravenously every eight hours.
hyp: Continue cefazolin 2 grams   intravenously every 8 hours.
```

Whisper renders spoken number words as digits. **The clinical value is
unchanged** — this is a formatting difference, and it accounts for the entire
gap between the 7.14 % literal and 2.38 % semantic WER.

It is recorded here because it has a direct design consequence for **Phase 4**:

> The protected-token validator compares raw against polished text. It must
> treat `two` and `2` as the same *value* while still rejecting any change to
> the value itself. A naïve string comparison of protected numbers would fire
> on every dose in every dictation, and an alert that always fires gets
> ignored — which would defeat the protection it exists to provide.

The `--semantic` scoring path in `scripts/score_transcripts.py` implements the
number-word mapping used above. That table exists for *scoring* only; it must
not be reused to rewrite clinical text.

### 4.3 What was already correct

Worth recording, because these are the things that most need protecting and the
baseline engine already handles them:

- Negation: `There is no evidence of endocarditis` — preserved exactly.
- Organism name: `methicillin-susceptible Staphylococcus aureus` — exact,
  including hyphenation and capitalisation, with no dictionary assistance.
- Laterality: `left flank pain` — preserved.
- Medication name: `cefazolin` — correct.
- Route/frequency: `intravenously`, `every 8 hours` — preserved.

---

## 5. Latency

Wall-clock, end to end through `scripts/transcribe_local.sh` (includes audio
conversion, process start, model load from disk, and cleanup).

| Utterance | Latency |
|---|---|
| 001 | 3.03 s |
| 002 | 1.91 s |
| 003 | 1.81 s |
| 004 | 1.90 s |
| 005 | 2.16 s |

Median ≈ **1.91 s**; first run is slower due to cold model load.

Per whisper.cpp's own timers on an 11-second sample: 1 149 ms model load,
1 143 ms encode, 2 642 ms total compute.

**Interpretation:** roughly 1.1 s of the per-utterance cost is model load, paid
on every invocation because the script starts a fresh process. For short
dictation this is a large fraction of total latency. A persistent process
(`whisper-server`, already built) would remove it. That is a Phase 5 concern
and is **not** being changed now — it would alter the measurement conditions
mid-baseline, which CLAUDE.md §10.3 forbids.

The `medium.en` / `small.en` fallback in CLAUDE.md §2 has **not** been needed so
far; `large-v3-turbo` is comfortably fast enough on this hardware at these
utterance lengths.

---

## 6. Offline verification

Transcription was re-run with network access denied at the kernel level using
`sandbox-exec` with a `(deny network*)` profile. The profile was first confirmed
to actually block traffic (a sandboxed `curl` to an external host fails).

Utterances 002, 004, and 005 produced **byte-identical transcripts** with the
network denied.

This is stronger evidence than disabling Wi-Fi, because it denies network access
to the process itself rather than relying on the absence of a route. It satisfies
the Phase 1 review gate. The fuller privacy audit — environment variables inside
the runtime process, log inspection, fail-closed behaviour — belongs to Phase 7.

---

## 7. Temporary file handling

`scripts/transcribe_local.sh` creates a `mktemp -d` working directory under
`runtime/tmp/` and removes it via a `trap ... EXIT INT TERM HUP`, so cleanup
runs on error and signal paths, not only on success.

Verified after the baseline run: `find runtime/tmp -maxdepth 1 -name 'asr.*'`
returned nothing. No temporary audio survived.

Note that the fixture audio in `runtime/tmp/baseline/` **is** retained
deliberately — it is synthetic, contains no patient data, and is needed to
reproduce this baseline. `runtime/` is excluded from git.

---

## 8. Conclusions

1. Unassisted `large-v3-turbo` is already strong on this material: 2.38 %
   semantic WER, with 3 of 5 utterances exact.
2. There is **one** genuine medical-term error to fix (`pyelonephritis`), and it
   is a well-conditioned target.
3. The number-word/digit difference is a formatting artefact, not an error, but
   it dictates how the Phase 4 validator must compare protected numbers.
4. Local transcription provably works with no network access.
5. Latency is dominated by per-invocation model load, which is addressable later
   without touching the model.

### Caveats on this baseline

- Five utterances is far too small a sample to support a precise error rate. The
  headline figures are indicative, not statistical.
- Synthetic speech understates real-world difficulty (§2).
- One voice, one microphone path, one speaking style.
- No measurement yet on the user's actual voice or the Bluetooth input path.
