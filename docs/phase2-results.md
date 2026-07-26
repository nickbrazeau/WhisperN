# Phase 2 Results — Medical Terminology in Speech Recognition

Measured 2026-07-26. Review gate for CLAUDE.md §6 Phase 2: report the change
in overall transcription error and medical-term error, keeping examples of both
improvements and regressions.

---

## 1. What changed

The only change is a Whisper **initial prompt** carrying medical vocabulary.
The model, decoding parameters, and audio are identical to the Phase 1
baseline. No transcript post-processing, no exact replacements (all remain
`enabled: false`), no polishing model.

Prompt built by `scripts/build_asr_prompt.py --mode infectious_diseases`.

## 2. The governing constraint

whisper.cpp caps the initial prompt at

```
max_prompt_ctx = min(n_max_text_ctx, n_text_ctx / 2)
```

which is **224 tokens** for `large-v3-turbo`. Overruns are truncated
**silently** — terms disappear with no warning.

In practice **~16 terms** fit. That is the whole budget. A 13,784-term drug
dictionary cannot be "added to the prompt"; attempting it would keep whatever
sorted first and discard the rest without saying so.

This is why `config/specialties.yaml` separates `prompt` files (small, ordered,
reach the decoder) from `validation` files (large, never reach the decoder).
Conflating the two is the main way to get this wrong.

---

## 3. Headline results

Two corpora, identical text, 33 utterances and 296 reference words each.
`REAL` is the user's recorded voice; `SYN` is macOS speech synthesis.

### Overall transcription error (content WER)

| Corpus | No prompt | Prompt (p1) | Prompt (p2) |
|---|---|---|---|
| REAL | 15.31 % (47 err) | **12.70 %** (39) | 13.68 % (42) |
| SYN | 10.42 % (32 err) | 9.77 % (30) | **9.45 %** (29) |
| Combined errors | 79 | 69 | 71 |

### Medical-term accuracy

Measured directly: for 37 target terms across 21 utterances, does the correct
term appear in the transcript?

| Corpus | No prompt | Prompt (p1) | Prompt (p2) |
|---|---|---|---|
| REAL | 73.0 % (27/37) | **83.8 %** (31/37) | **83.8 %** (31/37) |
| SYN | 81.1 % (30/37) | 89.2 % (33/37) | **91.9 %** (34/37) |
| Combined | 77.0 % (57/74) | 86.5 % (64/74) | **87.8 %** (65/74) |

**Medical-term accuracy on real speech improved from 73.0 % to 83.8 %**, a
40 % relative reduction in medical-term errors. Overall content WER improved
from 15.31 % to 12.70 %.

Medical-term accuracy is the metric this phase exists to move. CLAUDE.md §10.11
warns against claiming medical accuracy from general word error rate, and the
two metrics do diverge here — see §5.

---

## 4. Improvements

Real voice, no-prompt → prompted. Five utterances improved.

| # | Before | After |
|---|---|---|
| 002 | `methicillin susceptible **to** staphylococcus aureus` | `methicillin-susceptible Staphylococcus aureus` |
| 013 | `Pipercellum Tauzan Bactam` | `piperacillin-tazobactam` |
| 016 | `enterococcus facium vancomycin resistance` | `Enterococcus faecium, vancomycin-resistant` |
| 017 | `Pseudomonas originosa … septipine` | `Pseudomonas aeruginosa … cefipine` |
| 028 | `in the past **Michael Hex 3**` | `in the past medical history` |

Two of these matter more than the error counts suggest.

**028 was a fabrication.** The baseline invented `Michael Hex 3` — proper-noun-like
content that was never spoken. Supplying real medical vocabulary removed it.
This is the single most valuable result in this phase, because Phase 4 cannot
catch ASR fabrication: the validator compares polished text against the raw
transcript, so anything the ASR invents is already inside the trusted baseline.
Reducing fabrication at the source is the only defence available.

**002 changed meaning, not spelling.** The inserted `to` turned a susceptibility
phenotype into a different clinical claim. It is now correct.

---

## 5. Regressions

Two, both real and both worth recording.

### 5.1 `Clostridioides difficile` — correct at baseline, broken with the prompt

```
ref  : Stool testing was positive for Clostridioides difficile toxin.
base : ... positive for clostridioides difficile toxin      <- correct
p1   : ... positive for clisteroides, difficile toxin,      <- regressed
p2   : ... positive for clisteriodiodes difficile toxin.    <- still wrong
```

The initial attempt at a fix was to promote `Clostridioides difficile` into the
priority block so it would reach the decoder. **It did not fix the term** and it
made overall real-voice WER worse (12.70 % → 13.68 %). The entire difference was
one unrelated utterance (028) flipping from 0 to 3 errors.

### 5.2 `milligrams` → `mg`

```
ref : ... one point four milligrams per deciliter
base: ... 1.4 milligrams per deciliter
p1/2: ... 1.4 mg per deciliter
```

The unit was abbreviated. The **value is unchanged**, so this is a formatting
difference rather than a clinical error, but it is a unit rewrite occurring in
the *raw* transcript. Phase 4 must treat `milligrams` and `mg` as the same unit
while still rejecting any change to the number itself.

---

## 6. What prompting did not fix

Six target terms remain wrong on real speech, identically under both prompt
variants:

| Term | In prompt? | Result |
|---|---|---|
| `linezolid` | **yes** | `and nasal` — drug lost, route invented |
| `cefepime` | **yes** | `cefipine` |
| `Clostridioides` | **yes** (p2) | `clisteriodiodes` |
| `jirovecii` | **yes** | `Jervichet` |
| `bronchoalveolar` | no | `Bronchial, alveolar,` |
| `PCP` | no | `PCAP` |

**Four of the six are in the prompt and still fail.** Prompt inclusion biases
decoding; it does not guarantee recognition. This is a hard ceiling on what
Phase 2 can achieve, and it is the strongest available argument that the Phase 4
validator cannot be optional.

`linezolid` → `and nasal` remains the most dangerous single failure in the
corpus: a dose survives, attached to no medication and to a route never spoken.

---

## 7. On prompt tuning

Two prompt orderings were compared:

- **p1** — dictionary order: observed failures, then a taxonomic walk through
  *Staphylococcus* and *Streptococcus* species.
- **p2** — evidence-first: observed failures, then common-and-fragile terms
  (`Clostridioides difficile`, `Escherichia coli`, `Klebsiella pneumoniae`),
  displacing species that were never failing.

Results **disagree by corpus**: p1 is better on real audio by 3 errors, p2 is
better on synthetic by 1, and on medical-term accuracy they are identical on
real audio. The direction of the difference flips depending on which corpus is
used, which is the signature of noise rather than signal.

**Conclusion: prompt *presence* helps reliably and substantially; prompt
*ordering*, between two reasonable variants, is not measurable at this sample
size.** p2 is retained because it wins the combined medical-term metric
(87.8 % vs 86.5 %) and because its content is clinically more defensible —
`Clostridioides difficile` is dictated far more often than
*Staphylococcus lugdunensis*.

This is worth stating plainly: it would have been easy to run one corpus, see
12.70 % vs 13.68 %, and conclude that a particular term ordering was
"better". The second corpus showed that conclusion was unsupported.

---

## 8. Latency

No meaningful change. Real corpus, median 2.36 s → 1.99 s, p95 3.42 s →
2.15 s. The apparent improvement is filesystem cache warmth across repeated
runs, not an effect of the prompt; the prompt adds tokens to the decoder
context and should if anything cost slightly more.

---

## 9. Caveats

- 33 utterances per corpus is enough to see a large effect and not enough to
  resolve a small one. The 73.0 % → 83.8 % medical-term improvement is well
  outside noise; the p1/p2 difference is not.
- The corpus was written to exercise known-hard terminology and is adversarial
  by construction. It over-represents difficult terms relative to ordinary
  dictation, so these error rates are not an expected everyday rate.
- The prompt is tuned for `infectious_diseases`. Other modes are configured but
  unmeasured.
- Terms in the priority block were selected using failures observed on this
  same corpus. That is a form of fitting to the test set: the improvement on
  *these* terms is partly by construction. The synthetic corpus provides
  independent audio but not independent text. A genuinely held-out phrase set
  would be needed to estimate generalisation.

---

## 10. Status against the Phase 2 review gate

- [x] Overall transcription error reported, before and after.
- [x] Medical-term error reported, before and after.
- [x] Examples of improvements kept (§4).
- [x] Examples of regressions kept (§5).
- [x] Exact replacements written but **not activated** — all `enabled: false`,
      awaiting review per CLAUDE.md §8.

### Recommendation on exact replacements

`config/exact_replacements.yaml` contains three candidate rules. Recommendation:

- `clisteroides` → `Clostridioides`: defensible. The match is a nonword with
  nothing to collide with. But note the misrecognition is unstable across
  prompts (`clisteroides`, `clisteriodiodes`), so a rule chasing spellings will
  always lag.
- `septipine` → `cefepime`: **reject permanently.** This rule asserts which
  antibiotic the speaker said. If a different cephalosporin were misheard the
  same way, it would silently substitute the wrong drug. An unrecognised
  antibiotic should stop the pipeline for review, not be guessed.
- `staff aureus` → `Staphylococcus aureus`: not observed in this project;
  carried from the CLAUDE.md example and unvalidated here.

The general point: exact replacements treat the symptom. The four in-prompt
terms that still fail argue for closed-vocabulary validation in Phase 4, where
an unknown drug or organism name forces human review, rather than an
ever-growing list of misspellings.
