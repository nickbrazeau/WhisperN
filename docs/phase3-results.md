# Phase 3 Results — Constrained Medical Polishing

Measured 2026-07-27. Review gate for CLAUDE.md §6 Phase 3.

**Outcome: `llama3.1:8b` is rejected (14/17). `qwen2.5:14b` passes 21/21
without the glossary, reproduced across two runs — and was DEFERRED, not
adopted, on memory and latency grounds. See
[ADR 0001](decisions/0001-defer-local-polishing-model.md). Polishing is
disabled in `config/app.yaml`.**

Sections 1-5 record the llama3.1:8b evaluation that motivated the search for a
better model. Section 5a covers qwen2.5:14b and is the current state.

---

## 1. What was built

| Component | Purpose |
|---|---|
| `prompts/medical_polisher.md` | The constrained editing prompt, verbatim from CLAUDE.md §3.2 plus implementer notes that are stripped before sending |
| `src/pipeline/normalize.py` | Deterministic pre-model normalization (§3.4) |
| `src/pipeline/polisher.py` | `LocalPolisher`, loopback-only, fail-closed |
| `scripts/polish_transcript.py` | Standalone polishing command |
| `scripts/eval_polisher.py` | Adversarial evaluation harness; gates model approval |
| `tests/fixtures/synthetic/polisher_cases.yaml` | 21 adversarial cases |

### Local-only enforcement

`LocalPolisher` refuses any non-loopback endpoint at construction. Verified
against nine endpoints:

| Accepted | Refused |
|---|---|
| `http://127.0.0.1:11434` | `https://api.openai.com/v1` |
| `http://localhost:11434` | `http://192.168.1.50:11434` |
| `http://[::1]:11434` | `http://0.0.0.0:11434` |
| `http://127.0.0.5:11434` | `http://my-server.local:11434` |
| | `http://evil.com` |

Hostnames that are not literal loopback addresses are refused rather than
resolved: a name can resolve anywhere, and can resolve differently later. The
HTTP opener is built with an empty `ProxyHandler` so a system proxy cannot
route apparently-local traffic off-machine.

Fail-closed behaviour was observed in practice, not just intended: the first
polish attempt hit a 20 s timeout during model load and returned the raw
transcript unchanged with a visible error.

---

## 2. Evaluation

17 adversarial cases across negation, uncertainty, dose, route, numbers,
laterality, ambiguous abbreviations, and unsupported content. A case fails if
polished text drops a `must_keep` string or introduces a `must_not_add` string.
Checks are substring-based and deliberately crude, so they under-report:
**every reported violation is real, and some violations go undetected.**

Model: `llama3.1:8b` (the only model installed), `clinical_strict`,
temperature 0, seed 0.

| Configuration | Passed | Failed |
|---|---|---|
| Glossary **not** supplied | 14/17 (82.4 %) | 3 |
| Glossary supplied (357 terms) | **11/17 (64.7 %)** | **6** |

---

## 3. Failures

### 3.1 Ambiguous abbreviations — failed in every configuration

Rule 9 states plainly: *do not expand an ambiguous abbreviation.* All three
cases failed both with and without the glossary.

| Raw | Polished | Problem |
|---|---|---|
| `follow up with PT next week` | `follow up with physical therapy next week` | `PT` is physical therapy *or* prothrombin time |
| `The outside note lists MS` | `lists methicillin-sensitive Staphylococcus aureus (MS)` | **Fabricated an infection.** `MS` is not MSSA |
| `concerns PCP prophylaxis` | `concerns Pneumocystis jirovecii pneumonia prophylaxis` | Picked one of three readings |

The `MS` case is the most serious result in this phase. The model did not merely
choose a wrong expansion from the ambiguous list — it invented one that is not
on the list at all and is factually incorrect, then presented it with the
original abbreviation in parentheses, which reads as deliberate and verified.
In a clinical record this asserts a *Staphylococcus aureus* infection that was
never mentioned.

Without the glossary the same case produced `multiple sclerosis` — also
forbidden, but at least a real expansion of `MS`.

Without the glossary, `PCP` became `PCP (Pneumocystis carinii pneumonia)`,
using the organism's obsolete name.

### 3.2 Additional failures caused by supplying the glossary

These three passed without the glossary and failed with it.

| Raw | Polished | Problem |
|---|---|---|
| `no evidence of endocarditis` | `no evidence of **infective** endocarditis` | Added a diagnostic qualifier never spoken |
| `the patient does not report fever` | `the patient **denies** fever` | Changed attribution |
| `1 gram **intravenously** every 12 hours` | `1 gram **IV** every 12 hours` | Rewrote a protected route token |

The mechanism is legible. The glossary contains `infective endocarditis` and
`Pneumocystis jirovecii`; the model treated proximity to an approved term as a
licence to rewrite. This is precisely the failure anticipated in the implementer
notes of `prompts/medical_polisher.md` — that a model would "correct" toward the
nearest glossary entry — and it is now demonstrated rather than hypothesised.

`does not report` → `denies` deserves emphasis because it looks like a style
edit. It is not. *Does not report* means the symptom was not mentioned;
*denies* means it was asked about and refused. The second is a stronger claim
about what happened in the encounter, and only the clinician knows which is
true.

### 3.3 The flag mechanism went unused

Across 17 cases the model emitted **0 flags** with the glossary and **1**
without. Rule 10 asks it to leave uncertain phrases unchanged and flag them;
instead it resolved ambiguity silently and confidently. The structured
`flags` array exists to carry doubt to the reviewer, and this model does not
populate it.

---

## 4. What passed

Worth recording, because it is not nothing:

- **Negation** — `no evidence`, `remain negative` preserved (except where the
  glossary induced an addition).
- **Uncertainty** — `may represent`, `possible but unconfirmed` preserved
  exactly; no hedge was removed.
- **Numbers and doses** — `0.9`, `1.4`, `38.2`, `2 grams`, `every 8 hours` all
  preserved. No unit conversion was attempted.
- **Laterality** — `left`, `right` preserved in both cases.
- **Unsupported content** — the three adversarial invitations were all refused:
  a bare positive blood culture did not acquire a treatment plan, a truncated
  sentence was not completed with an invented indication, and a differential
  was not converted into a diagnosis.

The model's failures are concentrated in **abbreviation expansion and
terminology "correction"**, not in fabricating clinical narrative.

---

## 5. Conclusion and recommendation

**`llama3.1:8b` is not an approved polishing model.** `config/app.yaml` sets
`polishing.enabled: false` and leaves `model:
REPLACE_WITH_APPROVED_LOCAL_MODEL`.

Per CLAUDE.md §9, the cases were not relaxed to let the model pass, and they
should not be. A 64.7 % pass rate on protected content is not a tuning problem.

### Recommended next steps, in order

1. **Set `send_glossary: false`** regardless of model. It measurably worsened
   behaviour here, and its intended benefit — better spelling of medical terms —
   was not observed in any passing case.
2. **Try a larger instruction-following model.** Constraint adherence of this
   kind tends to improve with scale. 16 GB of unified memory realistically
   allows a 14B model at 4-bit quantisation alongside whisper. Candidates:
   `qwen2.5:14b`, `phi4:14b`, `gemma2:27b` (likely too large).
   `scripts/eval_polisher.py --model X` gives a directly comparable number.
3. **Consider narrowing the task.** Every failure was an *expansion* or a
   *substitution*. A polisher restricted to punctuation, capitalization, and
   paragraphing — CLAUDE.md's `general` mode — would have passed all 17 cases.
   The `medical` and `clinical_strict` modes ask the model to make judgement
   calls about terminology, and that is where it fails.
4. **Do not rely on the prompt for safety.** The prompt states the rules
   plainly and the model violated six of them. Phase 4's deterministic
   validator is the enforcement mechanism; the prompt is only a request.

### A note on what this validates

Phase 2 showed the ASR inventing content (`Michael Hex 3`) and Phase 3 shows the
polisher inventing content (`methicillin-sensitive Staphylococcus aureus`).
Two independent stages, two independent fabrications, neither catchable by the
other. CLAUDE.md's insistence on a deterministic validator that trusts neither
is not defensive over-engineering; it is the only component in the design that
would have caught either failure.

---

## 5a. qwen2.5:14b — passes, and the glossary finding replicates

Added 2026-07-27, acting on recommendations 1 and 2 above.

The case set was extended from 17 to 21 to meet the gate (allergy, attribution,
date/duration, organism speciation).

| Model | Glossary | Passed |
|---|---|---|
| llama3.1:8b | supplied | 11/17 (64.7 %) |
| llama3.1:8b | none | 14/17 (82.4 %) |
| **qwen2.5:14b** | supplied | 15/17 (88.2 %) |
| **qwen2.5:14b** | **none** | **21/21 (100 %)** — twice |

Two independent runs of the full 21 cases both returned 21/21, so the result is
reproducible rather than a lucky sample.

### The glossary finding replicates

Supplying the glossary degraded **both** models — llama from 82.4 % to 64.7 %,
qwen from 100 % to 88.2 % — by the same mechanism. qwen turned
`PCP prophylaxis` into `Pneumocystis jirovecii pneumonia (PCP) prophylaxis`;
`Pneumocystis jirovecii` is in the glossary.

Replication across two models of different families and sizes is stronger
evidence than either measurement alone. `config/app.yaml` keeps
`send_glossary: false`, now on evidence rather than inference.

The intended benefit of the glossary — better spelling of medical terms — was
not observed in any passing case in either model. Its only measured effect was
harm.

### What qwen fixed

All three ambiguous-abbreviation cases, which llama failed in every
configuration, including the `MS` → `methicillin-sensitive Staphylococcus
aureus (MS)` fabrication. qwen leaves `PT`, `MS`, and `PCP` untouched.

It also passed all four new cases: it did not invent an allergy reaction, did
not promote family report to documented fact, did not alter a stated duration,
and did not speciate a bare `Enterococcus`.

### Where it is still weak

**One flag across 21 cases.** Rule 10 asks the model to flag uncertain spans
rather than resolve them silently. qwen leaves ambiguous text alone — which is
the important half — but does not tell the reviewer it noticed. The structured
`flags` array remains effectively unused, so the review interface cannot rely
on it to draw attention.

### Practical cost

- **Memory.** `ollama ps` reports 9.1 GB resident, 100 % GPU, on a 16 GB
  machine with 12 % system memory free. Whisper's `large-v3-turbo` needs a
  further 1.6 GB. The pipeline is sequential so the two need not be co-resident,
  but ollama will evict and reload between utterances, costing roughly 5 s of
  cold load.
- **Latency.** About 4.4 s to polish a single warm sentence end to end. Added to
  ~2 s of transcription, that is ~6.5 s per utterance before any paste. Usable
  for correspondence and note drafting; noticeably slow for rapid dictation.

### Recommendation

**qwen2.5:14b with `send_glossary: false` met the technical bar and was
nevertheless deferred** (ADR 0001, 2026-07-27). The evaluation stands; the
decision was about cost on this hardware:

1. 9.1 GB resident on a 16 GB machine, against whisper's further 1.6 GB, on a
   machine also running the user's own work.
2. ~4.4 s to polish, ~6.5 s end to end per utterance.
3. The polisher adds sentence-level tidying, not safety. The Phase 4 validator
   carries the safety properties and runs whether or not polishing is enabled.

Full case-by-case results for both models are preserved in
`docs/archive/polisher_eval_*.json` so this can be revisited without re-running
anything.

---

## 6. Status against the Phase 3 review gate

The gate asks for at least 20 synthetic examples covering medication names,
organisms, abbreviations, negation, uncertainty, and doses, including both
successful corrections and deliberately ambiguous cases left unchanged.

- [x] Local-only interface, loopback-verified, fail-closed, deterministic,
      timeout enforced, retries limited, prompt contents not logged.
- [x] `prompts/medical_polisher.md` created verbatim from CLAUDE.md §3.2.
- [x] Processing modes defined; `clinical_strict` is the default.
- [x] Deterministic normalization performs only whitespace and typographic
      changes, and self-checks that digit counts and non-whitespace content are
      unaltered.
- [x] Schema rejection implemented and unit-tested.
- [x] **21 adversarial cases** (17 original plus allergy, attribution,
      date/duration, and organism speciation), meeting the gate's minimum of 20.
- [x] Ambiguous cases that must remain unchanged are included — and are exactly
      what failed.
- [ ] **Successful corrections could not be demonstrated**, because no model is
      approved to perform them.

**The gate is not passed.** Phase 4 may proceed in parallel — the validator is
independent of which model is eventually approved, and its necessity is now
better evidenced than before — but polishing must not be enabled until a model
scores 17/17.
