"""Deterministic evidence assembly: scope, isolation, memory provenance, failure handling."""

from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session

from app.ai.evidence import assemble_briefing_evidence
from app.config import get_settings
from app.memory.hindsight import HindsightMemoryService
from app.memory.service import BankRouter
from app.memory.types import EvidenceHit, MemoryRef
from tests.conftest import Api
from tests.fakes import FakeServer, unavailable

AS_OF = datetime.now(UTC).replace(microsecond=0)


def ts(days_ago: float) -> str:
    return (AS_OF - timedelta(days=days_ago)).isoformat()


class Env:
    def __init__(self, client: TestClient, server: FakeServer | None, service):
        self.c, self.api, self.server, self.service = client, Api(client), server, service

    def assemble(self, cid: str, did: str, as_of: datetime = AS_OF):
        state = self.c.app.state

        async def run():
            with Session(state.engine) as session:
                return await assemble_briefing_evidence(session, cid, did, as_of, memory=state.memory,
                                                        intelligence=state.intelligence)

        return self.c.portal.call(run)

    def without_fast_path(self):
        worker = self.c.app.state.memory_worker

        class _Ctx:
            def __enter__(ctx):
                self.c.app.state.memory_worker = None

            def __exit__(ctx, *a):
                self.c.app.state.memory_worker = worker

        return _Ctx()


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
def scenario(env):
    api = env.api
    a, b = api.customer("Aurora Logistics"), api.customer("Borealis Foods")
    deal = api.deal(a["id"], "Pune pilot", value_minor=4_200_000, currency="USD")
    other_deal = api.deal(a["id"], "Mumbai rollout")
    priya = api.stakeholder(a["id"], "Priya Raman", priorities=["security"])
    note = api.interaction(a["id"], deal_id=deal["id"], occurred_at=ts(3), title="Budget call",
                           notes="CFO said the budget is capped at USD 42,000.", participant_ids=[priya["id"]])
    api.interaction(a["id"], deal_id=other_deal["id"], occurred_at=ts(2), notes="OTHER-DEAL note about Mumbai.")
    commitment = api.commitment(a["id"], deal["id"], description="Send SOC 2 report",
                                due_date=(AS_OF - timedelta(days=2)).date().isoformat(), source_interaction_id=note["id"])
    api.commitment(a["id"], other_deal["id"], description="OTHER-DEAL commitment")
    b_deal = api.deal(b["id"], "Borealis deal")
    api.interaction(b["id"], deal_id=b_deal["id"], occurred_at=ts(1), notes="BOREALIS secret on-prem requirement.")
    api.commitment(b["id"], b_deal["id"], description="BOREALIS commitment")
    return {"a": a, "b": b, "deal": deal, "other_deal": other_deal, "note": note, "commitment": commitment,
            "priya": priya}


def texts(pack):
    return " ".join(item.text for item in pack.request.evidence)


def test_pack_contains_scoped_records_signals_notes_and_linked_memories(env, scenario):
    pack = env.assemble(scenario["a"]["id"], scenario["deal"]["id"])
    kinds = {(s.ref[0], s.kind, s.source_type) for s in pack.sources}
    assert ("R", "recorded", "deal") in kinds and ("R", "recorded", "commitment") in kinds
    assert ("R", "recorded", "stakeholder") in kinds and ("D", "signal", "signal") in kinds
    assert ("N", "rep_note", "interaction") in kinds and ("M", "memory", "memory") in kinds
    assert all(s.kind != "statement" for s in pack.sources)  # rep notes are never customer statements
    note = next(s for s in pack.sources if s.ref.startswith("N"))
    assert note.source_id == scenario["note"]["id"] and note.occurred_at is not None and "(rep note)" in note.excerpt
    memory = next(s for s in pack.sources if s.ref.startswith("M"))
    assert memory.source_id == scenario["note"]["id"] and memory.memory_ref.bank_id.endswith(scenario["a"]["id"])
    overdue = next(s for s in pack.sources if s.source_id == f"commitment_overdue:{scenario['commitment']['id']}")
    assert {(r.type, r.id) for r in overdue.related_records} >= {("commitment", scenario["commitment"]["id"])}
    assert pack.memory.status == "used" and pack.memory.included == 1
    assert pack.sufficient is True and "USD 42,000" in pack.sources[0].excerpt
    assert [i.ref for i in pack.request.evidence] == [s.ref for s in pack.sources]


def test_pack_never_contains_other_customers_or_other_deals(env, scenario):
    pack = env.assemble(scenario["a"]["id"], scenario["deal"]["id"])
    body = texts(pack)
    assert "BOREALIS" not in body and "Borealis" not in body
    assert "OTHER-DEAL" not in body and "Mumbai" not in body
    ids = {s.source_id for s in pack.sources}
    assert scenario["other_deal"]["id"] not in body and scenario["b"]["id"] not in str(ids)


def test_deal_of_another_customer_is_refused(env, scenario):
    with pytest.raises(ValueError):
        env.assemble(scenario["b"]["id"], scenario["deal"]["id"])


def test_stale_memory_is_excluded(env, scenario):
    cid, iid = scenario["a"]["id"], scenario["note"]["id"]
    with env.without_fast_path():  # edited, not yet re-synced
        env.c.patch(f"/api/customers/{cid}/interactions/{iid}", json={"notes": "Budget now USD 35,000."})
    pack = env.assemble(cid, scenario["deal"]["id"])
    assert pack.memory.excluded_stale == 1 and pack.memory.included == 0
    assert "42,000" not in " ".join(s.excerpt for s in pack.sources if s.kind == "memory")
    assert "USD 35,000" in texts(pack)  # the current note is still evidence (as a rep note)


def test_memory_of_deleted_source_is_excluded(env, scenario):
    cid = scenario["a"]["id"]
    # the commitment is sourced from the interaction, so it must go first (otherwise 409)
    env.c.delete(f"/api/customers/{cid}/commitments/{scenario['commitment']['id']}")
    with env.without_fast_path():  # interaction deleted, memory not yet removed from Hindsight
        assert env.c.delete(f"/api/customers/{cid}/interactions/{scenario['note']['id']}").status_code == 204
    pack = env.assemble(cid, scenario["deal"]["id"])
    assert pack.memory.included == 0 and pack.memory.excluded_deleted_sources == 1
    assert all(s.source_id != scenario["note"]["id"] for s in pack.sources)


def test_unlinked_and_other_deal_memories_are_excluded(env, scenario, monkeypatch):
    cid = scenario["a"]["id"]
    original = env.service.recall_customer_evidence

    async def with_extras(request):
        hits = await original(request)
        bank = env.service.router.customer_bank(cid)
        observation = EvidenceHit(ref=MemoryRef(bank_id=bank, memory_id="obs-1"), text="Cloud summary: budget 42k",
                                  memory_type="observation", document_id=None, source_type=None, source_id=None)
        return hits + [observation]

    monkeypatch.setattr(env.service, "recall_customer_evidence", with_extras)
    pack = env.assemble(cid, scenario["deal"]["id"])
    assert pack.memory.excluded_unlinked == 1
    assert all("Cloud summary" not in s.excerpt for s in pack.sources)


def test_memory_failure_is_explicit_and_sqlite_evidence_remains(env, scenario):
    env.server.fail_next = [unavailable()]
    pack = env.assemble(scenario["a"]["id"], scenario["deal"]["id"])
    assert pack.memory.status == "unavailable" and "cannot reach" in pack.memory.reason
    assert pack.memory.included == 0 and pack.sufficient is True
    assert any(s.kind == "rep_note" for s in pack.sources)


def test_insufficient_evidence_is_reported(env, scenario):
    empty = env.api.deal(scenario["a"]["id"], "Empty deal")
    pack = env.assemble(scenario["a"]["id"], empty["id"])
    assert pack.sufficient is False and "No interaction notes" in pack.insufficient_reason
    assert pack.memory.status in ("no_relevant_memories", "used") and pack.memory.included == 0


def test_notes_after_as_of_are_excluded(env, scenario):
    pack = env.assemble(scenario["a"]["id"], scenario["deal"]["id"], as_of=AS_OF - timedelta(days=5))
    assert not any(s.kind == "rep_note" for s in pack.sources)


def test_memory_disabled_backend_is_reported(client, api):
    c = api.customer()
    d = api.deal(c["id"])
    api.interaction(c["id"], deal_id=d["id"], occurred_at=ts(1))
    env = Env(client, None, client.app.state.memory)
    pack = env.assemble(c["id"], d["id"])
    assert pack.memory.status == "disabled" and pack.sufficient is True
