# ADR 0001 — Defer adopting a local polishing model

- **Status:** Accepted — 2026-07-27
- **Decision by:** user (project owner)
- **Supersedes:** nothing
- **Affects:** `config/app.yaml` `polishing.enabled`, Phase 3, Phase 8 readiness

---

## Decision

**Do not adopt `qwen2.5:14b` as the polishing model at this time.** Polishing
stays `enabled: false`. The pipeline runs raw-transcript-only: transcribe →
VAD gate → normalize → validate → review.

The evaluation is archived rather than discarded, so the decision can be
revisited without repeating the work.

## Context

Phase 3 built a constrained polisher and an adversarial harness. Two models
were evaluated against 21 cases derived from CLAUDE.md §3.2.

| Model | Glossary | Passed |
|---|---|---|
| llama3.1:8b | supplied | 11/17 (64.7 %) |
| llama3.1:8b | none | 14/17 (82.4 %) |
| qwen2.5:14b | supplied | 15/17 (88.2 %) |
| **qwen2.5:14b** | **none** | **21/21 (100 %)**, reproduced twice |

`qwen2.5:14b` met the technical bar. It was not adopted anyway.

## Rationale

**Memory, primarily.** `ollama ps` reports 9.1 GB resident on a 16 GB machine
with system memory free at 12 %. Whisper's `large-v3-turbo` needs a further
1.6 GB. The pipeline is sequential, so the two need not be co-resident, but
ollama will evict and reload between utterances at roughly 5 s of cold load —
on a machine that is also running the user's actual work.

**Latency.** ~4.4 s to polish one warm sentence; ~6.5 s end to end with
transcription. Acceptable for correspondence, poor for rapid dictation.

**Benefit is modest relative to cost.** With polishing off, the pipeline still
delivers capitalisation and punctuation directly from Whisper, the VAD gate,
and — most importantly — the Phase 4 validator, which surfaces mangled drug and
organism names in the *raw* transcript. The polisher adds sentence-level tidying,
not safety.

**The residual risk is asymmetric.** A polisher that passes 21 cases can still
fail case 22. Phase 3 and Phase 4 both produced fabrications that were not
anticipated in advance (`methicillin-sensitive Staphylococcus aureus (MS)`;
`Michael Hex 3`). Declining to enable a text-rewriting stage that is not needed
for safety is the conservative reading of CLAUDE.md §3.2 and §12.

## Consequences

### Now

- `polishing.enabled: false`; `model:` stays `REPLACE_WITH_APPROVED_LOCAL_MODEL`.
- `scripts/dictate.py` reports `polishing : OFF — raw transcript only`.
- The validator still runs, comparing the raw transcript against itself, which
  is what surfaces unrecognised drug names such as `septicpene`.
- Phase 3's review gate is **met but unused**. It is not a blocker for Phases 6
  and 7.

### Kept for later

| Artefact | Location |
|---|---|
| Full case-by-case results, both models | `docs/archive/polisher_eval_*.json` |
| Analysis, failure modes, glossary finding | `docs/phase3-results.md` |
| Adversarial cases (21) | `tests/fixtures/synthetic/polisher_cases.yaml` |
| Harness | `scripts/eval_polisher.py` |
| Polisher implementation | `src/pipeline/polisher.py` |
| End-to-end demo | `scripts/pipeline_demo.py` |

The `qwen2.5:14b` weights remain installed (9.0 GB under `~/.ollama/models`,
which currently holds 13 GB total). Removing them is a separate decision:
`ollama rm qwen2.5:14b` frees the space and costs a re-download to revisit.

## How to revisit

1. `ollama pull qwen2.5:14b` if it was removed.
2. `scripts/eval_polisher.py --model qwen2.5:14b` — expect 21/21. Re-run rather
   than trusting this document; the harness is the authority.
3. Confirm the memory and latency picture still holds on the hardware of the
   day; both objections are hardware-dependent and a machine with more unified
   memory would remove them.
4. If adopting: set `polishing.enabled: true`, `model: qwen2.5:14b`, and leave
   `send_glossary: false`.

## What would change this decision

- **More memory.** The objection is 9.1 GB on a 16 GB machine, not the model.
- **A smaller model that passes 21/21.** Worth testing when one appears; the
  harness gives a comparable number in one command.
- **Restricting the task.** Every llama failure was an expansion or
  substitution. CLAUDE.md's `general` mode — punctuation, capitalisation, and
  paragraphing only — is a materially easier task that a small model may handle
  safely. This was never measured and is the most promising unexplored option.
- **Evidence that raw transcripts are inadequate in practice.** If real use
  shows punctuation and capitalisation from Whisper alone are too poor, the
  cost-benefit shifts.

## Non-goals

This ADR does not reject polishing in principle, and does not reject
`qwen2.5:14b` on quality grounds. It passed. The decision is about cost on this
hardware and about not enabling a rewriting stage that safety does not require.

## Related

- `docs/phase3-results.md` §5a — the qwen evaluation in full
- `docs/phase4-results.md` — why the validator, not the polisher, carries safety
- CLAUDE.md §3.2, §9, §12
