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
