# CLAUDE.md — Local Medical Dictation and Polishing

## 1. Mission

Build and maintain a fully local, privacy-preserving dictation workflow that approximates the useful behavior of Wispr Flow without sending audio, transcripts, prompts, or medical terminology to a cloud service.

The system must:

1. Capture speech from a macOS hotkey (FN) or desktop dictation application.
2. Transcribe speech locally with Whisper through `whisper.cpp`.
3. Improve recognition of medical terminology using a curated local dictionary and transcription context.
   1. you will need to identify this dictionary from a remote source and download it locally 
4. Pass the raw transcript through a constrained local medical-polishing step.
5. Protect clinically critical content such as negation, numbers, doses, units, dates, laterality, organisms, and medication names.
6. Paste only the reviewed or validated polished text into the active application.
7. Preserve human responsibility for the final wording.

This is a dictation and editing tool. It is not a diagnostic system, clinical decision-support system, or autonomous medical documentation system.

---

## 2. Default implementation decision

Use the following default stack unless the user approves a change:

- **Speech-to-text engine:** `whisper.cpp`.
- **Default transcription model:** `large-v3-turbo`.
- **Fallback transcription model:** `medium.en` or `small.en` when latency is unacceptable.
- **Polishing engine:** A local instruction-following language model exposed through a local-only interface.
- **Medical terminology layer:** Local text files containing approved medical terms, abbreviations, medication names, organism names, and exact replacements.
- **Validation layer:** Deterministic code that compares the raw and polished text and flags clinically important changes.

Do not add a cloud fallback. Disable any existing automatic cloud fallback before real-world use.

---

## 3. Non-negotiable constraints

### 3.1 Privacy

- Runtime transcription and polishing must work with the network disabled.
- Do not transmit audio, transcripts, prompts, telemetry, crash dumps, or dictionaries to external services or host remotely.
- Do not enable cloud transcription, cloud language models, remote analytics, or automatic cloud fallback.
- Use synthetic dictation for all automated tests.
- Do not persist raw audio by default.
- Delete temporary audio immediately after transcription.
- Do not place patient audio, transcripts, or identifiable text in Git.
- Do not include protected health information in application logs.
- Treat clipboard history, macOS crash reports, shell history, and debug logs as possible leakage paths.

### 3.2 Clinical fidelity

The polishing step may improve spelling, punctuation, capitalization, paragraphing, and likely speech-recognition errors. It must not:

- Add a diagnosis, assessment, plan, symptom, medication, dose, laboratory value, imaging result, or physical finding.
- Remove uncertainty, hedging, or attribution.
- Convert a differential diagnosis into a confirmed diagnosis.
- Change positive to negative or negative to positive.
- Change laterality.
- Change a number, dose, unit, date, time, duration, frequency, route, or concentration without an exact deterministic rule.
- Expand an abbreviation when more than one expansion is clinically plausible.
- replace a medical term merely because another term sounds more likely.

When uncertain, preserve the raw wording and add a review flag.

### 3.3 Human review

- Keep the raw transcript available until the user accepts the polished text.
  - then immediately delete 
- Provide a visible diff or a review indicator for clinically important changes.
- Never silently alter protected tokens.
- The user remains responsible for all text entered into a clinical record, email, manuscript, note, or order.

### 3.4 Proposal before implementation

Before making a substantive code or architecture change:

1. Summarize the proposed change.
2. Explain why it is needed.
3. Identify privacy and clinical-fidelity risks.
4. List files that will be changed.
5. Define tests and acceptance criteria.
6. Wait for human approval when the change affects privacy, storage, external connectivity, or clinical text transformation.

Small formatting fixes and clearly reversible documentation changes may proceed directly.

---

## 4. Target architecture

```text
Microphone / hotkey
        |
        v
Local desktop recorder
        |
        v
Temporary 16 kHz mono WAV
        |
        v
whisper.cpp + medical context
        |
        +----------------------+
        |                      |
        v                      v
Raw transcript          ASR metadata
        |
        v
Deterministic normalization
        |
        v
Local medical-polishing model
        |
        v
Critical-token validator
        |
        +-------------------------------+
        |                               |
        v                               v
Validated polished text          Review-required result
        |                               |
        v                               v
Paste at cursor                  Show raw/polished diff
```

The raw transcript is the authoritative source for auditing transformations. The polishing model is an editor, not the source of clinical facts.

---

## 5. Repository layout

Create or adapt this structure:

```text
local-medical-dictation/
├── CLAUDE.md
├── README.md
├── .gitignore
├── config/
│   ├── app.yaml
│   ├── protected_patterns.yaml
│   ├── exact_replacements.yaml
│   └── specialties.yaml
├── dictionaries/
│   ├── general_medicine.txt
│   ├── infectious_diseases.txt
│   ├── medications.txt
│   ├── microorganisms.txt
│   ├── procedures.txt
│   ├── ... institutions_and_people.local.txt
│   └── abbreviations.yaml
├── prompts/
│   └── medical_polisher.md
├── scripts/
│   ├── bootstrap_macos.sh
│   ├── transcribe_local.sh
│   ├── build_asr_prompt.py
│   ├── normalize_transcript.py
│   ├── polish_transcript.py
│   ├── validate_changes.py
│   ├── offline_smoke_test.sh
│   └── benchmark.py
├── src/
│   ├── pipeline/
│   ├── privacy/
│   ├── validation/
│   └── integrations/
├── tests/
│   ├── fixtures/
│   │   └── synthetic/
│   ├── test_normalization.py
│   ├── test_protected_tokens.py
│   ├── test_medical_polisher.py
│   └── test_offline_operation.py
└── runtime/
    ├── tmp/
    └── audit/
```

The `runtime/` directory must be excluded from Git. The file `institutions_and_people.local.txt` must also be excluded from Git because it may contain names or local vocabulary.

Recommended `.gitignore` entries:

```gitignore
runtime/
*.wav
*.m4a
*.mp3
*.aiff
*.flac
*.transcript.txt
*.raw.txt
*.polished.txt
.env
.env.*
dictionaries/institutions_and_people.local.txt
.DS_Store
```

---

## 6. Execution plan

Execute the phases in order. Do not begin a later phase until the current phase passes its review gate.

---

### Phase 0 — Inspect and document the machine

#### Tasks

1. Confirm the Mac architecture:
   ```bash
   uname -m
   sw_vers
   sysctl -n machdep.cpu.brand_string 2>/dev/null || true
   ```

2. Record:
   - macOS version
   - Apple Silicon generation
   - available memory
   - free disk space
   - microphone permissions
   - intended dictation application
   - whether the application launches at login

3. Inspect whether these tools are already installed:
   ```bash
   command -v brew || true
   command -v git || true
   command -v cmake || true
   command -v ffmpeg || true
   command -v python3 || true
   command -v whisper-cli || true
   ```

4. Create `docs/environment.md` containing the results. Do not include usernames, hostnames, patient data, tokens, or secrets.

#### Review gate

Report the machine configuration and proposed installation actions before installing or modifying system software.

---

5. 

#### Direct `whisper.cpp` path

```bash
brew install cmake ffmpeg
git clone https://github.com/ggml-org/whisper.cpp.git
cd whisper.cpp

sh ./models/download-ggml-model.sh large-v3-turbo

cmake -B build
cmake --build build -j --config Release

ffmpeg -i input.m4a -ar 16000 -ac 1 -c:a pcm_s16le output.wav

./build/bin/whisper-cli \
  -m models/ggml-large-v3-turbo.bin \
  -f output.wav \
  -l en \
  -otxt
```

If the binary or model filename differs in the installed version, inspect the repository and use the actual generated path. Do not guess.

#### Baseline test phrases

Use synthetic phrases containing ordinary medical language:

```text
The patient reports three days of dysuria and left flank pain.
Blood cultures grew methicillin-susceptible Staphylococcus aureus.
The differential includes pyelonephritis, nephrolithiasis, and musculoskeletal pain.
Continue cefazolin two grams intravenously every eight hours.
There is no evidence of endocarditis on transthoracic echocardiography.
```

Save the baseline errors in `docs/baseline-errors.md`. Do not improve the text manually before measuring the baseline.

#### Review gate

Demonstrate that transcription works while the network is disabled. Report latency, raw transcription errors, model size, and temporary-file behavior.

---

### Phase 2 — Add medical terminology to speech recognition

Medical terminology support begins before the polishing model. Improve the raw transcript first.

#### 2.1 Create curated dictionaries

Each line in a `.txt` dictionary must contain one approved term or phrase.

Example `dictionaries/infectious_diseases.txt`:

```text
methicillin-resistant Staphylococcus aureus
methicillin-susceptible Staphylococcus aureus
Enterococcus faecalis
Enterococcus faecium
Pseudomonas aeruginosa
Clostridioides difficile
extended-spectrum beta-lactamase
carbapenem-resistant Enterobacterales
vancomycin-resistant enterococcus
Pneumocystis jirovecii
nontuberculous mycobacteria
Mycobacterium tuberculosis
human immunodeficiency virus
antiretroviral therapy
opportunistic infection
infective endocarditis
prosthetic joint infection
central line-associated bloodstream infection
```

Example `dictionaries/medications.txt`:

```text
cefazolin
cefepime
ceftriaxone
ceftazidime
piperacillin-tazobactam
vancomycin
daptomycin
linezolid
trimethoprim-sulfamethoxazole
amoxicillin-clavulanate
dolutegravir
bictegravir
tenofovir alafenamide
emtricitabine
```

Do not treat these examples as a complete formulary.

#### 2.2 Add abbreviations carefully

Use a structured file:

```yaml
# dictionaries/abbreviations.yaml
unambiguous:
  MRSA: methicillin-resistant Staphylococcus aureus
  MSSA: methicillin-susceptible Staphylococcus aureus
  HIV: human immunodeficiency virus
  ART: antiretroviral therapy

ambiguous:
  MS:
    - multiple sclerosis
    - mitral stenosis
    - morphine sulfate
  PT:
    - physical therapy
    - prothrombin time
  PCP:
    - Pneumocystis pneumonia
    - primary care physician
```

Do not automatically expand entries under `ambiguous`.

#### 2.3 Build the ASR context prompt

Create `scripts/build_asr_prompt.py` that:

1. Loads approved dictionary files.
2. Selects only terms relevant to the active specialty or application mode.
3. Deduplicates terms case-insensitively.
4. Limits prompt length to the transcription engine's practical context limit.
5. Produces natural text rather than an unstructured word dump.
6. Never includes patient-specific information.

Example generated context:

```text
This is English medical dictation in infectious diseases and internal medicine.
Possible terminology includes Staphylococcus aureus, Enterococcus faecium,
Pseudomonas aeruginosa, Clostridioides difficile, cefazolin, cefepime,
vancomycin, daptomycin, infective endocarditis, bacteremia, and osteomyelitis.
Preserve medication names, microorganism names, numbers, units, and negation.
```

Pass this through the application's custom dictionary feature or Whisper's initial prompt capability.

#### 2.4 Exact replacements

Use `config/exact_replacements.yaml` only for high-confidence mappings:

```yaml
replacements:
  "staff aureus": "Staphylococcus aureus"
  "c diff": "C. difficile"
```

Each replacement must have:

- A synthetic failing example.
- A synthetic passing example.
- A reason it cannot create a dangerous false correction.
- Human approval before activation.

Do not create broad phonetic replacements that can alter unrelated words.

#### Review gate

Repeat the baseline tests and report the change in overall transcription error and medical-term error. Keep examples of both improvements and regressions.

---

### Phase 3 — Implement constrained medical polishing

The polishing stage runs after local transcription and deterministic normalization.

#### 3.1 Local-only model interface

Implement a provider-neutral local interface:

```python
class LocalPolisher:
    def polish(
        self,
        raw_text: str,
        glossary: list[str],
        mode: str,
    ) -> dict:
        ...
```

Requirements:

- Bind only to `127.0.0.1` or use an in-process model.
- Never expose the service to the local network.
- Do not require an external API key.
- Fail closed when the local model is unavailable.
- Do not fall back to a cloud provider.
- Set temperature to zero or the lowest supported deterministic value.
- Return structured output.
- Enforce a timeout.
- Limit retries.
- Do not log prompt contents.

#### 3.2 Medical-polishing prompt

Create `prompts/medical_polisher.md` with the following content:

```text
You are a constrained editor for medical speech-recognition output.

Your only task is to improve spelling, capitalization, punctuation, paragraphing,
and highly likely speech-recognition errors while preserving the speaker's exact
clinical meaning.

RULES

1. Do not add clinical facts.
2. Do not remove clinical facts.
3. Do not infer a diagnosis, assessment, plan, medication, dose, result, or finding.
4. Preserve all uncertainty, hedging, attribution, and differential language.
5. Preserve negation exactly. Words such as "no," "not," "without," "denies,"
   "negative," and "unlikely" are protected.
6. Preserve numbers, dates, times, doses, frequencies, concentrations, routes,
   percentages, ranges, and units unless an exact deterministic correction is supplied.
7. Preserve laterality and anatomical location.
8. Preserve medication and microorganism names unless the glossary supports a
   clear spelling correction.
9. Do not expand an ambiguous abbreviation.
10. When a phrase is uncertain, leave the raw phrase unchanged and flag it.
11. Do not make the text more certain, more severe, or more clinically polished
    by inventing information.
12. Return valid JSON only.

OUTPUT SCHEMA

{
  "polished_text": "string",
  "flags": [
    {
      "raw_span": "string",
      "reason": "ambiguous_term | protected_change | uncertain_correction | unknown_term",
      "suggestion": "string or null"
    }
  ],
  "changed_medical_terms": [
    {
      "raw": "string",
      "polished": "string",
      "basis": "glossary | exact_replacement | punctuation_only"
    }
  ]
}
```

The application must reject output that does not match the schema.

#### 3.3 Processing modes

Implement explicit modes:

- `general`: punctuation, capitalization, and paragraphing only.
- `medical`: general editing plus glossary-supported spelling correction.
- `clinical_strict`: medical mode plus mandatory review for every protected-token change.
- `research`: preserve statistical notation, gene names, organism names, package names, and acronyms.
- `email`: improve readability while preserving all medical facts and uncertainty.

Default to `clinical_strict` in clinical applications.

#### 3.4 Deterministic normalization before the model

Perform only low-risk transformations before invoking the model:

- Normalize repeated whitespace.
- Normalize obvious sentence spacing.
- Standardize Unicode apostrophes and quotation marks when needed.
- Preserve line breaks when they encode structure.
- Do not alter numbers, units, medication names, abbreviations, or negation in this step.

#### Review gate

Show at least 20 synthetic examples containing medication names, organisms, abbreviations, negation, uncertainty, and doses. Include both successful corrections and deliberately ambiguous cases that remain unchanged.

---

### Phase 4 — Protect clinically important tokens

Create a deterministic validator that compares raw and polished text.

#### 4.1 Protected categories

At minimum detect:

- Negation: `no`, `not`, `without`, `denies`, `negative`, `never`, `unlikely`
- Numbers and ranges
- Decimal points
- Percentages
- Dates and times
- Medication doses
- Units
- Routes: `PO`, `IV`, `IM`, `SC`, `inhaled`, `topical`
- Frequencies: `daily`, `BID`, `TID`, `q8h`, `every eight hours`
- Laterality: `left`, `right`, `bilateral`
- Anatomical sites
- Allergies
- Organism names
- Medication names
- Laboratory values
- Diagnostic-test results
- Uncertainty terms: `possible`, `probable`, `suspected`, `may`, `might`, `could`
- Attribution terms: `patient reports`, `family states`, `outside records indicate`

#### 4.2 Validation behavior

For every protected item:

1. Extract the raw token and normalized form.
2. Extract corresponding tokens from the polished text.
3. Compare values and context.
4. Permit punctuation-only changes.
5. Permit an approved exact replacement.
6. Otherwise mark the result `review_required`.
7. Never auto-paste a `review_required` result in `clinical_strict` mode.

Return:

```json
{
  "status": "safe_to_paste | review_required | rejected",
  "raw_text": "...",
  "polished_text": "...",
  "protected_changes": [],
  "warnings": []
}
```

Reject the result if:

- The local model added a sentence.
- A protected number disappeared.
- Negation changed.
- Laterality changed.
- A dose, route, or frequency changed.
- The output is empty or malformed.
- The output contains content not traceable to the raw transcript.

#### Review gate

All adversarial protected-token tests must pass before enabling automatic paste.

---

### Phase 5 — Integrate with the desktop application

#### Tasks

1. Identify the application's post-transcription hook.
2. Pass the raw transcript to the local polishing service.
3. Validate the returned structured result.
4. In `safe_to_paste`, paste only `polished_text`.
5. In `review_required`, display:
   - raw text
   - polished text
   - highlighted protected changes
   - reasons for each flag
6. In `rejected`, return the raw transcript and an error message.
7. Delete temporary audio.
8. Clear sensitive in-memory buffers when practical.
9. Do not save transcript history by default.
10. Add a visible mode indicator so the user knows whether polishing is active.

Do not modify the upstream desktop application until the standalone transcription, polishing, and validation pipeline passes tests.

---

### Phase 6 — Build the synthetic medical test suite

Use synthetic or publicly constructed phrases only.

#### Required test groups

1. **Negation**
   ```text
   There is no evidence of infective endocarditis.
   The patient does not report dysuria.
   Blood cultures remain negative.
   ```

2. **Uncertainty**
   ```text
   This may represent contaminant bacteremia.
   Osteomyelitis remains possible but unconfirmed.
   ```

3. **Medication and dose**
   ```text
   Continue cefazolin two grams IV every eight hours.
   Reduce vancomycin to one gram every twelve hours.
   ```

4. **Organisms**
   ```text
   Cultures grew methicillin-susceptible Staphylococcus aureus.
   The isolate was identified as Enterococcus faecium.
   ```

5. **Numbers and units**
   ```text
   The creatinine increased from 0.9 to 1.4 milligrams per deciliter.
   The temperature was 38.2 degrees Celsius.
   ```

6. **Laterality**
   ```text
   There is erythema of the left lower extremity.
   The right knee is more swollen than the left.
   ```

7. **Ambiguous abbreviations**
   ```text
   The patient will follow up with PT.
   The outside note lists MS.
   ```

8. **Research terminology**
   ```text
   We fit a Bayesian hierarchical model with a beta-binomial likelihood.
   The analysis used whole-genome sequencing and pairwise genetic relatedness.
   ```

9. **Adversarial additions**
   Confirm the polisher does not add:
   - a diagnosis
   - an antibiotic recommendation
   - a duration of treatment
   - a normal finding
   - a laboratory result
   - a justification that was not spoken

#### Metrics

Report:

- Overall word error rate.
- Medical-term error rate.
- Medication-name accuracy.
- Microorganism-name accuracy.
- Number and unit preservation.
- Negation preservation.
- Laterality preservation.
- Unsupported-content rate.
- Median and 95th-percentile latency.
- Fraction requiring review.

The unsupported-content rate must be zero in the synthetic acceptance suite.

---

### Phase 7 — Offline and privacy verification

Create `scripts/offline_smoke_test.sh`.

The test must:

1. Confirm cloud-provider environment variables are absent.
2. Confirm configured endpoints use only local paths or loopback addresses.
3. Disable network access or run while physically disconnected.
4. Transcribe a synthetic audio sample.
5. Polish and validate it.
6. Confirm no external connection is required.
7. Confirm temporary audio is deleted.
8. Inspect logs for transcript content.
9. Confirm the pipeline fails closed if the local model is stopped.
10. Produce a redacted test report.

Useful inspection commands may include:

```bash
lsof -i -n -P
netstat -anv
find runtime/tmp -type f -maxdepth 1
```

Do not claim the system is offline-safe solely because the application settings say "local." Verify behavior empirically.

---

### Phase 8 — Deployment

Only deploy after Phases 0–7 pass.

#### Deployment checklist

- [ ] Local transcription works with the network disabled.
- [ ] Local polishing works with the network disabled.
- [ ] Cloud fallback is disabled.
- [ ] Temporary audio is deleted.
- [ ] Transcript logging is disabled.
- [ ] Protected-token validator is active.
- [ ] `clinical_strict` mode requires review when protected content changes.
- [ ] Synthetic adversarial tests pass.
- [ ] The user has reviewed the glossary.
- [ ] The user has reviewed all exact replacements.
- [ ] The active model and configuration are documented.
- [ ] A rollback procedure exists.
- [ ] The application visibly indicates its active mode.

Start with nonclinical email and research dictation. Do not begin with direct entry into a medical record.

---

## 7. Configuration example

Create `config/app.yaml`:

```yaml
transcription:
  engine: whisper_cpp
  model: large-v3-turbo
  language: en
  local_only: true
  cloud_fallback: false
  save_audio: false
  save_transcripts: false
  initial_prompt_enabled: true

polishing:
  enabled: true
  provider: local
  endpoint: http://127.0.0.1:11434
  model: REPLACE_WITH_APPROVED_LOCAL_MODEL
  temperature: 0
  mode: clinical_strict
  timeout_seconds: 20
  retries: 0
  cloud_fallback: false

validation:
  protect_negation: true
  protect_numbers: true
  protect_units: true
  protect_dates: true
  protect_laterality: true
  protect_medications: true
  protect_organisms: true
  protect_uncertainty: true
  require_review_on_protected_change: true

storage:
  temporary_directory: runtime/tmp
  delete_temporary_audio: true
  transcript_history: false
  prompt_logging: false
  response_logging: false
```

The endpoint is an example loopback address. Use the actual local runtime and verify that it is not bound to `0.0.0.0` or a LAN interface.

---

## 8. Medical dictionary maintenance

### Adding a term

For every new term:

1. Add the correctly spelled term to the relevant dictionary.
2. Add at least one synthetic dictation example.
3. Add known speech-recognition variants only when they are reproducible.
4. Avoid patient-specific names or identifiers.
5. Run the test suite.
6. Review regressions.
7. Commit the dictionary change separately from code changes.

### Adding an exact replacement

An exact replacement is higher risk than a dictionary term.

Require:

- A reproducible ASR error.
- A narrow match.
- No plausible nonmedical interpretation.
- No effect on numbers, negation, dose, units, or laterality.
- Human approval.
- A rollback entry.

### Specialty modes

Use separate dictionaries rather than one unbounded prompt:

```yaml
# config/specialties.yaml
general_internal_medicine:
  - general_medicine.txt
  - medications.txt

infectious_diseases:
  - general_medicine.txt
  - infectious_diseases.txt
  - medications.txt
  - microorganisms.txt

research:
  - infectious_diseases.txt
  - microorganisms.txt
  - procedures.txt
```

Load only the terms needed for the active mode.

---

## 9. Failure handling

### Transcription failure

- Return an error.
- Do not send audio to a cloud service.
- Preserve the temporary audio only long enough for an explicit user-approved retry.
- Delete it after failure handling.

### Polishing failure

- Return the raw transcript.
- Indicate that polishing was unavailable.
- Do not retry through an external provider.

### Validation failure

- Block automatic paste.
- Show raw and polished text.
- Highlight protected differences.
- Let the user choose the raw text, edited text, or cancellation.

### Model hallucination

- Reject the result.
- Save only a de-identified synthetic reproduction for debugging.
- Add a regression test.
- Do not weaken the validator to accommodate the model.

---

## 10. Change-management rules for Claude

When modifying this system:

1. Prefer small, reviewable commits.
2. Separate privacy changes, model changes, dictionary changes, and UI changes.
3. Do not combine an engine upgrade with prompt changes in the same benchmark.
4. Benchmark before and after every model, prompt, or dictionary change.
5. Preserve the previous working configuration.
6. Document exact model names and checksums when available.
7. Pin dependencies for reproducibility.
8. Review upstream licenses before redistributing binaries or models.
9. Never commit secrets, audio, transcripts, or locally identifying vocabulary.
10. Explain any change that could alter clinical wording.
11. Do not claim medical accuracy based on general word error rate alone.
12. Treat unsupported added content as a critical defect.

---

## 11. Initial deliverables

Claude should produce these deliverables in order:

1. `docs/environment.md`
2. A working offline raw-transcription demo
3. `docs/baseline-errors.md`
4. Initial medical dictionaries
5. `scripts/build_asr_prompt.py`
6. `prompts/medical_polisher.md`
7. A standalone local polishing command
8. `scripts/validate_changes.py`
9. Synthetic unit and integration tests
10. A raw-versus-polished review interface
11. `scripts/offline_smoke_test.sh`
12. `docs/validation-report.md`
13. A deployment and rollback guide

Do not skip directly to automatic pasting.

---

## 12. Definition of done

The first production candidate is complete only when:

1. It transcribes and polishes with the network disabled.
2. No cloud fallback is configured.
3. Temporary audio is deleted automatically.
4. It improves medical-term recognition on the synthetic benchmark.
5. It preserves all tested numbers, units, negation, uncertainty, and laterality.
6. It adds no unsupported clinical content.
7. It blocks auto-paste when protected content changes.
8. The user can inspect raw text, polished text, and flagged differences.
9. The glossary and exact-replacement rules have been reviewed.
10. The full setup can be reproduced from documented steps.

---

## 13. Primary implementation references

- OpenAI Whisper: https://github.com/openai/whisper
- whisper.cpp: https://github.com/ggml-org/whisper.cpp
- OpenWhispr: https://github.com/OpenWhispr/openwhispr
- VoiceInk: https://github.com/Beingpax/VoiceInk
