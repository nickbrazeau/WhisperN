# Phase 6 Results — Synthetic Medical Test Suite

Measured 2026-07-27. Review gate for CLAUDE.md §6 Phase 6.

**Acceptance criterion — unsupported-content rate must be zero — is met on both
corpora. Named-entity accuracy is poor and is the honest headline.**

---

## 1. Corpora

| Corpus | Utterances | Words | Audio | Text seen during tuning? |
|---|---|---|---|---|
| `corpus_prompted2` | 33 | 307 | user's voice, built-in mic | **yes** — prompt terms drawn from its failures |
| `heldout_syn` | 39 | 356 | macOS speech synthesis | **no** — terminology disjoint from the prompt |

Both are synthetic in the sense CLAUDE.md §3.1 requires: constructed phrases,
no patient information. The first is real speech reading constructed text.

Coverage against the required test groups in §6 Phase 6: negation, uncertainty,
medication and dose, organisms, numbers and units, laterality, ambiguous
abbreviations, research terminology, and adversarial additions — all present
across the two corpora and `tests/fixtures/synthetic/polisher_cases.yaml`.

---

## 2. Metrics

Produced by `scripts/benchmark.py`.

| Metric | Real voice (in-sample) | Held-out |
|---|---|---|
| Word error rate | 13.68 % | 14.89 % |
| Medical-term error rate | 35.71 % | 29.63 % |
| **Medication-name accuracy** | **62.5 %** (5/8) | **68.4 %** (13/19) |
| **Microorganism-name accuracy** | **66.7 %** (4/6) | **75.0 %** (6/8) |
| Number and unit preservation | **100 %** (33/33) | **100 %** (34/34) |
| Negation preservation | **100 %** (4/4) | **100 %** (4/4) |
| Laterality preservation | **100 %** (5/5) | **100 %** (5/5) |
| **Unsupported-content rate** | **0 %** | **0 %** |
| Fraction requiring review | 9.1 % (3/33) | 17.9 % (7/39) |

Latency through the full pipeline (VAD gate + transcription), 33 real
utterances: **median 1.77 s, p95 1.94 s, max 2.67 s.** The VAD gate costs
~79 ms of that. Polishing is disabled, so no polishing latency applies.

---

## 3. Reading these numbers honestly

### 3.1 The named-entity numbers are much worse than previously reported

`docs/phase2-results.md` reported "medical-term accuracy 83.8 %". This report
says 62.5 %. Both are correct; they measure different things.

The Phase 2 figure used a hand-written list of 37 target terms, which included
easy ones (`osteomyelitis`, `bacteremia`, `vancomycin`) alongside hard ones.
This report scores against the **generated dictionaries, filtered to words that
are not ordinary English** — so it measures only distinctive drug and organism
names, which is where the failures are.

**62.5 % is the number to plan around.** Roughly one distinctive drug or
organism name in three is mis-transcribed.

### 3.2 A vocabulary bug that flattered the earlier run

The first version of this benchmark reported medication accuracy of 85.4 % and
microorganism accuracy of **100.0 % (0/0)**.

Both were wrong:

- Organism entries are binomials (`Staphylococcus aureus`), and recall was
  measured per word, so nothing ever matched. Zero expected terms scored as
  100 % — a false pass that looks like success, which is worse than a wrong
  number.
- The FDA NDC file lists homeopathic and botanical preparations alongside
  drugs. **3,457 of 8,781 words** in `medications.txt` are ordinary English:
  `acid`, `above`, `abuse`, `fever`, `medical`. Scoring against them meant
  "medication-name accuracy" partly measured whether the ASR heard the word
  *fever*.

Both are fixed. Only the *scoring* vocabulary is filtered; the validator keeps
the full list deliberately, since there a larger known-word set means fewer
false alarms.

### 3.3 What "unsupported-content rate: 0 %" does and does not mean

CLAUDE.md's acceptance criterion concerns content the **polisher** adds that is
not traceable to the raw transcript (§4.2 reject list, §10.12). It is measured
polished-against-raw.

**Polishing is disabled** (ADR 0001), so polished text *is* the raw transcript
and the rate is zero by construction. That is a true property of the shipped
configuration, not a measurement gap — but it is not evidence that a polisher
would pass. When polishing is enabled this metric becomes meaningful without a
code change.

An earlier version of this benchmark compared hypothesis against *reference*
and reported a 24 % "unsupported-content rate", failing the suite. That was
wrong: `cefipine` for cefepime is a transcription error, already counted in the
medical-term error rate, not content the polisher invented. Conflating the two
would fail the acceptance gate on ordinary ASR error while hiding the defect
the gate exists to catch.

### 3.4 The protected categories are perfect, and that is the point

Numbers, units, negation, and laterality: **100 % preservation on both
corpora**, 85 protected tokens in total.

This is the asymmetry the whole design rests on. The ASR is unreliable on
*names* and reliable on *values*. A wrong drug name is detectable by dictionary
lookup; a wrong dose would not be, because any number is a plausible number.
The measurements say the dangerous-and-undetectable failure mode is not
occurring, while the detectable one occurs often.

---

## 4. Errors observed

Out-of-vocabulary terms produced by the ASR, all flagged by the validator:

| Corpus | Spoken | Transcribed |
|---|---|---|
| real | `cefepime` | `cefipine` |
| real | `Clostridioides` | `clisteriodiodes` |
| real | `jirovecii` | `jervichet` |
| held-out | `Cryptococcus` | `creptococcus` |
| held-out | `Serratia marcescens` | `seratium arcesans` |
| held-out | `ertapenem` | `Urtapenem` |
| held-out | `micafungin` | `McCaffungen` |
| held-out | `ceftaroline` | `Sifteraline` |
| held-out | `Galactomannan` | `Galactamannan` |
| real | `linezolid` | *(lost entirely — "and nasal")* |

`linezolid` is the only case where a drug name vanished rather than being
mangled, and it remains the most dangerous single failure in the corpus: a dose
survives attached to no medication.

---

## 5. Status against the Phase 6 review gate

- [x] Required test groups covered.
- [x] Overall word error rate reported.
- [x] Medical-term error rate reported.
- [x] Medication-name accuracy reported.
- [x] Microorganism-name accuracy reported.
- [x] Number and unit preservation reported — 100 %.
- [x] Negation preservation reported — 100 %.
- [x] Laterality preservation reported — 100 %.
- [x] Median and p95 latency reported — 1.77 s / 1.94 s.
- [x] Fraction requiring review reported — 9.1 % / 17.9 %.
- [x] **Unsupported-content rate is zero.**

**The gate is passed**, with the caveat in §3.3: zero unsupported content is
currently a property of having no polisher, not a demonstration that a polisher
behaves.

### Caveats

- 33 and 39 utterances are small. Denominators for the named-entity metrics are
  smaller still (8, 6, 19, 8), so those percentages carry wide uncertainty —
  62.5 % is 5 of 8.
- Both corpora were written to exercise hard terminology and are adversarial by
  construction. They over-represent difficult names relative to ordinary
  dictation.
- One voice, one microphone, scripted rather than spontaneous speech.
- The Bluetooth input path is still unmeasured.
