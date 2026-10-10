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

    def revise(self, raw, draft, missing):
        return PolishResult(ok=True, raw_text=raw, polished_text=draft)


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


# --- context cap (num_ctx) ------------------------------------------------

class _FakeResponse:
    def __init__(self, data: dict) -> None:
        import json
        self._body = json.dumps(data).encode()

    def read(self) -> bytes:
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *exc) -> None:
        return None


class _FakeOpener:
    def __init__(self, data: dict) -> None:
        self.data = data
        self.sent: list[dict] = []

    def open(self, req, timeout=None):
        import json
        self.sent.append(json.loads(req.data))
        return _FakeResponse(self.data)


def _capped(num_ctx: int, reply: dict) -> tuple[LocalPolisher, _FakeOpener]:
    p = LocalPolisher(model="qwen2.5:14b",
                      prompt_path=str(REPO_ROOT / "prompts/eloquent_rewriter.md"),
                      num_ctx=num_ctx)
    opener = _FakeOpener(reply)
    p._opener = lambda: opener
    return p, opener


_OK_REPLY = {"message": {"content": '{"polished_text": "Synthetic.", "flags": []}'},
             "done_reason": "stop", "prompt_eval_count": 700, "eval_count": 20}


def test_num_ctx_is_sent_with_every_request_and_warmup():
    p, opener = _capped(4096, _OK_REPLY)
    assert p.polish("synthetic short dictation", [], "rewrite").ok
    p.warm()
    assert [s["options"]["num_ctx"] for s in opener.sent] == [4096, 4096]


def test_overlong_dictation_is_refused_before_sending():
    # Ollama would truncate from the start, dropping the system prompt.
    p, opener = _capped(4096, _OK_REPLY)
    res = p.polish("synthetic words " * 2000, [], "rewrite")
    assert not res.ok and "too long" in res.error
    assert opener.sent == []


def test_output_that_hits_the_context_limit_is_rejected():
    p, _ = _capped(4096, dict(_OK_REPLY, done_reason="length"))
    res = p.polish("synthetic short dictation", [], "rewrite")
    assert not res.ok and "context limit" in res.error


def test_overlong_dictation_falls_back_to_raw_for_review(validator):
    p, _ = _capped(4096, _OK_REPLY)
    pipe = DictationPipeline(repo_root=REPO_ROOT, validator=validator, rewriter=p)
    raw = "synthetic words " * 2000
    res = pipe.rewrite_text(raw)
    assert res.outcome is Outcome.REVIEW and not res.rewritten
    assert res.text_to_use.strip() == raw.strip()


def test_tiny_num_ctx_refused():
    with pytest.raises(ValueError):
        LocalPolisher(model="qwen2.5:14b", num_ctx=512,
                      prompt_path=str(REPO_ROOT / "prompts/eloquent_rewriter.md"))


# --- one repair for dropped hedges and negations --------------------------

HEDGED = "I think maybe we could move the meeting to Wednesday."


class RepairingStub:
    """First rewrite drops hedges; the revision returns `revised`."""

    def __init__(self, first: str, revised: str | None) -> None:
        self.first, self.revised = first, revised
        self.revise_calls: list[list[str]] = []

    def polish(self, raw, glossary, mode):
        return PolishResult(ok=True, raw_text=raw, polished_text=self.first)

    def revise(self, raw, draft, missing):
        self.revise_calls.append(missing)
        if self.revised is None:
            return PolishResult.failed(raw, "stub failure")
        return PolishResult(ok=True, raw_text=raw, polished_text=self.revised)


def _rewrite(stub, validator):
    return DictationPipeline(REPO_ROOT, validator=validator, rewriter=stub).rewrite_text(HEDGED)


@pytest.mark.parametrize("hedge", ["maybe", "perhaps", "I think", "I guess", "not sure", "likely"])
def test_conversational_hedges_are_protected(validator, hedge):
    raw = f"So {hedge} the draft is ready."
    v = validator.validate_rewrite(raw, "The draft is ready.")
    assert any(c.category == "uncertainty" for c in v.protected_changes)


def test_dropped_hedges_trigger_one_repair_that_is_used(validator):
    stub = RepairingStub("We could move the meeting to Wednesday.",
                         "I think maybe we could move the meeting to Wednesday.")
    res = _rewrite(stub, validator)
    assert stub.revise_calls == [["i think", "maybe"]]
    assert res.text_to_use == "I think maybe we could move the meeting to Wednesday."
    assert not res.protected_changes
    assert any("restored" in w for w in res.warnings)
    assert res.outcome is Outcome.REVIEW   # still never SAFE


def test_repair_that_is_not_better_is_discarded(validator):
    first = "Maybe we could move the meeting to Wednesday."
    stub = RepairingStub(first, "We should definitely move the meeting.")
    res = _rewrite(stub, validator)
    assert len(stub.revise_calls) == 1
    assert res.text_to_use == first
    assert any(c.raw_value == "i think" for c in res.protected_changes)


def test_failed_repair_keeps_the_first_rewrite(validator):
    first = "We could move the meeting to Wednesday."
    res = _rewrite(RepairingStub(first, None), validator)
    assert res.text_to_use == first and res.rewritten


def test_faithful_rewrite_is_not_repaired(validator):
    stub = RepairingStub(HEDGED, "unused")
    _rewrite(stub, validator)
    assert stub.revise_calls == []
