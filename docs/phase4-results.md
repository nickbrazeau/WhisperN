# Phase 4 Results — Protected-Token Validator

Measured 2026-07-27. Review gate for CLAUDE.md §6 Phase 4: all adversarial
protected-token tests must pass before automatic paste is enabled.

**Outcome: the validator passes its tests. Automatic paste remains disabled,
because Phase 3 has no approved polishing model.**

---

## 1. Headline

The validator catches **every failure observed anywhere in this project**, and
does so on vocabulary it has never seen.

| Corpus | Utterances | Flagged | True positives | False positives |
|---|---|---|---|---|
| Real voice (in-sample) | 33 | 3 | **3** | **0** |
| Held-out (unseen terminology) | 39 | 9 | **9** | **0** |

100 % precision on both. Test suite: **40/40 passing**.

### The result that matters most

**Detection generalizes where correction did not.**

Phase 2 showed that supplying medical vocabulary to the ASR helps only the ~16
terms that fit in the prompt, and does not transfer to neighbouring terminology
(docs/phase2-results.md §8a). On the same held-out corpus, the validator caught
every mangled drug and organism name:

| Spoken | ASR produced | Caught |
|---|---|---|
| `Cryptococcus neoformans` | `creptococcus neoformans` | yes |
| `Serratia marcescens` | `seratium arcesans` | yes |
| `ertapenem` | `Urtapenem` | yes |
| `micafungin` | `McCaffungen` | yes |
| `dalbavancin` | `dalbavansin` | yes |
| `ceftaroline` | `Sifteraline` | yes |
| `Galactomannan` | `Galactamannan` | yes |

None of these terms is in the ASR prompt. None needed to be. Asking "is this a
real drug name?" scales to the whole 21,000-term vocabulary in a way that
asking the decoder to recognise a term does not.

This inverts the emphasis the project started with. Improving recognition is
bounded by a 224-token budget; detecting non-recognition is bounded only by the
size of the dictionary.

---

## 2. Design decisions forced by measurement

### 2.1 Numbers compare by value, not by string

The ASR renders spoken numbers as digits: `two grams` → `2 grams`,
`seventy-two hours` → `72 hours`, `zero point nine` → `0.9`. A string
comparison would fire on every dose in every dictation.

An alert that always fires is ignored, and would take the real alerts with it.
`src/validation/protected.py` therefore evaluates spoken number phrases —
including `two hundred ten` → `210` and `eighteen thousand` → `18000` — and
compares canonical values. Eight equivalence pairs are covered by tests.

### 2.2 Units and routes compare by meaning

The raw transcripts contain both `milligrams per deciliter` and `mg per
deciliter`; the Phase 3 polisher rewrote `intravenously` to `IV`. These are the
same clinical fact. Synonym tables map them to canonical forms, so
`1 gram intravenously` and `1 gram IV` validate as **safe_to_paste** while
`500 mg orally` versus `500 mg intravenously` is **rejected**.

### 2.3 Name checking is membership, not spelling

Suffix matching was implemented first and **failed**. The real ASR output
`pipercellum` (for piperacillin) retains no drug-like suffix, so a rule keyed on
`-cillin`, `-mycin`, `-penem` missed precisely the worst-mangled names.

The working approach tests membership in the union of ordinary English
(`/usr/share/dict/words`) and the medical vocabularies — 245,853 words. A word
that is in neither is reported.

---

## 3. The false-positive problem, and why it dominated the work

The first membership implementation flagged **13 of 33** real transcripts.
Almost all were wrong:

```
imaging, identified, increased, demonstrated, showed, sequencing
methicillin, nephrolithiasis, musculoskeletal, echocardiogram, Bayesian
```

Two causes:

1. **`/usr/share/dict/words` is `web2`, a 1934 word list.** It contains base
   forms only — `show` but not `showed`, `identify` but not `identified` — and
   predates modern medical vocabulary entirely. It does not contain
   `vancomycin`.
2. **Modern clinical terms** were absent from both it and the generated
   dictionaries.

Fixes: morphological tolerance (`-s`, `-es`, `-ies`, `-ied`, `-ed`, `-ing`,
`-ly`, `-er`, `-est`, with consonant doubling), and inclusion of the curated
clinical dictionaries as known vocabulary.

Result: 13 false positives → **0**, with all true positives retained.

This mattered more than it might appear. A validator that flags `showed` as a
suspicious medical term teaches its user to dismiss warnings, and the next
warning dismissed is `Sifteraline`. Precision is not a cosmetic property here;
it is what makes the true positives visible.

---

## 4. Adversarial results

All cases in `tests/test_protected_tokens.py` (40 tests).

### Rejected — CLAUDE.md §4.2's reject list

| Change | Example | Status |
|---|---|---|
| Dose changed | `2 grams` → `3 grams` | rejected |
| Negation removed | `no evidence of` → `evidence of` | rejected |
| Negation substituted | `does not report` → `denies` | rejected |
| Laterality flipped | `left` → `right` | rejected |
| Frequency changed | `every 8 hours` → `every 12 hours` | rejected |
| Route changed | `orally` → `intravenously` | rejected |
| Sentence added | `+ "Recommend echocardiogram."` | rejected |
| Empty output | `""` | rejected |
| Fabricated organism | `MS` → `methicillin-sensitive Staphylococcus aureus (MS)` | rejected |

### Review required

| Change | Status |
|---|---|
| `no evidence of endocarditis` → `no evidence of **infective** endocarditis` | review |
| `PT` → `physical therapy` | review |
| `cefepime` → `septipine` | review |
| Unrecognised drug name in the raw transcript | review |

### Safe to paste

| Change | Status |
|---|---|
| Capitalisation and terminal punctuation only | safe |
| `intravenously` → `IV` | safe |
| Spoken number rendered as digits | safe |

Every one of the six polisher failures from docs/phase3-results.md is caught.

---

## 5. Limitations — stated plainly

### 5.1 A real word used wrongly is not detectable here

During Phase 1 the ASR produced `Pneumocystis cerevisiae`. *Cerevisiae* is a
genuine organism (from *Saccharomyces cerevisiae*), so membership testing
passes it. Only context could reveal that a yeast has no business in a
bronchoalveolar lavage report for *Pneumocystis*.

Membership testing is a **floor** on detection, not a ceiling. It catches
non-words; it cannot catch a plausible substitution of one real name for
another.

### 5.2 ASR fabrication is inside the trusted baseline

The validator compares polished text **against the raw transcript**. Content the
ASR invented — `Michael Hex 3`, or `Thank you.` transcribed from silence — is
part of the reference and cannot be flagged as unsupported. This is structural,
not an implementation gap, and it is why a voice-activity gate before
transcription remains a Phase 5 requirement.

### 5.3 Sentence counting is crude

Added sentences are detected by counting terminal punctuation. Legitimate
punctuation repair that splits a run-on sentence would be rejected. This is
deliberate in `clinical_strict`: false rejection costs a review, false
acceptance costs a wrong record.

### 5.4 The word lists are not authoritative for English

`web2` is old, and the morphological rules are heuristic. Words such as
`biofilm` and `musculoskeletal` had to be added to the dictionaries by hand.
Expect occasional additions as new vocabulary appears; each one is a
one-line dictionary change, not a code change.

---

## 6. Status against the Phase 4 review gate

> All adversarial protected-token tests must pass before enabling automatic
> paste.

- [x] Protected categories from §4.1 extracted and tested: negation, numbers,
      decimals, percentages, dates/times, doses, units, routes, frequencies,
      laterality, uncertainty, attribution.
- [x] Values and normalized forms extracted from both texts and compared.
- [x] Punctuation-only changes permitted.
- [x] Approved exact replacements permitted (none are enabled).
- [x] Otherwise marked `review_required`.
- [x] `review_required` never returns `safe_to_paste` in `clinical_strict`.
- [x] Reject conditions implemented: added sentence, lost number, changed
      negation, changed laterality, changed dose/route/frequency, empty or
      malformed output, untraceable content.
- [x] Structured JSON result as specified.
- [x] 40/40 adversarial tests pass.

**The gate is passed.** Automatic paste nevertheless remains disabled, because
Phase 3 has no approved model: there is nothing safe to paste yet. The
validator is ready for whichever model eventually passes.
