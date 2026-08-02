"""Tests for protected-token extraction and validation.

All fixtures are synthetic and describe no real person.

Every case here is derived from behaviour actually observed during
development, or from CLAUDE.md §4.2's reject list. Per CLAUDE.md §9 these are
never relaxed to accommodate a model.

Run: .venv/bin/python -m pytest tests/ -q
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "src"))

from pipeline.normalize import normalization_diff, normalize_transcript  # noqa: E402
from validation.protected import Category, extract_all, extract_numbers  # noqa: E402
from validation.validator import Status, Validator  # noqa: E402


@pytest.fixture(scope="module")
def validator() -> Validator:
    return Validator.from_dictionaries(REPO_ROOT / "dictionaries", "clinical_strict")


# ---------------------------------------------------------------- numbers

@pytest.mark.parametrize("spoken,digits", [
    ("two grams", "2 grams"),
    ("every eight hours", "every 8 hours"),
    ("seventy-two hours", "72 hours"),
    ("zero point nine", "0.9"),
    ("one point four", "1.4"),
    ("two hundred ten", "210"),
    ("eighteen thousand", "18,000"),
    ("six hundred milligrams", "600 milligrams"),
])
def test_spoken_and_digit_numbers_are_equivalent(spoken, digits):
    """The ASR renders spoken numbers as digits; that is not a change of value.

    Without this, every dose in every dictation would raise an alert, and an
    alert that always fires is ignored.
    """
    a = [t.normalized for t in extract_numbers(spoken)]
    b = [t.normalized for t in extract_numbers(digits)]
    assert a == b, f"{spoken!r} -> {a} but {digits!r} -> {b}"


@pytest.mark.parametrize("a,b", [
    ("2 grams", "3 grams"),
    ("0.9", "0.8"),
    ("every 8 hours", "every 12 hours"),
    ("38.2 degrees", "38.5 degrees"),
])
def test_different_values_are_not_equivalent(a, b):
    assert [t.normalized for t in extract_numbers(a)] != \
           [t.normalized for t in extract_numbers(b)]


# ---------------------------------------------------------------- units, routes

@pytest.mark.parametrize("a,b", [
    ("1.4 mg per deciliter", "1.4 milligrams per deciliter"),
    ("1 gram intravenously", "1 gram IV"),
    ("500 mg orally", "500 mg PO"),
])
def test_unit_and_route_synonyms_compare_equal(a, b):
    ta = {(t.category, t.normalized) for t in extract_all(a)}
    tb = {(t.category, t.normalized) for t in extract_all(b)}
    assert ta == tb


def test_different_routes_are_distinguished():
    a = {(t.category, t.normalized) for t in extract_all("1 gram IV")}
    b = {(t.category, t.normalized) for t in extract_all("1 gram PO")}
    assert a != b


# ---------------------------------------------------------------- rejections

@pytest.mark.parametrize("raw,polished,reason", [
    ("Continue cefazolin 2 grams intravenously every 8 hours",
     "Continue cefazolin 3 grams intravenously every 8 hours.", "dose changed"),
    ("There is no evidence of endocarditis",
     "There is evidence of endocarditis.", "negation removed"),
    ("there is erythema of the left lower extremity",
     "There is erythema of the right lower extremity.", "laterality flipped"),
    ("Continue vancomycin every 8 hours",
     "Continue vancomycin every 12 hours.", "frequency changed"),
    ("Give 500 mg orally",
     "Give 500 mg intravenously.", "route changed"),
    ("Blood cultures grew Staphylococcus aureus",
     "Blood cultures grew Staphylococcus aureus. Recommend echocardiogram.",
     "sentence added"),
    ("The patient has a fever", "", "empty output"),
])
def test_protected_changes_are_rejected(validator, raw, polished, reason):
    result = validator.validate(raw, polished)
    assert result.status == Status.REJECTED, f"{reason}: got {result.status.value}"


def test_negation_substitution_is_caught(validator):
    """'does not report' -> 'denies' is a different claim about the encounter.

    Observed from llama3.1:8b (docs/phase3-results.md §3.2).
    """
    result = validator.validate(
        "the patient does not report fever chills or night sweats",
        "The patient denies fever, chills, or night sweats.",
    )
    assert result.status == Status.REJECTED


def test_fabricated_organism_expansion_is_caught(validator):
    """The worst observed polisher failure must not reach the clipboard."""
    result = validator.validate(
        "The outside note lists MS in the past medical history",
        "The outside note lists methicillin-sensitive Staphylococcus aureus "
        "(MS) in the past medical history.",
    )
    assert result.status == Status.REJECTED


# ---------------------------------------------------------------- review

@pytest.mark.parametrize("raw,polished", [
    ("there is no evidence of endocarditis on transthoracic echocardiography",
     "There is no evidence of infective endocarditis on transthoracic "
     "echocardiography."),
    ("The patient will follow up with PT next week",
     "The patient will follow up with physical therapy next week."),
    ("Cultures grew Pseudomonas aeruginosa susceptible to cefepime",
     "Cultures grew Pseudomonas aeruginosa susceptible to septipine."),
])
def test_unsupported_additions_require_review(validator, raw, polished):
    result = validator.validate(raw, polished)
    assert result.status in (Status.REVIEW, Status.REJECTED)
    assert result.status != Status.SAFE


def test_unknown_drug_in_raw_transcript_forces_review(validator):
    """An ASR-mangled drug name must stop the pipeline, not be guessed at."""
    result = validator.validate(
        "Start pipercellum tazabactam 4.5 grams every 6 hours",
        "Start pipercellum tazabactam 4.5 grams every 6 hours.",
    )
    assert result.status != Status.SAFE
    assert any("not in the approved vocabulary" in w for w in result.warnings)


# ---------------------------------------------------------------- safe

@pytest.mark.parametrize("raw,polished", [
    ("Continue cefazolin 2 grams intravenously every 8 hours",
     "Continue cefazolin 2 grams intravenously every 8 hours."),
    ("the patient reports three days of dysuria and left flank pain",
     "The patient reports three days of dysuria and left flank pain."),
    ("Reduce vancomycin to 1 gram intravenously every 12 hours",
     "Reduce vancomycin to 1 gram IV every 12 hours"),
])
def test_benign_edits_are_safe(validator, raw, polished):
    """Capitalization, punctuation, and route synonyms must not raise alerts.

    A validator that fires on these would be turned off within a day.
    """
    result = validator.validate(raw, polished)
    assert result.status == Status.SAFE, \
        f"expected safe, got {result.status.value}: {result.warnings}"


# ---------------------------------------------------------------- normalization

@pytest.mark.parametrize("text", [
    "Continue cefazolin 2 grams every 8 hours",
    "The creatinine increased from 0.9 to 1.4 mg/dL",
    "There is no evidence of endocarditis",
    "38.2 degrees Celsius; WBC 18,000",
])
def test_normalization_preserves_content(text):
    out = normalize_transcript(text)
    assert not normalization_diff(text, out)


def test_normalization_collapses_whitespace_only():
    raw = "The  patient   has\n\n\n\na fever ."
    out = normalize_transcript(raw)
    assert out == "The patient has\n\na fever."
    assert not normalization_diff(raw, out)


def test_normalization_does_not_touch_digits():
    raw = "0.9 to 1.4 mg, 18,000 WBC, 38.2 C"
    out = normalize_transcript(raw)
    assert "0.9" in out and "1.4" in out and "18,000" in out and "38.2" in out


def test_normalization_standardizes_smart_quotes():
    raw = "the patient’s fever “improved”"
    out = normalize_transcript(raw)
    assert "'" in out and '"' in out
    assert "’" not in out and "“" not in out


# ---------------------------------------------------------------- categories

def test_all_required_categories_are_detectable():
    """Every category CLAUDE.md §4.1 names must actually be extracted."""
    text = ("The patient denies fever. Continue 500 mg IV every 8 hours to the "
            "left arm. This may represent contamination. Patient reports "
            "improvement.")
    found = {t.category for t in extract_all(text)}
    for required in (Category.NEGATION, Category.NUMBER, Category.UNIT,
                     Category.ROUTE, Category.FREQUENCY, Category.LATERALITY,
                     Category.UNCERTAINTY):
        assert required in found, f"{required.value} not extracted"


# ---------------------------------------------------------------- VAD gate

@pytest.fixture(scope="module")
def pipeline():
    from pipeline.dictation import DictationPipeline
    return DictationPipeline(repo_root=REPO_ROOT)


@pytest.mark.parametrize("name", ["silence.wav", "roomtone.wav"])
def test_vad_gate_rejects_non_speech(pipeline, name):
    """Whisper invents text from non-speech; the gate must stop it reaching the model.

    Without the gate, digital silence transcribes as "you" and room tone as
    "-". No downstream stage can detect this: the validator compares polished
    text against the raw transcript, so an ASR fabrication is already inside
    the trusted baseline.
    """
    audio = REPO_ROOT / "runtime/tmp/vadtest" / name
    if not audio.exists():
        pytest.skip(f"fixture {name} not generated")
    assert pipeline.has_speech(audio) is False


def test_vad_gate_accepts_all_real_speech(pipeline):
    """A false negative silently discards a dictation, which is worse than a
    false positive. All 33 recorded utterances must pass the gate."""
    wavs = sorted((REPO_ROOT / "runtime/tmp/corpus_real").glob("phrase_*.wav"))
    if not wavs:
        pytest.skip("real corpus not recorded")
    missed = [w.name for w in wavs if not pipeline.has_speech(w)]
    assert not missed, f"gate discarded real speech: {missed}"


# ---------------------------------------------------------------- GUI invariant

def test_gui_never_auto_pastes_unsafe_results():
    """The GUI must not auto-paste review_required or rejected output.

    CLAUDE.md §4.2: never auto-paste a review_required result in
    clinical_strict. This is a source-level check because the behaviour is a
    branch in the render path, and a refactor could silently move the paste
    call outside the SAFE guard.
    """
    src = (REPO_ROOT / "scripts/whispern_app.py").read_text()

    safe_branch = src.split("if result.outcome is Outcome.SAFE:")[1] \
                     .split("# review_required")[0]
    assert "self._paste" in safe_branch, "safe branch should offer paste"

    review_branch = src.split("# review_required / rejected")[1].split("def ")[0]
    assert "self._paste" not in review_branch, \
        "review/rejected branch must never call _paste"
    assert "self.root.after" not in review_branch, \
        "review/rejected branch must not schedule a deferred paste"


def _paste_body() -> str:
    """Source of _paste with its docstring removed.

    The docstring names withdraw() to explain why it must not be used, so the
    guards below have to read the code rather than the prose.
    """
    src = (REPO_ROOT / "scripts/whispern_app.py").read_text()
    fn = src.split("def _paste(")[1].split("\n    def ")[0]
    parts = fn.split('"""')
    return parts[2] if len(parts) >= 3 else fn


def test_gui_paste_does_not_hide_the_window():
    """_paste must not go back to yielding focus by hiding the window.

    withdraw() does not transfer frontmost status, so the keystroke was
    delivered to our own hidden window and discarded -- measured 0/9 at sleeps
    from 250 ms to 1200 ms, while osascript still exited 0 and the UI showed a
    green "Pasted". A refactor reaching for withdraw() again would silently
    reintroduce a paste that reports success and does nothing.
    """
    body = _paste_body()
    for banned in ("withdraw(", "deiconify("):
        assert banned not in body, f"_paste must not call {banned}"


def test_gui_paste_fails_closed_without_a_known_target():
    """No target means no keystroke.

    ⌘V is delivered to whatever is frontmost. Sending it without knowing where
    it will land is how clinical text reaches the wrong window; during
    development exactly this pasted test markers into a terminal. The guard
    must come before paste_into() and must return.
    """
    body = _paste_body()

    guard = body.index("if not target:")
    call = body.index("paste_into(")
    assert guard < call, "the no-target guard must precede paste_into()"
    assert "return" in body[guard:call], "the no-target guard must return"


def test_focus_tracker_never_targets_itself():
    """Our own process must never become the paste target.

    If it did, activating the 'target' would focus this window and ⌘V would
    type the transcript into the app's own result box.
    """
    sys.path.insert(0, str(REPO_ROOT / "scripts"))
    from whispern_app import FocusTracker

    t = FocusTracker(self_name="python3")
    assert t.target is None

    t.consider("python3")
    assert t.target is None, "must not target our own process"
    t.consider("")
    assert t.target is None, "must not target an empty name"
    t.consider(None)
    assert t.target is None, "must not target a missing name"

    t.consider("Notes")
    assert t.target == "Notes"
    t.consider("python3")
    assert t.target == "Notes", "our own process must not clear a good target"


def test_applescript_literals_are_escaped():
    """An application name is interpolated into an AppleScript literal.

    Names containing a quote or backslash would otherwise break out of the
    string and change what the script does.
    """
    sys.path.insert(0, str(REPO_ROOT / "scripts"))
    from whispern_app import _as_literal

    assert _as_literal('Fo"o') == 'Fo\\"o'
    assert _as_literal("Fo\\o") == "Fo\\\\o"


# ---------------------------------------------------------------- local vocabulary


def _dictionaries_with_local(tmp_path: Path, entries: str) -> Path:
    """A dictionary directory carrying a local proper-noun list.

    Built in a temp directory rather than reusing the real one, because
    `institutions_and_people.local.txt` is gitignored (CLAUDE.md §5) and is
    absent on a fresh checkout. A test that depended on it would pass here and
    fail in a clean clone.
    """
    d = tmp_path / "dictionaries"
    d.mkdir()
    for name in ("medications.txt", "microorganisms.txt", "general_medicine.txt",
                 "infectious_diseases.txt", "procedures.txt", "research_terms.txt"):
        src = REPO_ROOT / "dictionaries" / name
        (d / name).write_text(src.read_text(encoding="utf-8") if src.exists() else "",
                              encoding="utf-8")
    (d / "institutions_and_people.local.txt").write_text(entries, encoding="utf-8")
    return d


def test_local_proper_nouns_are_not_flagged_as_drug_like(tmp_path):
    """A listed proper noun stops tripping the unknown-word check.

    "Google" in ordinary email opened a clinical review dialog, and a review
    nobody reads is worse than no review.
    """
    v = Validator.from_dictionaries(
        _dictionaries_with_local(tmp_path, "# local\nGoogle\nSharePoint\n"),
        "clinical_strict")
    assert v._unrecognized("Google") == []
    assert v._unrecognized("SharePoint") == []


def test_local_vocabulary_does_not_mask_mangled_drug_names(tmp_path):
    """The safety property the local list must never cost.

    Suppressing proper nouns must not suppress the ASR errors this check
    exists for. `pipercellum` is real observed output for piperacillin.
    """
    v = Validator.from_dictionaries(
        _dictionaries_with_local(tmp_path, "Google\n"), "clinical_strict")
    for mangled in ("pipercellum", "ceftazidimabibactam", "pharsinica"):
        assert v._unrecognized(mangled), f"{mangled} must still be flagged"


def test_validator_works_without_the_local_file(tmp_path):
    """A fresh checkout has no local list; loading must not break."""
    d = tmp_path / "dictionaries"
    d.mkdir()
    (d / "medications.txt").write_text("cefazolin\n", encoding="utf-8")
    v = Validator.from_dictionaries(d, "clinical_strict")
    assert not (d / "institutions_and_people.local.txt").exists()
    assert v._unrecognized("pipercellum")
