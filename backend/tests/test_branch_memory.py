"""TM1 follow-up: opt-in historical memory recall in the branch view (fake Hindsight server; no live calls)."""

from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session

from app.ai.evidence import MEMORY_NOT_REQUESTED, MemoryEvidenceStatus, assemble_briefing_evidence
from app.config import get_settings
from app.memory.hindsight import HindsightMemoryService
from app.memory.service import BankRouter
from app.memory.types import EvidenceHit, MemoryRef
from tests.conftest import Api
from tests.fakes import FakeServer, unavailable

EARLY = datetime(2025, 1, 10, 10, 0, tzinfo=UTC)
LATE = datetime(2025, 1, 20, 10, 0, tzinfo=UTC)
BRANCH = datetime(2025, 1, 15, 12, 0, tzinfo=UTC)
AS_OF = datetime(2025, 2, 1, 12, 0, tzinfo=UTC)
URL = "/api/customers/{cid}/deals/{did}/timeline/branch"


class Env:
    def __init__(self, client: TestClient, server: FakeServer, service: HindsightMemoryService):
        self.c, self.api, self.server, self.service = client, Api(client), server, service

    def without_fast_path(self):
        worker = self.c.app.state.memory_worker
        env = self

        class _Ctx:
            def __enter__(self):
                env.c.app.state.memory_worker = None

            def __exit__(self, *exc):
                env.c.app.state.memory_worker = worker

        return _Ctx()

    def recalled_banks(self) -> list[str]:
        return [bank for op, bank in self.server.calls if op == "recall"]


@pytest.fixture
def env(monkeypatch):
    from app.main import create_app

    monkeypatch.setenv("MEMORY_BACKEND", "hindsight")
    monkeypatch.setenv("MEMORY_MAX_ATTEMPTS", "1")
    get_settings.cache_clear()
    settings = get_settings()
    server = FakeServer()
    service = HindsightMemoryService(settings, BankRouter(settings.hindsight_customer_bank_prefix,
                                                          settings.hindsight_outcomes_bank_id),
                                     client_factory=server.factory, retry_base_delay=0)
    with TestClient(create_app(memory_service=service)) as client:
        yield Env(client, server, service)


@pytest.fixture
def sc(env):
    api = env.api
    a, b = api.customer("Aurora Logistics"), api.customer("Borealis Foods")
    deal, other_deal = api.deal(a["id"], "Pune pilot"), api.deal(a["id"], "Mumbai rollout")
    early = api.interaction(a["id"], deal_id=deal["id"], occurred_at=EARLY.isoformat(), title="Budget call",
                            notes="CFO said the budget is capped at USD 42,000.")
    late = api.interaction(a["id"], deal_id=deal["id"], occurred_at=LATE.isoformat(),
                           notes="LATE-NOTE procurement asked for the SOC 2 report.")
    api.interaction(a["id"], deal_id=other_deal["id"], occurred_at=EARLY.isoformat(), notes="OTHER-DEAL Mumbai note.")
    b_deal = api.deal(b["id"], "Borealis deal")
    api.interaction(b["id"], deal_id=b_deal["id"], occurred_at=EARLY.isoformat(), notes="BOREALIS secret.")
    env.server.calls.clear()  # only count calls made by the branch requests below
    return {"a": a["id"], "b": b["id"], "deal": deal["id"], "other_deal": other_deal["id"], "b_deal": b_deal["id"],
            "early": early["id"], "late": late["id"]}


def branch(env, sc, *, include_memory=None, branch_at=BRANCH, did=None, cid=None):
    params = {"branch_at": branch_at.isoformat(), "as_of": AS_OF.isoformat()}
    if include_memory is not None:
        params["include_memory"] = str(include_memory).lower()
    return env.c.get(URL.format(cid=cid or sc["a"], did=did or sc["deal"]), params=params)


def memories(body):
    return [s for s in body["known_then"] if s["kind"] == "memory"]


def test_memory_is_not_requested_by_default_and_nothing_is_contacted(env, sc):
    body = branch(env, sc).json()
    assert body["memory"] == {**MemoryEvidenceStatus(status="not_requested", reason=MEMORY_NOT_REQUESTED).model_dump()}
    assert env.recalled_banks() == [] and memories(body) == []
    assert branch(env, sc, include_memory=False).json()["memory"]["status"] == "not_requested"


def test_opt_in_recall_uses_the_customers_bank_and_keeps_only_eligible_memories(env, sc):
    r = branch(env, sc, include_memory=True)
    assert r.status_code == 200, r.text
    body = r.json()
    assert env.recalled_banks() == [env.service.router.customer_bank(sc["a"])]  # one recall, own bank only
    mem = body["memory"]
    assert (mem["status"], mem["included"]) == ("used", 1)
    assert mem["excluded_after_cutoff"] == 1   # the LATE interaction occurred after branch_at
    assert mem["excluded_other_deal"] == 0     # recall is already filtered to this deal's tag upstream
    [m] = memories(body)
    assert m["source_id"] == sc["early"] and m["memory_ref"]["bank_id"].endswith(sc["a"])
    assert m["occurred_at"] == "2025-01-10T10:00:00Z"  # the source interaction's date, not a retrieval time
    text = " ".join(s["excerpt"] for s in body["known_then"])
    assert "LATE-NOTE" not in text and "OTHER-DEAL" not in text and "BOREALIS" not in text


def test_memories_are_eligible_once_their_source_interaction_has_occurred(env, sc):
    body = branch(env, sc, include_memory=True, branch_at=LATE).json()  # exactly at the late interaction
    assert {m["source_id"] for m in memories(body)} == {sc["early"], sc["late"]}
    assert body["memory"]["excluded_after_cutoff"] == 0


def test_success_with_nothing_eligible_differs_from_failure(env, sc):
    before = branch(env, sc, include_memory=True, branch_at=datetime(2025, 1, 1, tzinfo=UTC)).json()["memory"]
    assert (before["status"], before["included"], before["excluded_after_cutoff"]) == ("no_relevant_memories", 0, 2)
    assert before["reason"] is None

    env.server.fail_next = [unavailable()]
    failed = branch(env, sc, include_memory=True)
    assert failed.status_code == 200  # the SQLite view is still returned, as for briefings
    body = failed.json()
    assert body["memory"]["status"] == "unavailable" and "cannot reach" in body["memory"]["reason"]
    assert body["memory"]["included"] == 0 and memories(body) == []
    assert any(s["kind"] == "rep_note" and s["source_id"] == sc["early"] for s in body["known_then"])


def test_memory_of_a_deleted_interaction_is_excluded(env, sc):
    with env.without_fast_path():  # interaction deleted; its memory is not yet removed from Hindsight
        assert env.c.delete(f"/api/customers/{sc['a']}/interactions/{sc['early']}").status_code == 204
    mem = branch(env, sc, include_memory=True).json()["memory"]
    assert (mem["status"], mem["included"], mem["excluded_deleted_sources"]) == ("no_relevant_memories", 0, 1)


def test_stale_memory_is_excluded(env, sc):
    with env.without_fast_path():  # edited after the memory was written, not yet re-synced
        env.c.patch(f"/api/customers/{sc['a']}/interactions/{sc['early']}", json={"notes": "Budget now USD 35,000."})
    body = branch(env, sc, include_memory=True).json()
    assert body["memory"]["excluded_stale"] == 1 and memories(body) == []


def test_unlinked_observations_other_deal_and_foreign_bank_hits_are_excluded(env, sc, monkeypatch):
    original = env.service.recall_customer_evidence
    own_bank = env.service.router.customer_bank(sc["a"])
    foreign_bank = env.service.router.customer_bank(sc["b"])

    async def with_extras(request):
        # Ignore the deal tag so the other deal's memory reaches the provenance check (defence in depth).
        hits = await original(request.model_copy(update={"deal_id": None}))
        return hits + [
            EvidenceHit(ref=MemoryRef(bank_id=own_bank, memory_id="obs-1"), text="Cloud summary: budget 42k",
                        memory_type="observation", document_id=None, source_type=None, source_id=None),
            EvidenceHit(ref=MemoryRef(bank_id=foreign_bank, memory_id="x-1"), text="BOREALIS foreign hit",
                        memory_type="world", document_id=None, source_type="interaction", source_id=sc["early"]),
        ]

    monkeypatch.setattr(env.service, "recall_customer_evidence", with_extras)
    body = branch(env, sc, include_memory=True).json()
    assert body["memory"]["excluded_unlinked"] == 1 and body["memory"]["excluded_foreign_bank"] == 1
    assert body["memory"]["excluded_other_deal"] == 1
    assert [m["source_id"] for m in memories(body)] == [sc["early"]]
    assert "Cloud summary" not in str(body) and "BOREALIS" not in str(body) and "OTHER-DEAL" not in str(body)


def test_other_customers_deal_is_404_and_no_recall_happens(env, sc):
    r = branch(env, sc, include_memory=True, did=sc["b_deal"])
    assert r.status_code == 404 and env.recalled_banks() == []


def test_identical_inputs_give_identical_ordered_evidence(env, sc):
    first = branch(env, sc, include_memory=True).json()
    second = branch(env, sc, include_memory=True).json()
    assert first == second
    assert [s["ref"] for s in first["known_then"]] == [s["ref"] for s in second["known_then"]]


def test_disabled_backend_with_include_memory_reports_disabled(client, api):
    c = api.customer()
    d = api.deal(c["id"])
    api.interaction(c["id"], deal_id=d["id"], occurred_at=EARLY.isoformat())
    body = client.get(URL.format(cid=c["id"], did=d["id"]),
                      params={"branch_at": BRANCH.isoformat(), "as_of": AS_OF.isoformat(),
                              "include_memory": "true"}).json()
    assert body["memory"]["status"] == "disabled" and body["memory"]["reason"] != MEMORY_NOT_REQUESTED


def test_briefing_memory_behaviour_is_unchanged(env, sc):
    state = env.c.app.state

    async def run():
        with Session(state.engine) as s:
            return await assemble_briefing_evidence(s, sc["a"], sc["deal"], AS_OF, memory=state.memory,
                                                    intelligence=state.intelligence)

    pack = env.c.portal.call(run)
    assert pack.memory.status == "used" and pack.memory.included == 2    # both interactions, as before
    assert pack.memory.excluded_after_cutoff == 0
    assert not pack.sources[0].excerpt.startswith("Current values")


def test_existing_memory_status_payloads_still_parse():
    old = MemoryEvidenceStatus.model_validate({"status": "used", "included": 2})
    assert old.excluded_after_cutoff == 0
    for status in ("used", "no_relevant_memories", "unavailable", "disabled", "not_requested"):
        assert MemoryEvidenceStatus(status=status).status == status
    with pytest.raises(ValueError):
        MemoryEvidenceStatus(status="skipped")


def test_strategy_anchor_candidates_include_only_eligible_memories(env, sc):
    params = {"branch_at": BRANCH.isoformat(), "as_of": AS_OF.isoformat(), "include_memory": "true"}
    url = f"/api/customers/{sc['a']}/deals/{sc['deal']}/time-machine/strategies"
    body = env.c.get(url, params=params).json()
    refs = {(s["kind"], s["source_id"]): s["ref"] for s in body["sources"]}
    requirement = next(c for c in body["strategies"] if c["template"]["id"] == "address_requirement_early")
    memory_ref, note_ref = refs[("memory", sc["early"])], refs[("rep_note", sc["early"])]
    assert memory_ref in requirement["anchor_candidates"]
    assert ("memory", sc["late"]) not in refs  # its interaction occurred after branch_at
    mapped = env.c.get(url, params={**params, "anchor_ref": memory_ref}).json()
    m = next(c for c in mapped["strategies"] if c["template"]["id"] == "address_requirement_early")["evidence_map"]
    assert m["anchor_ref"] == memory_ref and set(m["related"]) == {memory_ref, note_ref}
