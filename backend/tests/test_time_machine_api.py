"""TM4 assess route: order of operations, isolation, point-in-time evidence, memory, checks, errors (fakes only)."""

from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from app.ai.provider import AIOperationError, AIUnavailableError
from app.ai.schemas import AIStatus, CitedText, GenerationInfo, StrategyAssessment, StrategyComparison
from app.config import get_settings
from app.memory.hindsight import HindsightMemoryService
from app.memory.service import BankRouter
from app.memory.types import EvidenceHit, MemoryRef
from tests import tm_scenario as tm
from tests.conftest import Api
from tests.fakes import FakeServer, unavailable

GEN = GenerationInfo(provider="fake", model="fake-strategy", generated_at=tm.AS_OF)


class FakeAI:
    """Records every call. Default answer cites the first rep note (and a recorded item when there is one)."""

    name, model = "fake", "fake-strategy"

    def __init__(self):
        self.available = True
        self.error: Exception | None = None
        self.answer = None  # callable(request) -> StrategyComparison
        self.requests = []
        self.status_calls = 0

    async def status(self):
        self.status_calls += 1
        return AIStatus(provider="fake", implemented=True, available=self.available, model=self.model,
                        reason=None if self.available else "fake provider is down")

    async def compare_strategies(self, request):
        self.requests.append(request)
        if self.error:
            raise self.error
        if self.answer:
            return self.answer(request)
        note = next(e for e in request.evidence if e.kind in ("rep_note", "memory"))
        recorded = next((e for e in request.evidence if e.source_type == "commitment"), None)
        supporting = [CitedText(text="The CFO said the budget is capped at USD 42,000.", citations=[note.ref])]
        if recorded:
            supporting.append(CitedText(text="Sending the SOC 2 report is recorded as a commitment.",
                                        citations=[recorded.ref]))
        return StrategyComparison(assessments=[StrategyAssessment(
            strategy_id=request.strategies[0].id, supporting=supporting,
            evidence_gaps=["No decision-maker is recorded."], commitments_created=["Could send the SOC 2 report."],
            verdict="supported")], generated=GEN)

    async def generate_briefing(self, request):
        raise AIOperationError("fake provider has no briefing")

    analyze_objections = draft_follow_up = generate_briefing


@pytest.fixture
def ai():
    return FakeAI()


@pytest.fixture
def client(ai):
    from app.main import create_app

    with TestClient(create_app(ai_provider=ai)) as c:
        yield c


@pytest.fixture
def sc(client):
    return tm.build(client.app.state.engine)


def assess(client, sc, *, cid=None, did=None, **body):
    payload = {"strategy_id": "earlier_follow_up", "branch_at": tm.BRANCH_AT.isoformat(),
               "as_of": tm.AS_OF.isoformat(), **body}
    return client.post(f"/api/customers/{cid or sc.customer}/deals/{did or sc.deal}/ai/time-machine/assess",
                       json=payload)


def code(r):
    return r.json()["error"]["code"]


# -- request validation and isolation --------------------------------------------------------------------


@pytest.mark.parametrize("target", ["customer", "deal", "cross"])
def test_ownership_is_checked_first_and_nothing_else_runs(client, sc, ai, target):
    kw = {"customer": {"cid": "cus_missing"}, "deal": {"did": "deal_missing"}, "cross": {"did": sc.other_deal}}[target]
    r = assess(client, sc, **kw)
    assert r.status_code == 404 and code(r) == "not_found"
    assert ai.status_calls == 0 and ai.requests == []
    assert "BOREALIS" not in r.text


@pytest.mark.parametrize("body", [
    {"branch_at": "2026-09-23T12:00:00"}, {"as_of": "2026-09-29T12:00:00"},       # naive timestamps
    {"strategy_id": "offer_discount"}, {"strategy_id": "follow_up_sooner"},       # not in the catalogue
    {"description": "Offer a 50% discount"}, {"question": "Would we have won?"},  # no free text
    {"anchor_ref": "not a ref!"},
])
def test_invalid_requests_are_rejected_before_any_work(client, sc, ai, body):
    r = assess(client, sc, **body)
    assert r.status_code == 422 and code(r) == "validation_error"
    assert ai.status_calls == 0 and ai.requests == []


def test_branch_after_as_of_is_rejected(client, sc, ai):
    r = assess(client, sc, branch_at=(tm.AS_OF + timedelta(seconds=1)).isoformat())
    assert r.status_code == 422 and code(r) == "validation_error" and ai.status_calls == 0


def test_a_strategy_that_is_not_offered_cannot_be_assessed(client, sc, ai):
    r = assess(client, sc, branch_at=(tm.CALL_AT - timedelta(days=1)).isoformat())  # nothing to follow up on yet
    assert r.status_code == 422 and code(r) == "strategy_not_applicable"
    assert ai.status_calls == 0 and ai.requests == []


@pytest.mark.parametrize("anchor", [None, "D1", "R1", "Z9", "M1"])
def test_missing_or_invalid_anchor_is_rejected(client, sc, ai, anchor):
    body = {"strategy_id": "address_requirement_early"} | ({"anchor_ref": anchor} if anchor else {})
    r = assess(client, sc, **body)
    assert r.status_code == 422 and code(r) == "invalid_anchor"
    assert ai.status_calls == 0 and ai.requests == []


# -- evidence given to the model ------------------------------------------------------------------------


def test_only_point_in_time_evidence_of_this_deal_reaches_the_model(client, sc, ai):
    r = assess(client, sc, strategy_id="address_requirement_early", anchor_ref="N1")
    assert r.status_code == 200, r.text
    [request] = ai.requests
    assert request.as_of == tm.BRANCH_AT and [s.id for s in request.strategies] == ["address_requirement_early"]
    for item in request.evidence:
        assert item.occurred_at is None or item.occurred_at <= tm.BRANCH_AT
    text = " ".join(e.text for e in request.evidence) + " ".join(request.strategy_context)
    for later in ("LATER-NOTE", "FUTURE-CALL", "LATE-COMMITMENT", "Priya", "BOREALIS", "2026-09-29"):
        assert later not in text
    assert "status done" not in text  # C1 was completed after the branch: shown as open then
    # the catalogue's "as of now" reasons are never given to the model
    assert not any("stall rule" in c or "applies" in c for c in request.strategy_context)
    assert "Requirement anchor chosen by the user: N1." in request.strategies[0].description


def test_sources_are_the_same_pack_as_the_strategy_catalogue(client, sc, ai):
    body = assess(client, sc).json()
    catalogue = client.get(f"/api/customers/{sc.customer}/deals/{sc.deal}/time-machine/strategies",
                           params={"branch_at": tm.BRANCH_AT.isoformat(), "as_of": tm.AS_OF.isoformat()}).json()
    assert body["sources"] == catalogue["sources"]
    assert {s["ref"]: s["kind"] for s in body["sources"]}["N1"] == "rep_note"
    [request] = ai.requests
    assert [(e.ref, e.kind, e.text) for e in request.evidence] == [(s["ref"], s["kind"], s["excerpt"])
                                                                  for s in body["sources"]]
    assert body["evidence_map"] == next(c for c in catalogue["strategies"]
                                        if c["template"]["id"] == "earlier_follow_up")["evidence_map"]


def test_generated_assessment_is_hypothetical_checked_and_not_saved(client, sc, ai):
    body = assess(client, sc, strategy_id="address_requirement_early", anchor_ref="N1").json()
    assert body["status"] == "generated" and body["insufficient_reason"] is None
    comparison = body["comparison"]
    assert comparison["kind"] == "hypothetical" and "not predictions" in comparison["disclaimer"]
    [a] = comparison["assessments"]
    assert a["strategy_id"] == "address_requirement_early" and a["verdict"] == "supported"
    assert all(c in {s["ref"] for s in body["sources"]} for item in a["supporting"] for c in item["citations"])
    assert "Nothing is saved" in body["note"]
    assert ai.status_calls == 1 and len(ai.requests) == 1


def test_insufficient_evidence_skips_the_model(client, sc, ai):
    r = assess(client, sc, did=sc.empty_deal, strategy_id="identify_decision_maker")
    body = r.json()
    assert r.status_code == 200 and body["status"] == "insufficient_evidence" and body["comparison"] is None
    assert "No rep note, linked memory or commitment" in body["insufficient_reason"]
    assert ai.requests == []


def test_memory_not_requested_by_default_and_disabled_backend_reported(client, sc, ai):
    assert assess(client, sc).json()["memory"]["status"] == "not_requested"
    assert assess(client, sc, include_memory=True).json()["memory"]["status"] == "disabled"


# -- output checks through the route --------------------------------------------------------------------


def one(request, **fields):
    return StrategyComparison(assessments=[StrategyAssessment(strategy_id=request.strategies[0].id, **fields)],
                              generated=GEN)


def test_unknown_and_post_branch_citations_are_rejected(client, sc, ai):
    ai.answer = lambda req: one(req, verdict="supported", supporting=[
        CitedText(text="A later note mentions procurement.", citations=["N2"]),
        CitedText(text="Something else.", citations=["X9"])])
    body = assess(client, sc).json()
    comparison = body["comparison"]
    assert set(comparison["rejected_citations"]) == {"N2", "X9"}
    a = comparison["assessments"][0]
    assert all(i["citations"] == [] and i["issues"] for i in a["supporting"])
    assert a["verdict"] == "unsupported"


def test_outcome_language_and_invented_facts_are_removed_or_marked(client, sc, ai):
    ai.answer = lambda req: one(req, verdict="supported", supporting=[
        CitedText(text="Following up sooner would have closed the deal.", citations=["N1"]),
        CitedText(text="The customer said \"the budget is capped at USD 42,000\".", citations=["N1"]),
        CitedText(text="Northwind was also evaluating the pilot.", citations=["N1"])],
        commitments_created=["Could call by 2026-10-05 with a 20% discount."])
    a = assess(client, sc).json()["comparison"]["assessments"][0]
    text = str(a)
    assert "would have closed" not in text and "20%" not in text
    assert [i["citations"] for i in a["supporting"]] == [[], []]  # both marked, not accepted as grounded
    assert a["verdict"] == "unsupported" and a["rejected_items"] == 4 and a["commitments_created"] == []


@pytest.mark.parametrize("error, status, error_code", [
    (AIOperationError("Ollama returned output that does not match the strategy schema"), 502, "ai_provider_error"),
    (AIUnavailableError("Ollama did not answer within 60s"), 503, "ai_unavailable"),
])
def test_provider_errors_use_the_established_responses(client, sc, ai, error, status, error_code):
    ai.error = error
    r = assess(client, sc)
    assert r.status_code == status and code(r) == error_code and "comparison" not in r.text


def test_missing_assessment_for_the_strategy_is_a_provider_error(client, sc, ai):
    ai.answer = lambda req: StrategyComparison(assessments=[], generated=GEN)
    r = assess(client, sc)
    assert r.status_code == 502 and code(r) == "ai_provider_error"


def test_unavailable_provider_is_checked_after_eligibility_and_before_evidence(client, sc, ai):
    ai.available = False
    r = assess(client, sc)
    assert r.status_code == 503 and code(r) == "ai_unavailable" and "fake provider is down" in r.json()["error"]["message"]
    assert ai.status_calls == 1 and ai.requests == []


def test_max_retries_zero_is_respected(monkeypatch):
    from app.ai.provider import AITransientError
    from app.main import create_app

    monkeypatch.setenv("AI_MAX_RETRIES", "0")
    get_settings.cache_clear()
    fake = FakeAI()
    fake.error = AITransientError("timeout")
    with TestClient(create_app(ai_provider=fake)) as c:
        s = tm.build(c.app.state.engine)
        r = assess(c, s)
    assert r.status_code == 503 and len(fake.requests) == 1


def test_briefing_route_is_unchanged(client, sc, ai):
    r = client.post(f"/api/customers/{sc.customer}/deals/{sc.deal}/ai/briefing")
    assert r.status_code == 502  # FakeAI has no briefing: the existing error path, untouched
    assert ai.requests == []


# -- memory (fake Hindsight server) ------------------------------------------------------------------------

EARLY, LATE = datetime(2025, 1, 10, 10, tzinfo=UTC), datetime(2025, 1, 20, 10, tzinfo=UTC)
M_BRANCH, M_AS_OF = datetime(2025, 1, 15, 12, tzinfo=UTC), datetime(2025, 2, 1, 12, tzinfo=UTC)


class MemEnv:
    def __init__(self, client, server, service, ai):
        self.c, self.api, self.server, self.service, self.ai = client, Api(client), server, service, ai

    def recalls(self):
        return [bank for op, bank in self.server.calls if op == "recall"]

    def assess(self, ids, **body):
        payload = {"strategy_id": "address_requirement_early", "branch_at": M_BRANCH.isoformat(),
                   "as_of": M_AS_OF.isoformat(), "include_memory": True, **body}
        return self.c.post(f"/api/customers/{ids['a']}/deals/{ids['deal']}/ai/time-machine/assess", json=payload)

    def without_fast_path(self):
        worker, env = self.c.app.state.memory_worker, self

        class _Ctx:
            def __enter__(self):
                env.c.app.state.memory_worker = None

            def __exit__(self, *exc):
                env.c.app.state.memory_worker = worker

        return _Ctx()


@pytest.fixture
def menv(monkeypatch):
    from app.main import create_app

    monkeypatch.setenv("MEMORY_BACKEND", "hindsight")
    monkeypatch.setenv("MEMORY_MAX_ATTEMPTS", "1")
    get_settings.cache_clear()
    settings = get_settings()
    server = FakeServer()
    service = HindsightMemoryService(settings, BankRouter(settings.hindsight_customer_bank_prefix,
                                                          settings.hindsight_outcomes_bank_id),
                                     client_factory=server.factory, retry_base_delay=0)
    fake = FakeAI()
    with TestClient(create_app(memory_service=service, ai_provider=fake)) as client:
        yield MemEnv(client, server, service, fake)


@pytest.fixture
def ids(menv):
    api = menv.api
    a, b = api.customer("Aurora Logistics"), api.customer("Borealis Foods")
    deal, other = api.deal(a["id"], "Pune pilot"), api.deal(a["id"], "Mumbai rollout")
    early = api.interaction(a["id"], deal_id=deal["id"], occurred_at=EARLY.isoformat(), title="Budget call",
                            notes="CFO said the budget is capped at USD 42,000.")
    late = api.interaction(a["id"], deal_id=deal["id"], occurred_at=LATE.isoformat(),
                           notes="LATE-NOTE procurement asked for the SOC 2 report.")
    api.interaction(a["id"], deal_id=other["id"], occurred_at=EARLY.isoformat(), notes="OTHER-DEAL Mumbai note.")
    b_deal = api.deal(b["id"], "Borealis deal")
    api.interaction(b["id"], deal_id=b_deal["id"], occurred_at=EARLY.isoformat(), notes="BOREALIS secret.")
    menv.server.calls.clear()
    return {"a": a["id"], "deal": deal["id"], "early": early["id"], "late": late["id"]}


def memory_texts(request):
    return [e.text for e in request.evidence if e.kind == "memory"]


def test_one_recall_and_only_eligible_memories_reach_the_model(menv, ids):
    r = menv.assess(ids, anchor_ref="N1")
    assert r.status_code == 200, r.text
    assert menv.recalls() == [menv.service.router.customer_bank(ids["a"])]  # exactly one recall, own bank
    [request] = menv.ai.requests
    mems = [e for e in request.evidence if e.kind == "memory"]
    assert [m.source_id for m in mems] == [ids["early"]]
    joined = " ".join(e.text for e in request.evidence)
    assert "LATE-NOTE" not in joined and "OTHER-DEAL" not in joined and "BOREALIS" not in joined
    memory = r.json()["memory"]
    assert memory["status"] == "used" and memory["excluded_after_cutoff"] == 1


def test_memory_anchor_is_validated_after_the_single_recall(menv, ids):
    catalogue = menv.c.get(f"/api/customers/{ids['a']}/deals/{ids['deal']}/time-machine/strategies",
                           params={"branch_at": M_BRANCH.isoformat(), "as_of": M_AS_OF.isoformat(),
                                   "include_memory": "true"}).json()
    m_ref = next(s["ref"] for s in catalogue["sources"] if s["kind"] == "memory")
    menv.server.calls.clear()
    r = menv.assess(ids, anchor_ref=m_ref)
    assert r.status_code == 200, r.text
    assert len(menv.recalls()) == 1 and r.json()["evidence_map"]["anchor_ref"] == m_ref
    without = menv.assess(ids, anchor_ref=m_ref, include_memory=False)
    assert without.status_code == 422 and code(without) == "invalid_anchor"


def test_no_recall_when_memory_is_not_requested_or_the_provider_is_down(menv, ids):
    assert menv.assess(ids, anchor_ref="N1", include_memory=False).json()["memory"]["status"] == "not_requested"
    menv.ai.available = False
    assert menv.assess(ids, anchor_ref="N1").status_code == 503
    assert menv.recalls() == []


def test_stale_deleted_and_unlinked_memories_are_excluded(menv, ids, monkeypatch):
    original = menv.service.recall_customer_evidence
    bank = menv.service.router.customer_bank(ids["a"])

    async def with_observation(request):
        return await original(request) + [EvidenceHit(
            ref=MemoryRef(bank_id=bank, memory_id="obs-1"), text="Cloud summary: budget 42k",
            memory_type="observation", document_id=None, source_type=None, source_id=None)]

    monkeypatch.setattr(menv.service, "recall_customer_evidence", with_observation)
    with menv.without_fast_path():
        menv.c.patch(f"/api/customers/{ids['a']}/interactions/{ids['early']}", json={"notes": "Budget now USD 35,000."})
    r = menv.assess(ids, anchor_ref="N1")
    memory = r.json()["memory"]
    assert memory["excluded_stale"] == 1 and memory["excluded_unlinked"] == 1 and memory["included"] == 0
    assert memory_texts(menv.ai.requests[0]) == []
    with menv.without_fast_path():
        assert menv.c.delete(f"/api/customers/{ids['a']}/interactions/{ids['early']}").status_code == 204
    # the deleted interaction's note is gone, so nothing is known at the branch: no model call
    after = menv.assess(ids, strategy_id="identify_decision_maker")
    assert after.json()["memory"]["excluded_deleted_sources"] == 1
    assert after.json()["status"] == "insufficient_evidence" and len(menv.ai.requests) == 1


def test_memory_unavailable_and_no_relevant_memories_are_distinct(menv, ids):
    menv.server.fail_next = [unavailable()]
    failed = menv.assess(ids, anchor_ref="N1").json()
    assert failed["memory"]["status"] == "unavailable" and failed["memory"]["reason"]
    assert failed["status"] == "generated" and memory_texts(menv.ai.requests[-1]) == []
    none = menv.assess(ids, strategy_id="identify_decision_maker",
                       branch_at=datetime(2025, 1, 1, tzinfo=UTC).isoformat()).json()
    assert none["memory"]["status"] == "no_relevant_memories" and none["memory"]["reason"] is None
    assert none["status"] == "insufficient_evidence"


def test_rule_derived_next_steps_reach_the_service_and_ground_next_steps_only(client, sc, ai):
    ai.answer = lambda req: one(req, verdict="mixed", supporting=[
        CitedText(text="The CFO said the budget is capped at USD 42,000.", citations=["N1"])],
        commitments_created=["Could schedule the next customer contact within 5 day(s) of 2026-09-20.",
                             "Could schedule the next customer contact within 4 day(s) of 2026-09-20."])
    body = assess(client, sc).json()
    [request] = ai.requests
    assert request.rule_next_steps == [c["description"] for c in body["evidence_map"]["implied_commitments"]]
    a = body["comparison"]["assessments"][0]
    assert a["commitments_created"] == ["Could schedule the next customer contact within 5 day(s) of 2026-09-20."]
    assert any("number '4'" in n for n in a["check_notes"])
    assert body["comparison"]["kind"] == "hypothetical"



def test_owner_gap_contact_conclusion_is_split_in_the_api_response(client, sc, ai):
    def answer(req):
        owner = next(e.ref for e in req.evidence if (e.source_id or "").startswith("missing_deal_owner"))
        return one(req, verdict="mixed", supporting=[
            CitedText(text="The CFO said the budget is capped at USD 42,000.", citations=["N1"])],
            contradicting=[CitedText(text="No deal owner is recorded, so the sales rep cannot determine who to "
                                          "contact next.", citations=[owner])])
    ai.answer = answer
    a = assess(client, sc).json()["comparison"]["assessments"][0]
    fact, conclusion = a["contradicting"]
    assert fact["text"] == "No deal owner is recorded." and fact["citations"] and fact["issues"] == []
    assert conclusion["citations"] == [] and "deal owner is seller-side" in conclusion["issues"][0]
    assert a["rejected_items"] == 1
