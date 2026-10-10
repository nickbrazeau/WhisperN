# Validation Report

Consolidated validation evidence for the local medical dictation pipeline.
CLAUDE.md §11 deliverable 12. Covers Phase 7 (offline and privacy
verification) and summarises the evidence from Phases 1–6.

Last updated: 2026-10-10.

---

## 1. Offline and privacy verification (Phase 7)

`scripts/offline_smoke_test.sh` — **16 checks, 16 pass, 0 fail, 0 warnings.**

Every check observes behaviour. CLAUDE.md §6 Phase 7 says not to claim the
system is offline-safe because settings say "local", so nothing here is
inferred from configuration.

| # | Check | Method | Result |
|---|---|---|---|
| 1 | No cloud credentials in environment | `env` scan, 13 provider prefixes | pass |
| 2 | No provider credential files | `~/.netrc`, `~/.config/openai`, … | pass |
| 3 | Configured endpoints are loopback | parse `config/*.yaml` | pass (1 endpoint) |
| 4 | Code *refuses* non-loopback | constructor rejects 4 hostile URLs | pass |
| 5 | Sandbox actually blocks network | sandboxed `curl` must fail | pass |
| 6 | Transcription works offline | `sandbox-exec` `(deny network*)` | pass |
| 7 | Validation works offline | same sandbox | pass |
| 8 | No sockets during transcription | `lsof` over the process tree | pass |
| 9 | No temporary audio survives | `find runtime/tmp` | pass |
| 10 | Cleanup survives SIGTERM | kill mid-transcription | pass |
| 11 | No transcripts persisted | `find runtime/` | pass |
| 12 | Source does not log transcripts | grep logging calls | pass |
| 13 | No audio tracked in git | `git ls-files` | pass |
| 14 | No transcripts tracked in git | `git ls-files` | pass |
| 15 | Polisher fails closed | point at closed port `127.0.0.1:9` | pass |
| 16 | No cloud provider in polisher source | grep | pass |

### Why the network denial is trustworthy

Network access is denied by the kernel through `sandbox-exec` with
`(deny network*)`, and **the profile is proven before it is relied on**: a
sandboxed `curl` to an external host must fail, and the suite aborts its
offline claims if it succeeds. This is stronger than disabling Wi-Fi, which
proves only that no route existed at that moment.

### A false positive found and fixed

The first run reported *"transcription process opened 120 non-loopback
sockets"*. That was a defect in the check, not the pipeline: **`lsof` ORs its
selection criteria unless `-a` is given**, so `lsof -p PID -i` listed every
internet socket on the machine — `rapportd`, `identityservicesd`, and other
macOS daemons. With `-a`, and walking the process tree so `whisper-cli` itself
is inspected, the count is zero.

Recorded because a privacy check that cannot distinguish this process from the
rest of the system is worse than no check: it produces alarming numbers nobody
can act on, and its real failures would be lost in the noise.

### Report redaction

Reports are written to `runtime/audit/` (gitignored) and contain check
outcomes, counts, and paths only. Verified: the generated report contains
**zero clinical terms**. Transcript word counts are recorded; transcript text
never is.

---

## 2. Clinical-fidelity evidence (Phases 4 and 6)

### Protected content

Across the real-voice and held-out corpora, **85 protected tokens**:

| Category | Preservation |
|---|---|
| Numbers and units | **100 %** (67/67) |
| Negation | **100 %** (8/8) |
| Laterality | **100 %** (10/10) |

No number, unit, negation, or laterality was altered in any test case.

### Validator

`tests/test_protected_tokens.py` — **48 tests passing.**

Rejects: dose change, negation removal, negation substitution
(`does not report` → `denies`), laterality flip, frequency change, route
change, added sentence, empty output, fabricated organism expansion.

Permits: capitalisation, punctuation, route synonyms (`intravenously` → `IV`),
spoken-number-to-digit rendering.

Detection of mangled names, measured on both corpora:

| Corpus | Flagged | True positives | False positives |
|---|---|---|---|
| Real voice | 3 | 3 | **0** |
| Held-out | 9 | 9 | **0** |

100 % precision, including on terminology never seen during development.

### Acceptance criterion

**Unsupported-content rate: 0 %** on both corpora — the Phase 6 gate.

Important qualification: polishing is disabled (ADR 0001), so polished text
*is* the raw transcript and this is zero by construction. True of the shipped
configuration; not evidence that a polisher would pass.

---

## 2a. Rewrite mode (ADR 0002)

Email/research rewrite with `qwen2.5:14b` through loopback Ollama, scored
against the 12 synthetic cases in `tests/fixtures/synthetic/rewrite_cases.yaml`.

| Input to the rewriter | Passed | How |
|---|---|---|
| Typed case text | 9 / 12 | `scripts/eval_polisher.py --rewrite --model qwen2.5:14b` |
| Synthetic speech → Whisper → rewrite | **5 / 12** | `scripts/eval_rewrite_audio.py` (2026-10-10, voice Samantha) |

The audio run attributes each failure to the stage that caused it:

| Case | Failure | Stage |
|---|---|---|
| `research_stats_numbers` | "6 sites" heard as "six" | ASR (numeral form) |
| `research_attribution` | "reviewer two" heard as "reviewer 2" | ASR (numeral form) |
| `research_gene_and_organism_names` | `blaKPC` heard as "black APC" | ASR |
| `research_result_not_strengthened` | "crossed one" heard as "crossed 1"; "can't say" became "cannot conclude" | ASR, and rewriter (wording; meaning kept) |
| `email_hedged_deadline` | **"probably" dropped** | Rewriter |
| `email_no_invented_reason` | "miss" became "unable to attend"; "Apologies for any inconvenience" added for "sorry about that" | Rewriter (wording) |
| `email_laterality_and_dates` | kept "not March 13th" as the contrast instead of dropping the retracted date | Rewriter (arguably correct) |

Three of the seven failures are numeral formatting rather than lost
meaning, and are left failing rather than relaxing the cases (CLAUDE.md §9).

**The finding that matters is hedging.** The rewriter drops hedge words
despite the prompt forbidding it: "probably" (twice), "maybe", and "I think"
→ "I believe" were seen in this run. `validate_rewrite` caught every dropped
"probably" and shows it under "Check against what you said"; it does not flag
"maybe" → "around" or "I think" → "I believe", because those are not in the
protected uncertainty list or are synonyms. Read rewrites for certainty.

Synthetic speech is cleaner than a person, so 5 / 12 is an optimistic bound.
The harness takes real recordings with `--audio-dir` (files named
`<case id>.wav`); that measurement is still owed.

### Clipboard

Copies are marked `org.nspasteboard.ConcealedType` and `TransientType`, and
the GUI clears the clipboard after a paste, after 2 minutes, and on quit,
only if nothing else was copied since. Verified in `tests/test_clipboard.py`
on a private named pasteboard, and through the app's own timer path. Whether
a given clipboard manager honours the markers is not verified here.

---

## 3. Known limitations

These are properties of the design, not defects to be fixed later, and they
bound what this system can claim.

### 3.1 ASR fabrication is inside the trusted baseline

The validator compares polished text **against the raw transcript**. Anything
the ASR invented is part of the reference and cannot be flagged as
unsupported. Observed: `in the past medical history` → `in the past Michael
Hex 3`.

Mitigation is partial — the VAD gate removes the non-speech case, and the
Phase 2 prompt reduced fabrication — but there is no detection for this class.
**Human review of the raw transcript is the only defence.**

### 3.2 A real word used wrongly is undetectable

Membership testing catches non-words (`septipine`, `creptococcus`,
`Urtapenem`). It cannot catch a plausible substitution of one real name for
another. Observed: `Pneumocystis jirovecii` → `Pneumocystis cerevisiae`, where
*cerevisiae* is a genuine organism.

### 3.3 Named-entity accuracy is poor

**62.5 %** medication-name accuracy on the real corpus. Roughly one
distinctive drug or organism name in three is mis-transcribed. Every dictation
containing drug names needs reading before use.

### 3.4 The ASR prompt does not generalise

Phase 2's gains were in-sample. On held-out terminology the prompt is worth
one error in 356 words. It is a patch list for observed failures, not a
general improvement.

### 3.5 Sample sizes are small

33 real and 39 synthetic utterances. Named-entity denominators are smaller
still (8, 6, 19, 8), so those percentages carry wide uncertainty — 62.5 % is
5 of 8. Both corpora are adversarial by construction and over-represent
difficult terminology.

### 3.6 Unmeasured

- Bluetooth microphone input (expected worse than built-in).
- Spontaneous speech with hesitation and self-correction from a real voice;
  rewrite mode has been measured on synthetic speech only (§2a).
- Any polishing model in production use.
- Specialty modes other than `infectious_diseases`.

---

## 4. What this build is and is not suitable for

**Suitable:** reviewed dictation where the user reads every word before use —
correspondence, note drafting, research writing.

**Not suitable:** direct entry into a medical record without review;
unattended or automated pasting; any workflow where a mis-transcribed drug
name would not be caught by the person dictating.

Since 2026-10-07 the default mode is `medical`, not `clinical_strict` (owner
decision): a result with validator warnings but no protected change may now
auto-paste. Protected changes still never auto-paste.

A paste-at-cursor path exists in the desktop GUI (`scripts/whispern_app.py`)
and is **off for everything except `safe_to_paste`**. `review_required` and
`rejected` are never pasted automatically under any setting. This does not
change the judgement above: "safe" means no protected token changed, not that
every word is right, so the one-in-three named-entity caveat still applies to
anything pasted. See `docs/deployment.md` §6.3.

CLAUDE.md §6 Phase 8 directs starting with nonclinical email and research
dictation, and not beginning with direct entry into a medical record. The
evidence here supports that sequencing.

---

## 5. Reproducing this report

```bash
scripts/offline_smoke_test.sh                      # 22 privacy/offline checks
.venv/bin/python -m pytest tests/ -q               # 123 tests
scripts/eval_rewrite_audio.py                      # rewrite mode, audio to rewrite (§2a)
scripts/benchmark.py runtime/tmp/corpus_prompted2 runtime/tmp/heldout_syn
```

The benchmark needs the recorded corpora under `runtime/`, which is gitignored
and machine-local. `scripts/record_baseline.sh` regenerates the real-voice
corpus; `scripts/make_synthetic_audio.sh` regenerates the synthetic ones.
