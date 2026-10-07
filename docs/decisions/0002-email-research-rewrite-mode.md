# ADR 0002 — Add an email/research rewrite mode

- **Status:** Accepted — 2026-10-07
- **Decision by:** user (project owner)
- **Supersedes:** ADR 0001 *in part*: the memory objection no longer holds on
  current hardware. ADR 0001's decision to keep the constrained clinical
  polisher (`polishing.enabled`) off is unchanged.
- **Affects:** `config/app.yaml` `rewrite:`, CLAUDE.md §3.2, the desktop app

---

## Decision

Add a **rewrite mode** that turns rambling dictation into concise,
professional prose using a local model (`qwen2.5:14b` through Ollama).

Three constraints, set by the owner:

1. **Scope: email and research dictation only.** Rewrite mode is never used for
   clinical notes or patient data.
2. **Every rewrite needs an explicit click to paste.** There is no auto-paste
   path for a rewrite, whatever the auto-paste checkbox says.
3. **Local only.** Ollama runs as a login service bound to `127.0.0.1` with
   `OLLAMA_NO_CLOUD=1` (`scripts/setup_ollama.sh`).

## Why this is a separate mode and not the Phase 3 polisher

CLAUDE.md §3.2 lets the polisher change spelling, punctuation, and likely
recognition errors, and nothing else. A concise rewrite removes words,
reorders sentences, and rewords by design, so it cannot meet §3.2, and the
Phase 4 validator rejects it (it forbids new words and added sentences).

Rather than weaken either, rewrite mode is a different, explicitly scoped
function with its own check:

| | Constrained polisher (ADR 0001) | Rewrite mode (this ADR) |
|---|---|---|
| Purpose | fix ASR errors in clinical text | concise prose for email/research |
| Prompt | `prompts/medical_polisher.md` | `prompts/eloquent_rewriter.md` |
| Validator | `Validator.validate` | `Validator.validate_rewrite` |
| Can be `safe_to_paste` | yes | **never** |
| State | disabled | enabled, owner toggles per dictation |

## What `validate_rewrite` checks

It compares the raw transcript and the rewrite by **presence** of protected
values (negation, numbers, dates, units, hedging, attribution, laterality,
routes, frequencies) rather than by count. "No fever and no chills" becoming
"no fever or chills" loses a word, not a fact. Separately, it reports:

- protected values in the transcript that are missing from the rewrite
- protected values in the rewrite that were never said
- capitalised words that were not said (invented names)
- unrecognised words the rewrite introduced
- a rewrite longer than the transcript

The result is always `review_required`. The app shows the rewrite, then the
moved facts, then the original transcript, with **Paste** and **Paste
original** buttons.

**Limitation, stated plainly:** this is a floor. A dropped request or question
that contains no protected token is caught only by the reader, which is why the
original transcript is always on screen beside the rewrite.

## Why ADR 0001's objections no longer block this

ADR 0001 deferred `qwen2.5:14b` mainly because of memory: 9.1 GB resident on a
16 GB machine. This machine is an Apple M5 Pro with 48 GB, so the model and
Whisper fit side by side with room to spare. ADR 0001's latency estimate
(~4.4 s per sentence) was acceptable for correspondence then, and
correspondence is exactly this mode's scope. ADR 0001's third objection,
asymmetric fabrication risk, is why every rewrite is reviewed.

## Consequences

- `ollama` installed with Homebrew; LaunchAgent `local.whispern.ollama`.
  Remove with `scripts/setup_ollama.sh --remove`.
- `qwen2.5:14b` stored under `~/.ollama/models` (~9 GB).
- The polisher refuses Ollama cloud model names (`-cloud` / `:cloud`), because
  those forward prompts to ollama.com through an endpoint that passes the
  loopback check.
- Rewrite cases: `tests/fixtures/synthetic/rewrite_cases.yaml`, run with
  `scripts/eval_polisher.py --rewrite`. Deterministic checks:
  `tests/test_rewrite.py`.

## Rollback

Set `rewrite.enabled: false` in `config/app.yaml`. The app then shows "rewrite
unavailable" and behaves exactly as before this ADR. To remove the runtime as
well: `scripts/setup_ollama.sh --remove`, `ollama rm qwen2.5:14b`,
`brew uninstall ollama`.

## Evaluation at adoption (2026-10-07, `qwen2.5:14b`, temperature 0)

`scripts/eval_polisher.py --rewrite --model qwen2.5:14b`: **9/12**, identical
across two runs. Rewrites are 60–89 % of the transcript's length, at
1.2–2.8 s each with the model warm.

| Case | Result | Assessment |
|---|---|---|
| `email_hedged_deadline` | "I think I can probably get…" → "I think I can get…" | **Real defect.** One hedge of two dropped. `validate_rewrite` reports it to the reader in red. |
| `research_result_not_strengthened` | "we really can't say there's an effect" → "we cannot conclude there is an effect" | Meaning kept; the case matches exact wording. Left unchanged rather than relaxed after seeing the result. |
| `email_laterality_and_dates` | kept "due on March 3rd, not March 13th" | Arguably correct: the speaker corrects an earlier message, so the contrast informs the recipient. The case's `must_drop` is questionable; left unchanged for the owner to decide. |

Fixed during evaluation: `blaKPC` → `BLA-KPC` (rule 7, technical terms
verbatim, using a different gene as the prompt example to avoid teaching to
the case), and "probably" → "likely" (rule 4 example).

Regression: the constrained clinical polisher still passes 21/21
(`polisher_cases.yaml`); `pytest` 108 passed; `offline_smoke_test.sh` 22/22,
including no non-loopback sockets from Ollama during a rewrite and no
dictated canary text in the Ollama log.

## Model comparison: `qwen2.5:14b` vs `qwen2.5:32b` (2026-10-07)

Same prompt, same 12 rewrite cases, two runs each (identical at temperature 0).

| | 14b | 32b |
|---|---|---|
| Rewrite cases | 9/12 | 9/12 |
| Clinical polisher cases (21) | 21/21 | 21/21 |
| Rewrite latency, warm | 1.2–2.8 s | 3.0–6.9 s |
| Resident memory (32k context) | 15 GB | 28 GB |

The two models fail different cases, and neither is clearly more faithful:

| Behaviour | 14b | 32b |
|---|---|---|
| "I think I can probably…" | drops "probably" | drops "I think" |
| "we should probably address both" | drops "probably" | drops "probably" |
| "not March 13th like I said earlier" | keeps the contrast | drops it |
| "I'm going to have to miss… sorry" | "Sorry about that." | "Apologies for any inconvenience." (more formal, not said) |
| "don't tell the sponsor it's in" | "that it is in" | "that it has been submitted" (paraphrase, same meaning) |

**Decision: keep `qwen2.5:14b`.** Equal score, 2–2.5× faster, half the
memory, and its wording stays closer to the speaker's. 32b's extra formality
("Apologies for any inconvenience") adds phrasing the speaker did not use,
which is the wrong direction for this mode. Every hedge loss, in both models,
is reported by `validate_rewrite` in the review view.

`qwen2.5:32b` remains installed (19 GB); `ollama rm qwen2.5:32b` removes it.
