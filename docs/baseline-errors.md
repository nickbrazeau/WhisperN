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

## 7a. Real-voice baseline (33 phrases)

Measured 2026-07-25. Recorded by the user reading
`tests/fixtures/synthetic/dictation_corpus.txt` aloud at natural dictation
pace, via `scripts/record_baseline.sh`, on the **built-in MacBook Pro
microphone** (not the Bluetooth headset). Same model, same settings, no
dictionary or prompt assistance.

Utterance durations 2.2–6.4 s. Median transcription latency 2.36 s, p95 3.42 s.

### Headline

| Axis | Synthetic (TTS) | **Real voice** |
|---|---|---|
| Literal WER | 15.54 % | **21.62 %** |
| Semantic WER | 12.50 % | **19.26 %** |
| Content WER | 10.42 % | **15.31 %** |
| Exact utterances | 20 / 33 | **12 / 33** |

Real speech is roughly **47 % worse** on content WER than synthesised speech on
identical text. This confirms the caveat in §2: the synthetic harness is a
regression detector, not an accuracy estimate. Had dictionaries been tuned
against the synthetic distribution, they would have been tuned against the
wrong errors.

The five-phrase figure of 2.38 % reported in §3 is superseded and should not be
quoted. It was a small-sample artefact.

### Errors that change clinical meaning

These are the findings that matter. They are not spelling slips; in each case
Whisper produced **fluent, well-formed, wrong text**.

| # | Spoken | Transcribed | Why it matters |
|---|---|---|---|
| 015 | `oral linezolid six hundred milligrams` | `oral and nasal at 600 milligrams` | **The drug name vanished and a route was invented.** A dose survives, attached to no medication and to a route never spoken. |
| 017 | `Pseudomonas aeruginosa … cefepime` | `Pseudomonas originosa … septipine` | Organism *and* antibiotic both wrong. `septipine` is not a drug. |
| 002 | `methicillin-susceptible Staphylococcus aureus` | `methicillin susceptible **to** staphylococcus aureus` | An inserted `to` turns a susceptibility phenotype into a different claim. |
| 013 | `piperacillin-tazobactam` | `Pipercellum Tauzan Bactam` | Drug name split into three invented words. |
| 019 | `Pneumocystis jirovecii` | `pneumocystis, Jervichet` | Organism name destroyed. |
| 029 | `PCP prophylaxis` | `PCAP prophylaxis` | One abbreviation silently became a different one. |
| 028 | `in the past medical history` | `in the past **Michael Hex 3**` | **Invented proper-noun-like content** in a clinical sentence. |
| 016 | `vancomycin-resistant` | `vancomycin resistance` | Adjective to noun; alters the assertion. |
| 005 | `echocardiography` | `echocardiogram` | Modality wording changed (clinically minor). |

Note the contrast with the synthetic run, where `jirovecii` became `cerevisiae`
— a real, different organism. Both runs corrupt organism names; the *shape* of
the corruption differs, which is another reason not to tune on synthetic audio
alone.

### The pattern

Failures cluster tightly on **multi-syllable antibiotic names, organism names,
and abbreviations**:

- Antibiotics: `linezolid`, `cefepime`, `piperacillin-tazobactam`,
  `trimethoprim-sulfamethoxazole`
- Organisms: `aeruginosa`, `jirovecii`, `faecium`
- Abbreviations: `PCP` → `PCAP`

This is precisely the target of Phase 2, and it is now evidenced on real audio
rather than assumed.

### What held up

- **Negation**: `does not report`, `no radiographic evidence`, `remain negative`
  — all preserved (006, 007, 008).
- **Uncertainty**: `may represent`, `remains possible but unconfirmed` — exact
  (009, 010).
- **Attribution**: `the patient reports that the outside records indicate` —
  exact (011).
- **Laterality**: `left lower extremity`, `right knee … than the left`,
  `right psoas` — all exact (024, 025, 026).
- **Numbers and units**: every value preserved (020–023); only spoken-to-digit
  formatting changed.
- **Ambiguous abbreviation `PT`** was left alone and not expanded (027).

So the protected categories that CLAUDE.md §4.1 cares most about survived raw
transcription intact. The damage is concentrated in **named entities**.

### Consequence for later phases

1. **Phase 2** has a clear, evidence-backed target list.
2. **Phase 4 cannot rely on spelling.** `septipine` and `and nasal` are
   fluent English. A validator that only checks whether a token is a known word
   would pass both. Medication and organism names must be checked against a
   closed vocabulary, and an out-of-vocabulary drug name must force review.
3. **Fabrication is real** (`Michael Hex 3`), and it originates in the **ASR**,
   not the polishing model. The validator compares polished text against the raw
   transcript, so ASR-invented content is inside the trusted baseline and will
   pass validation unchallenged. This is a structural gap worth stating plainly.

### Silence hallucination

Separately observed while testing the recorder: given a recording of an empty
room, `large-v3-turbo` transcribed `Thank you.` — a well-documented Whisper
behaviour on non-speech input.

A dictation tool that emits invented text when the user has said nothing is a
hazard, and Phase 4 cannot catch it for the reason above. A voice-activity gate
before transcription is the appropriate mitigation. `whisper-vad-speech-segments`
was built alongside `whisper-cli` and is available. Logged here as a Phase 5
requirement.

---

## 8. Conclusions

1. The authoritative baseline is the real-voice run in §7a: **15.31 % content
   WER**, 12 of 33 utterances exact. The earlier 5-phrase synthetic figure
   (2.38 %) is superseded and misleading.
2. Errors concentrate on **named entities** — antibiotics, organisms,
   abbreviations — not on grammar, negation, numbers, or laterality.
3. The most dangerous failures are **fluent and wrong** (`septipine`,
   `and nasal`, `Michael Hex 3`), so no spell-check-style validator will catch
   them. Closed-vocabulary checking is required.
4. The protected categories in CLAUDE.md §4.1 survived raw transcription intact
   in every test case.
5. The number-word/digit difference is a formatting artefact, not an error, but
   it dictates how the Phase 4 validator must compare protected numbers.
6. Local transcription provably works with no network access.
7. Latency on real utterances: median 2.36 s, p95 3.42 s. Roughly 1.1 s of each
   is per-invocation model load, addressable later with a persistent process.

### Caveats on this baseline

- 33 utterances and 296 reference words is enough to see a pattern, not enough
  for a precise rate. Treat the percentages as indicative.
- One voice, one microphone, one reading session, scripted rather than
  spontaneous speech. Real dictation includes hesitation and self-correction,
  which are not represented here.
- The Bluetooth input path remains unmeasured and is expected to be worse.
- The corpus was written to exercise known-hard categories, so it is adversarial
  by construction. It over-represents difficult terminology relative to ordinary
  dictation, and its WER should not be read as an expected everyday rate.
