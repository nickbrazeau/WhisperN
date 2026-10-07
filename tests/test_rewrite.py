"""Tests for email/research rewrite mode (ADR 0002).

No model is needed: the rewriter is replaced by a stub returning fixed text,
so these test the checks around the model, not the model. Model behaviour is
measured by scripts/eval_polisher.py against rewrite_cases.yaml.

All fixtures are synthetic and describe no real person.

Run: .venv/bin/python -m pytest tests/ -q
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "src"))

from pipeline.dictation import DictationPipeline, Outcome  # noqa: E402
from pipeline.polisher import (  # noqa: E402
    LocalOnlyError, LocalPolisher, PolishResult, _assert_local_model,
)
from validation.validator import Status, Validator  # noqa: E402

RAMBLE = ("um so I think we should probably meet on Wednesday at 3 to go over "
          "the draft and uh Sarah said the reviewers did not like figure two")
GOOD = ("I think we should probably meet on Wednesday at 3 to go over the "
        "draft. Sarah said the reviewers did not like Figure 2.")


@pytest.fixture(scope="module")
def validator() -> Validator:
    return Validator.from_dictionaries(REPO_ROOT / "dictionaries", "clinical_strict")


def details(result) -> str:
    return " | ".join(c.detail for c in result.protected_changes)


# ---------------------------------------------------------------- validator

def test_faithful_rewrite_is_still_review_never_safe(validator):
    r = validator.validate_rewrite(RAMBLE, GOOD)
    assert r.status is Status.REVIEW
    assert r.protected_changes == []


def test_dropped_negation_is_reported(validator):
    r = validator.validate_rewrite(RAMBLE, GOOD.replace("did not like", "liked"))
    assert "negation 'not'" in details(r)


def test_changed_number_reported_both_ways(validator):
    r = validator.validate_rewrite(RAMBLE, GOOD.replace("at 3", "at 4"))
    d = details(r)
    assert "number '3' is in the transcript but not in the rewrite" in d
    assert "number '4' is in the rewrite but was not said" in d


def test_dropped_hedge_is_reported(validator):
    r = validator.validate_rewrite(RAMBLE, GOOD.replace("probably ", ""))
    assert "uncertainty 'probably'" in details(r)


def test_added_hedge_is_reported(validator):
    raw = "the effect was significant in the second cohort"
    r = validator.validate_rewrite(raw, "The effect may have been significant in the second cohort.")
    assert "uncertainty 'may' is in the rewrite but was not said" in details(r)


def test_merged_repeated_negation_is_not_a_change(validator):
    # Presence, not count: two "no"s becoming one loses a word, not a fact.
    raw = "there was no response from the editor and no response from the funder"
    r = validator.validate_rewrite(raw, "There was no response from the editor or the funder.")
    assert r.protected_changes == []


def test_invented_name_is_flagged(validator):
    r = validator.validate_rewrite(RAMBLE, GOOD + " Dr. Okonkwo agreed.")
    assert any("Okonkwo" in w for w in r.warnings)


def test_ordinary_sentence_start_is_not_flagged_as_a_name(validator):
    r = validator.validate_rewrite(RAMBLE, "However, " + GOOD[0].lower() + GOOD[1:])
    assert not any("invented names" in w for w in r.warnings)


def test_longer_rewrite_is_flagged(validator):
    raw = "send the slides to the group"
    r = validator.validate_rewrite(raw, "Please send the slides to the group, along with "
                                        "the updated budget and the timeline for next year.")
    assert any("longer than the transcript" in w for w in r.warnings)


def test_empty_rewrite_rejected(validator):
    assert validator.validate_rewrite(RAMBLE, "   ").status is Status.REJECTED


# ---------------------------------------------------------------- local-only

@pytest.mark.parametrize("name", [
    "gpt-oss:120b-cloud", "qwen3-coder:480b-cloud", "deepseek-v3.1:671b-cloud",
    "some-model:cloud", "kimi-k2-cloud",
])
def test_cloud_models_refused(name):
    with pytest.raises(LocalOnlyError):
        _assert_local_model(name)
    with pytest.raises(LocalOnlyError):
        LocalPolisher(model=name, prompt_path=str(REPO_ROOT / "prompts/eloquent_rewriter.md"))


@pytest.mark.parametrize("name", ["qwen2.5:14b", "llama3.1:8b", "qwen2.5:32b"])
def test_local_models_accepted(name):
    _assert_local_model(name)


def test_rewriter_prompt_excludes_implementer_notes():
    p = LocalPolisher(model="qwen2.5:14b",
                      prompt_path=str(REPO_ROOT / "prompts/eloquent_rewriter.md"))
    assert "NOTES FOR IMPLEMENTERS" not in p.system_prompt
    assert "polished_text" in p.system_prompt


# ---------------------------------------------------------------- pipeline

class StubRewriter:
    def __init__(self, text: str | None) -> None:
        self.text = text

    def polish(self, raw, glossary, mode):
        assert mode == "rewrite"
        if self.text is None:
            return PolishResult.failed(raw, "local model unreachable: stub")
        return PolishResult(ok=True, raw_text=raw, polished_text=self.text)


def pipeline_with(rewriter, validator) -> DictationPipeline:
    p = DictationPipeline(REPO_ROOT, validator=validator, rewriter=rewriter)
    p.transcribe = lambda audio: (RAMBLE, 5)  # no audio, no whisper
    return p


def test_pipeline_rewrite_is_review_and_pastes_the_rewrite(validator):
    res = pipeline_with(StubRewriter(GOOD), validator).run(Path("x.wav"), rewrite=True)
    assert res.outcome is Outcome.REVIEW
    assert res.rewritten
    assert res.text_to_use == GOOD
    assert res.raw_text == RAMBLE


def test_pipeline_rewrite_failure_falls_back_to_raw_for_review(validator):
    res = pipeline_with(StubRewriter(None), validator).run(Path("x.wav"), rewrite=True)
    assert res.outcome is Outcome.REVIEW
    assert not res.rewritten
    assert res.text_to_use == RAMBLE
    assert any("rewrite unavailable" in w for w in res.warnings)


def test_pipeline_without_rewrite_flag_never_calls_rewriter(validator):
    class Exploding:
        def polish(self, *a):
            raise AssertionError("rewriter called with rewrite off")
    res = pipeline_with(Exploding(), validator).run(Path("x.wav"))
    assert not res.rewritten


# ---------------------------------------------------------------- names and scope

def test_rewrite_that_guesses_a_drug_is_a_protected_change(validator):
    # The ASR mangles a drug name; the model "fixes" it to a real one.
    raw = "we should probably switch him to septicpene for the resistant isolates"
    rw = "We should probably switch him to cefepime for the resistant isolates."
    r = validator.validate_rewrite(raw, rw)
    assert "medication 'cefepime' is in the rewrite but was not said" in details(r)


def test_rewrite_that_supplies_an_organism_is_a_protected_change(validator):
    r = validator.validate_rewrite("the cultures grew staff aureus",
                                   "The cultures grew Staphylococcus aureus.")
    assert "organism 'staphylococcus aureus'" in details(r)


def test_drug_name_that_was_said_is_not_flagged(validator):
    raw = "um the isolates were all susceptible to meropenem"
    r = validator.validate_rewrite(raw, "All isolates were susceptible to meropenem.")
    assert "meropenem" not in details(r)


def test_ordinary_words_in_the_drug_list_are_not_names(validator):
    # "water" and "iron" are FDA list entries and ordinary English.
    raw = "remind me to pay the bill"
    r = validator.validate_rewrite(raw, "Remind me to pay the water and iron bill.")
    assert "medication" not in details(r)


@pytest.mark.parametrize("raw", [
    "continue cefazolin 2 grams IV every 8 hours",
    "the patient denies fever or chills",
    "start vancomycin 1 gram BID",
])
def test_clinical_dictation_in_rewrite_mode_is_warned(validator, raw):
    r = validator.validate_rewrite(raw, raw.capitalize() + ".")
    assert r.warnings and r.warnings[0].startswith("looks clinical")


@pytest.mark.parametrize("raw", [
    "we enrolled 412 patients across 6 sites",
    "the isolates were all susceptible to meropenem",
    "can you send me the slides from Tuesday",
    "the hazard ratio was 1.3 with a confidence interval that crossed one",
])
def test_email_and_research_dictation_is_not_warned(validator, raw):
    r = validator.validate_rewrite(raw, raw.capitalize() + ".")
    assert not any(w.startswith("looks clinical") for w in r.warnings)
