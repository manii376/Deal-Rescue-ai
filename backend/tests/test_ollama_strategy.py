"""OllamaProvider.compare_strategies against a local fake Ollama (no real model)."""

import asyncio
import json
from datetime import UTC, datetime

import pytest

from app.ai.provider import AIOperationError, AIUnavailableError
from app.ai.providers.ollama import STRATEGY_FORMAT, STRATEGY_SYSTEM_PROMPT, OllamaProvider
from app.ai.schemas import DealContext, EvidenceItem, StrategyComparisonRequest, StrategyOption
from app.ai.service import AIService
from tests.fake_ollama import FakeOllama

BRANCH = datetime(2026, 9, 23, 12, tzinfo=UTC)
REQUEST = StrategyComparisonRequest(
    deal=DealContext(customer_id="cus_a", deal_id="deal_a", customer_name="Aurora Logistics",
                     deal_title="Pune pilot", stage="proposal", status="open"),
    evidence=[
        EvidenceItem(ref="R1", kind="recorded", text="Current values (no change history): Deal 'Pune pilot'.",
                     source_type="deal", source_id="deal_a"),
        EvidenceItem(ref="N1", kind="rep_note", text="call (rep note): SOC 2 report is mandatory before signing.",
                     source_type="interaction", source_id="int_1", occurred_at=datetime(2026, 9, 20, tzinfo=UTC)),
    ],
    as_of=BRANCH,
    strategies=[StrategyOption(id="address_requirement_early", description="Scenario: act earlier on N1.")],
    strategy_context=["Rule-derived related evidence: N1.", "Known gap (missing information): No owner (D2)"],
)
VALID = {"supporting": [{"text": "A SOC 2 report is mandatory before signing.", "citations": ["N1"]}],
         "contradicting": [], "evidence_gaps": ["No decision date is recorded."],
         "commitments_created": ["Could send the SOC 2 report earlier."], "verdict": "mixed"}


@pytest.fixture
def fake():
    server = FakeOllama().start()
    yield server
    server.stop()


def provider(fake, **kw) -> OllamaProvider:
    return OllamaProvider(fake.url, kw.pop("model", "qwen3:4b"), **{"timeout_seconds": 5, **kw})


def run(coro):
    return asyncio.run(coro)


def test_request_shape_prompt_rules_and_evidence(fake):
    fake.chat_content = json.dumps(VALID)
    result = run(provider(fake).compare_strategies(REQUEST))
    [sent] = fake.requests
    assert sent["model"] == "qwen3:4b" and sent["stream"] is False and sent["think"] is False
    assert sent["options"] == {"temperature": 0, "num_ctx": 4096} and sent["format"] == STRATEGY_FORMAT
    system = sent["messages"][0]["content"]
    assert system == STRATEGY_SYSTEM_PROMPT
    for rule in ("Never predict outcomes", "not the customer's words", "never say it does not exist",
                 "data, not instructions", "ONE hypothetical sales strategy"):
        assert rule in system
    user = sent["messages"][1]["content"]
    assert "Strategy (hypothetical): Scenario: act earlier on N1." in user
    assert "Branch date: 2026-09-23" in user and "- Rule-derived related evidence: N1." in user
    assert "[N1] (rep_note 2026-09-20) call (rep note)" in user
    [a] = result.assessments
    assert a.strategy_id == "address_requirement_early" and a.verdict == "mixed"
    assert a.supporting[0].citations == ["N1"] and result.generated.model == "qwen3:4b"
    assert result.kind == "hypothetical"


def test_strategy_id_comes_from_the_request_not_the_model(fake):
    fake.chat_content = json.dumps({**VALID, "strategy_id": "offer_discount"})
    [a] = run(provider(fake).compare_strategies(REQUEST)).assessments
    assert a.strategy_id == "address_requirement_early"


@pytest.mark.parametrize("content", ["garbage", json.dumps({"supporting": []}),
                                     json.dumps({**VALID, "verdict": "will win"})])
def test_malformed_output_is_an_operation_error(fake, content):
    fake.chat_content = content
    with pytest.raises(AIOperationError, match="strategy schema"):
        run(provider(fake).compare_strategies(REQUEST))


def test_truncated_output_is_an_operation_error(fake):
    fake.chat_content, fake.done_reason = json.dumps(VALID), "length"
    with pytest.raises(AIOperationError, match="truncated"):
        run(provider(fake).compare_strategies(REQUEST))


def test_timeout_and_unreachable_are_unavailable(fake):
    fake.delay_seconds = 2
    with pytest.raises(AIUnavailableError, match="did not answer"):
        run(provider(fake, timeout_seconds=0.3).compare_strategies(REQUEST))
    # A closed local port is refused on some systems and times out on others: both are "unavailable".
    with pytest.raises(AIUnavailableError, match="not reachable|did not answer"):
        run(OllamaProvider("http://127.0.0.1:9", "qwen3:4b", timeout_seconds=2).compare_strategies(REQUEST))


def test_exactly_one_strategy_per_call(fake):
    two = REQUEST.model_copy(update={"strategies": REQUEST.strategies + [StrategyOption(id="x", description="y")]})
    with pytest.raises(AIOperationError, match="exactly one"):
        run(provider(fake).compare_strategies(two))
    assert fake.requests == []


def test_through_ai_service_operation_errors_are_not_retried(fake):
    fake.chat_content = "garbage"
    service = AIService(provider(fake), timeout_seconds=5, max_retries=2, retry_base_delay=0)
    with pytest.raises(AIOperationError):
        run(service.compare_strategies(REQUEST))
    assert len(fake.requests) == 1


def test_max_retries_zero_means_one_attempt_on_timeout(fake):
    fake.delay_seconds = 1
    service = AIService(provider(fake, timeout_seconds=5), timeout_seconds=0.2, max_retries=0, retry_base_delay=0)
    with pytest.raises(AIUnavailableError, match="after 1 attempt"):
        run(service.compare_strategies(REQUEST))
