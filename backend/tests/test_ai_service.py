"""AI boundary: unavailable paths and evidence enforcement, with fake providers only."""

import asyncio
from datetime import UTC, datetime

import pytest

from app.ai.provider import AIProvider, AITransientError, AIUnavailableError
from app.ai.providers import UnavailableProvider, build_ai_provider
from app.ai.schemas import (
    AIStatus,
    BriefingRequest,
    CitedText,
    Claim,
    DealBriefing,
    DealContext,
    EvidenceItem,
    FollowUpDraft,
    FollowUpRequest,
    GenerationInfo,
    ObjectionAnalysis,
    ObjectionAnalysisRequest,
    ObjectionHypothesis,
    StrategyAssessment,
    StrategyComparison,
    StrategyComparisonRequest,
    StrategyOption,
)
from app.ai.service import AIService
from app.config import Settings

NOW = datetime(2026, 9, 28, tzinfo=UTC)
GEN = GenerationInfo(provider="fake", model="fake-1", generated_at=NOW)
DEAL = DealContext(customer_id="cus_a", deal_id="deal_a", customer_name="Aurora", deal_title="Pilot",
                   stage="proposal", status="open")
EVIDENCE = [
    EvidenceItem(ref="R1", kind="recorded", text="Deal value USD 42,000", source_type="deal", source_id="deal_a"),
    EvidenceItem(ref="S1", kind="statement", text='"Budget is capped"', source_type="interaction", source_id="int_1"),
    EvidenceItem(ref="O1", kind="outcome", text="Borealis lost after late discount", source_type="outcome"),
]


class FakeProvider:
    name = "fake"
    model = "fake-1"

    def __init__(self, fail_times: int = 0, delay: float = 0):
        self.fail_times = fail_times
        self.delay = delay
        self.calls = 0

    async def status(self) -> AIStatus:
        return AIStatus(provider="fake", implemented=True, available=True, model="fake-1")

    async def _tick(self):
        self.calls += 1
        if self.delay:
            await asyncio.sleep(self.delay)
        if self.calls <= self.fail_times:
            raise AITransientError("rate limited")

    async def generate_briefing(self, request):
        await self._tick()
        return DealBriefing(claims=[
            Claim(text="Deal is worth 42k", kind="recorded", citations=["R1"]),
            Claim(text="Budget is capped", kind="statement", citations=["S1", "X9"]),
            Claim(text="Customer said security blocks it", kind="statement", citations=["R1"]),
            Claim(text="Procurement is the real blocker", kind="recorded", citations=[]),
        ], generated=GEN)

    async def analyze_objections(self, request):
        await self._tick()
        return ObjectionAnalysis(hypotheses=[ObjectionHypothesis(
            statement="Security review may be blocking", supporting=[CitedText(text="cap", citations=["S1", "Z1"])],
            what_would_confirm="Ask procurement")], generated=GEN)

    async def draft_follow_up(self, request):
        await self._tick()
        return FollowUpDraft(subject="Next steps", body="...", citations=["S1", "NOPE"], generated=GEN)

    async def compare_strategies(self, request):
        await self._tick()
        return StrategyComparison(assessments=[
            StrategyAssessment(strategy_id="A", supporting=[CitedText(text="x", citations=["O1"])], verdict="supported"),
            StrategyAssessment(strategy_id="B", supporting=[CitedText(text="y", citations=["FAKE"])], verdict="supported"),
            StrategyAssessment(strategy_id="C", verdict="mixed"),
            StrategyAssessment(strategy_id="ZZZ", verdict="supported"),
        ], generated=GEN)


def service(provider: AIProvider, **kw) -> AIService:
    return AIService(provider, timeout_seconds=kw.get("timeout", 5), max_retries=kw.get("retries", 2),
                     retry_base_delay=0)


def test_default_provider_is_explicitly_unavailable():
    provider = build_ai_provider(Settings(_env_file=None))
    status = asyncio.run(provider.status())
    assert status.provider == "none" and status.available is False and status.implemented is True
    assert "AI_PROVIDER=none" in status.reason
    with pytest.raises(AIUnavailableError):
        asyncio.run(service(provider).generate_briefing(BriefingRequest(deal=DEAL, evidence=EVIDENCE, as_of=NOW)))


@pytest.mark.parametrize("name", ["anthropic"])  # ollama is implemented since M5 (test_ollama_provider.py)
def test_planned_providers_report_not_implemented(name):
    provider = build_ai_provider(Settings(_env_file=None, ai_provider=name))
    status = asyncio.run(provider.status())
    assert status.implemented is False and status.available is False
    assert "not implemented" in status.reason
    assert isinstance(provider, UnavailableProvider)


def test_anthropic_status_mentions_missing_key_without_leaking_values():
    s = Settings(_env_file=None, ai_provider="anthropic", anthropic_api_key="sk-ant-secret-value-123")
    status = asyncio.run(build_ai_provider(s).status())
    assert "sk-ant-secret-value-123" not in status.model_dump_json()


def test_briefing_citations_and_kinds_are_enforced():
    result = asyncio.run(service(FakeProvider()).generate_briefing(
        BriefingRequest(deal=DEAL, evidence=EVIDENCE, as_of=NOW)))
    kinds = [(c.kind, c.citations) for c in result.claims]
    assert kinds == [
        ("recorded", ["R1"]),
        ("statement", ["S1"]),        # unknown X9 removed
        ("inference", ["R1"]),        # "statement" citing a recorded fact is not a customer statement
        ("inference", []),            # "recorded" with no evidence is relabelled
    ]
    assert result.rejected_citations == ["X9"] and result.relabelled_claims == 2


def test_objection_and_follow_up_outputs_are_inference_and_hypothetical():
    svc = service(FakeProvider())
    obj = asyncio.run(svc.analyze_objections(ObjectionAnalysisRequest(deal=DEAL, evidence=EVIDENCE, as_of=NOW)))
    assert obj.hypotheses[0].kind == "inference"
    assert obj.hypotheses[0].supporting[0].citations == ["S1"] and obj.rejected_citations == ["Z1"]
    draft = asyncio.run(svc.draft_follow_up(FollowUpRequest(deal=DEAL, evidence=EVIDENCE, as_of=NOW,
                                                            purpose="recap")))
    assert draft.kind == "hypothetical" and draft.citations == ["S1"] and draft.rejected_citations == ["NOPE"]


def test_strategy_verdicts_are_only_weakened_and_unknown_strategies_dropped():
    req = StrategyComparisonRequest(deal=DEAL, evidence=EVIDENCE, as_of=NOW, strategies=[
        StrategyOption(id="A", description="Offer pilot"), StrategyOption(id="B", description="Discount"),
        StrategyOption(id="C", description="Escalate")])
    result = asyncio.run(service(FakeProvider()).compare_strategies(req))
    assert [(a.strategy_id, a.verdict) for a in result.assessments] == [
        ("A", "supported"), ("B", "unsupported"), ("C", "unsupported")]
    assert result.adjusted_verdicts == 2 and result.rejected_citations == ["FAKE"]
    assert result.kind == "hypothetical" and "not predictions" in result.disclaimer


def test_transient_errors_are_retried_then_reported_unavailable():
    ok = FakeProvider(fail_times=2)
    asyncio.run(service(ok, retries=2).generate_briefing(BriefingRequest(deal=DEAL, evidence=EVIDENCE, as_of=NOW)))
    assert ok.calls == 3
    failing = FakeProvider(fail_times=10)
    with pytest.raises(AIUnavailableError, match="after 3 attempt"):
        asyncio.run(service(failing, retries=2).generate_briefing(
            BriefingRequest(deal=DEAL, evidence=EVIDENCE, as_of=NOW)))
    assert failing.calls == 3


def test_timeout_is_enforced():
    slow = FakeProvider(delay=1.0)
    with pytest.raises(AIUnavailableError, match="TimeoutError"):
        asyncio.run(service(slow, timeout=0.05, retries=0).generate_briefing(
            BriefingRequest(deal=DEAL, evidence=EVIDENCE, as_of=NOW)))


def test_capabilities_endpoint_reports_ai_unavailable(client):
    ai = client.get("/api/system/capabilities").json()["ai"]
    assert ai == {"provider": "none", "implemented": True, "available": False, "model": None,
                  "reason": "No AI provider configured (AI_PROVIDER=none). AI features are unavailable; nothing is generated."}
