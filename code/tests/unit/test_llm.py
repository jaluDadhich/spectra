"""GPT integration tests — all mocked; no real API key required."""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.config import Settings
from app.llm import CostTracker, MockGateway, SemanticReviewer
from app.llm.gateway import LLMError, get_gateway
from app.llm.tracking import BudgetExceeded
from app.schemas.findings import IssueNature
from tests.conftest import envelope, make_case


def _settings(tmp_path, **kw) -> Settings:
    defaults = dict(spectra_llm_enabled=False)
    defaults.update(kw)
    return Settings(_env_file=None, **defaults)


def _case_with_narrative():
    return make_case([envelope("broker_email", {
        "subject": "s", "body": "The insured performs warehousing and local delivery."})])


def test_gateway_none_when_disabled(tmp_path):
    assert get_gateway(_settings(tmp_path)) is None
    assert get_gateway(_settings(tmp_path, spectra_llm_enabled=True)) is None  # no key


def test_candidate_with_nonexistent_evidence_rejected(tmp_path):
    settings = _settings(tmp_path, spectra_llm_enabled=True)
    tracker = CostTracker(10, 1.0, None, None)
    gateway = MockGateway(scripted=[{
        "task_id": "t1",
        "candidate_findings": [{
            "candidate_type": "OPS", "title": "Hallucinated",
            "issue_nature": IssueNature.EXPOSURE_OMISSION.value,
            "observed_facts": ["x"], "comparison_summary": "y",
            "evidence_ids": ["EV-FAKE-1"], "possible_consequence": "z",
            "recommended_action": "a", "uncertainty_state": "POSSIBLE_MISMATCH",
            "limitations": []}],
        "no_issue_reason": None}])
    reviewer = SemanticReviewer(gateway, settings, tracker)
    case = _case_with_narrative()
    eids = [eid for n in case.narratives for eid in n.field_evidence.values()]
    accepted, notes = reviewer.review(case, "t1", "q", ["P7"], eids)
    assert accepted == []
    assert any("nonexistent evidence" in n for n in notes)


def test_valid_candidate_accepted_as_model_suggested(tmp_path):
    settings = _settings(tmp_path, spectra_llm_enabled=True)
    tracker = CostTracker(10, 1.0, None, None)
    case = _case_with_narrative()
    eids = [eid for n in case.narratives for eid in n.field_evidence.values()]
    gateway = MockGateway(scripted=[{
        "task_id": "t1",
        "candidate_findings": [{
            "candidate_type": "OPS", "title": "Undeclared operation hinted",
            "issue_nature": IssueNature.EXPOSURE_OMISSION.value,
            "observed_facts": ["text mentions delivery"], "comparison_summary": "not in quote",
            "evidence_ids": eids[:1], "possible_consequence": "may be unpriced",
            "recommended_action": "ask broker", "uncertainty_state": "NEEDS_HUMAN_REVIEW",
            "limitations": ["semantic only"]}],
        "no_issue_reason": None}])
    reviewer = SemanticReviewer(gateway, settings, tracker)
    accepted, notes = reviewer.review(case, "t1", "q", ["P7"], eids)
    assert len(accepted) == 1
    f = accepted[0]
    assert f.confidence.basis.value == "MODEL_SUGGESTED"
    assert f.conclusion_state.value != "CONFIRMED_DATA_CONFLICT"  # never auto-confirmed
    assert f.evidence[0].evidence_id == eids[0]


def test_invalid_enum_rejected_by_schema(tmp_path):
    settings = _settings(tmp_path, spectra_llm_enabled=True)
    tracker = CostTracker(10, 1.0, None, None)
    gateway = MockGateway(scripted=[{
        "task_id": "t1",
        "candidate_findings": [{
            "candidate_type": "X", "title": "t", "observed_facts": [],
            "comparison_summary": "", "evidence_ids": ["EV-1"],
            "possible_consequence": "", "recommended_action": "",
            "uncertainty_state": "DEFINITELY_FRAUD", "limitations": []}],
        "no_issue_reason": None}])
    reviewer = SemanticReviewer(gateway, settings, tracker)
    case = _case_with_narrative()
    accepted, notes = reviewer.review(case, "t1", "q", ["P7"], [])
    assert accepted == []
    assert any("schema validation" in n for n in notes)


def test_budget_limit_blocks_calls(tmp_path):
    settings = _settings(tmp_path, spectra_llm_enabled=True)
    tracker = CostTracker(0, 1.0, None, None)
    reviewer = SemanticReviewer(MockGateway(), settings, tracker)
    case = _case_with_narrative()
    accepted, notes = reviewer.review(case, "t1", "q", ["P7"], [])
    assert accepted == [] and any("unavailable" in n for n in notes)


def test_cost_estimation_with_pricing():
    tracker = CostTracker(10, 1.0, 0.001, 0.002)
    tracker.record(1000, 500)
    assert tracker.usage.estimated_cost_usd == pytest.approx(0.002)


def test_openai_gateway_requires_key(tmp_path):
    settings = _settings(tmp_path, spectra_llm_enabled=True)
    from app.llm.gateway import OpenAIGateway
    with pytest.raises(LLMError) as exc:
        OpenAIGateway(settings)
    assert exc.value.category == "auth"


def test_openai_gateway_expands_completion_budget_once_on_truncation(tmp_path):
    settings = _settings(tmp_path, spectra_llm_enabled=True,
                         openai_api_key="test-key", openai_max_request_tokens=25_000,
                         openai_max_output_tokens=2_000)
    from app.llm.gateway import OpenAIGateway
    gateway = OpenAIGateway(settings)

    class FakeCompletions:
        def __init__(self):
            self.calls = []
            self.responses = [
                SimpleNamespace(
                    choices=[SimpleNamespace(finish_reason="length", message=SimpleNamespace(
                        content=None, refusal=None))],
                    usage=SimpleNamespace(prompt_tokens=100, completion_tokens=4_000)),
                SimpleNamespace(
                    id="retry-ok", model="gpt-6-luna",
                    choices=[SimpleNamespace(finish_reason="stop", message=SimpleNamespace(
                        content='{"task_id":"t","candidate_findings":[],"no_issue_reason":"clear"}',
                        refusal=None))],
                    usage=SimpleNamespace(prompt_tokens=100, completion_tokens=80)),
            ]

        def create(self, **kwargs):
            self.calls.append(kwargs)
            return self.responses.pop(0)

    class FakeClient:
        def __init__(self):
            self.chat = SimpleNamespace(completions=FakeCompletions())

        def with_options(self, **kwargs):
            return self

    client = FakeClient()
    gateway._client = client
    response = gateway.complete_json(
        "system", "user", {"type": "object", "properties": {}, "required": []},
        "document_triage")

    assert len(client.chat.completions.calls) == 2
    first_limit = client.chat.completions.calls[0]["max_completion_tokens"]
    second_limit = client.chat.completions.calls[1]["max_completion_tokens"]
    assert second_limit > first_limit
    assert response.content["no_issue_reason"] == "clear"
    assert response.input_tokens == 200
    assert response.output_tokens == 4_080
    assert response.retries == 1
