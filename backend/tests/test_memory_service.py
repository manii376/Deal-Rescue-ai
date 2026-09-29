"""HindsightMemoryService + sync ledger against an in-memory fake (no network, no LLM)."""

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, select

from app.config import get_settings
from app.domain.models import Interaction, MemorySourceRef, MemoryWrite
from app.memory.hindsight import HindsightMemoryService
from app.memory.service import BankRouter
from app.memory.sync import _claim
from tests.conftest import Api
from tests.fakes import FakeServer, llm_auth_failure, unavailable


@pytest.fixture
def server() -> FakeServer:
    return FakeServer()


@pytest.fixture
def mem_api(monkeypatch, server):
    from app.main import create_app

    monkeypatch.setenv("MEMORY_BACKEND", "hindsight")
    get_settings.cache_clear()
    settings = get_settings()
    router = BankRouter(settings.hindsight_customer_bank_prefix, settings.hindsight_outcomes_bank_id)
    service = HindsightMemoryService(settings, router, client_factory=server.factory, retry_base_delay=0)
    with TestClient(create_app(memory_service=service)) as client:
        yield Api(client)


def test_retain_routes_to_customer_bank_and_records_bank_and_memory_id(mem_api, server):
    a, b = mem_api.customer("Aurora"), mem_api.customer("Borealis")
    ia = mem_api.interaction(a["id"], notes="Aurora budget 42k")
    ib = mem_api.interaction(b["id"], notes="Borealis wants on-prem")

    bank_a, bank_b = f"deal-rescue-cust-{a['id']}", f"deal-rescue-cust-{b['id']}"
    mem = mem_api.c.get(f"/api/customers/{a['id']}/interactions/{ia['id']}").json()["memory"]
    assert mem["status"] == "stored" and mem["bank_id"] == bank_a
    assert mem["document_id"] == f"interaction:{ia['id']}"
    assert len(mem["memory_refs"]) == 1 and mem["memory_refs"][0]["bank_id"] == bank_a

    when = f"{ia['occurred_at'][:10]} {ia['occurred_at'][11:16]} UTC"
    assert [r.text for r in server.all_records(bank_a)] == [
        f"Customer: Aurora\nCall on {when}\nNotes:\nAurora budget 42k"]
    assert all("Borealis" not in r.text for r in server.all_records(bank_a))
    assert server.all_records(bank_b)[0].metadata["source_id"] == ib["id"]
    assert sorted(set(server.created_banks)) == sorted([bank_a, bank_b])


def test_no_duplicate_writes_on_retry_replay_or_unchanged_edit(mem_api, server):
    c = mem_api.customer()
    body = {"occurred_at": "2026-09-20T10:00:00Z", "channel": "call", "notes": "Budget 42k"}
    base = f"/api/customers/{c['id']}/interactions"
    i = mem_api.c.post(base, json=body, headers={"Idempotency-Key": "abc-12345"}).json()
    assert server.retain_count() == 1

    mem_api.c.post(base, json=body, headers={"Idempotency-Key": "abc-12345"})  # client retry
    r = mem_api.c.post(f"{base}/{i['id']}/memory/sync")  # explicit resync, nothing changed
    assert r.status_code == 200 and r.json()["status"] == "stored"
    mem_api.c.patch(f"{base}/{i['id']}", json={"notes": "Budget 42k"})  # same content
    assert server.retain_count() == 1

    mem_api.c.patch(f"{base}/{i['id']}", json={"notes": "Budget cut to 35k"})  # real change
    assert server.retain_count() == 2
    bank = f"deal-rescue-cust-{c['id']}"
    records = server.all_records(bank)
    assert len(records) == 1 and "35k" in records[0].text  # replaced, not duplicated
    refs = mem_api.c.get(f"{base}/{i['id']}/memory").json()["memory_refs"]
    assert refs == [{"bank_id": bank, "memory_id": records[0].id}]


def test_memory_failure_never_rolls_back_business_record(mem_api, server):
    c = mem_api.customer()
    server.fail_next = [llm_auth_failure()]
    i = mem_api.interaction(c["id"], notes="Recorded even if memory fails")
    # The response is built before the background write runs, so it honestly says "pending".
    assert i["memory"]["status"] == "pending"

    stored = mem_api.c.get(f"/api/customers/{c['id']}/interactions/{i['id']}").json()
    assert stored["notes"] == "Recorded even if memory fails"
    assert stored["memory"]["status"] == "failed"
    assert "HTTP 500" in stored["memory"]["last_error"]
    assert server.retain_count() == 1  # non-retryable error: no automatic retries

    r = mem_api.c.post(f"/api/customers/{c['id']}/interactions/{i['id']}/memory/sync")
    assert r.status_code == 202
    after = mem_api.c.get(f"/api/customers/{c['id']}/interactions/{i['id']}/memory").json()
    assert after["status"] == "stored" and after["attempts"] == 2 and after["last_error"] is None


def test_transient_failures_are_retried_with_a_bound(mem_api, server):
    c = mem_api.customer()
    server.fail_next = [unavailable(), unavailable()]
    i = mem_api.interaction(c["id"])
    assert mem_api.c.get(f"/api/customers/{c['id']}/interactions/{i['id']}/memory").json()["status"] == "stored"
    assert server.retain_count() == 3

    server.fail_next = [unavailable()] * 5
    j = mem_api.interaction(c["id"], notes="second")
    mem = mem_api.c.get(f"/api/customers/{c['id']}/interactions/{j['id']}/memory").json()
    assert mem["status"] == "failed" and "cannot reach" in mem["last_error"]
    assert server.retain_count() == 3 + 3  # MEMORY_MAX_ATTEMPTS=3


def test_recall_returns_bank_scoped_refs_linked_to_sources(mem_api, server):
    a, b = mem_api.customer("Aurora"), mem_api.customer("Borealis")
    d = mem_api.deal(a["id"])
    ia = mem_api.interaction(a["id"], notes="Aurora budget", deal_id=d["id"])
    mem_api.interaction(a["id"], notes="Aurora unrelated, no deal")

    r = mem_api.c.post(f"/api/customers/{a['id']}/memory/recall", json={"query": "budget", "deal_id": d["id"]})
    assert r.status_code == 200
    hits = r.json()["hits"]
    assert len(hits) == 1
    assert hits[0]["ref"]["bank_id"] == f"deal-rescue-cust-{a['id']}"
    assert {k: hits[0]["source"][k] for k in ("source_type", "source_id")} == {
        "source_type": "interaction", "source_id": ia["id"]}
    assert hits[0]["provenance"] == "linked" and hits[0]["source"]["occurred_at"] == ia["occurred_at"]

    # customer B has no bank yet: empty result, never A's memories
    r = mem_api.c.post(f"/api/customers/{b['id']}/memory/recall", json={"query": "Aurora budget"})
    assert r.status_code == 200 and r.json()["hits"] == []
    assert all(bank == f"deal-rescue-cust-{b['id']}" for op, bank in server.calls
               if op == "recall" and b["id"] in bank)


def test_reflect_resolves_evidence_ids_inside_the_customer_bank(mem_api, server):
    c = mem_api.customer()
    i = mem_api.interaction(c["id"], notes="SOC 2 required")
    r = mem_api.c.post(f"/api/customers/{c['id']}/memory/reflect", json={"question": "What is required?"})
    assert r.status_code == 200
    body = r.json()
    assert body["kind"] == "inference"
    assert [e["source"]["source_id"] for e in body["evidence"]] == [i["id"]]
    assert body["unresolved_memory_ids"] == ["mem-ghost"]


def test_deleting_interaction_removes_its_memory_document(mem_api, server):
    c = mem_api.customer()
    i = mem_api.interaction(c["id"])
    bank = f"deal-rescue-cust-{c['id']}"
    assert server.all_records(bank)
    assert mem_api.c.delete(f"/api/customers/{c['id']}/interactions/{i['id']}").status_code == 204
    assert server.all_records(bank) == []
    engine = mem_api.c.app.state.engine
    with Session(engine) as s:
        mw = s.exec(select(MemoryWrite).where(MemoryWrite.source_id == i["id"])).one()
        assert mw.status == "deleted"
        assert s.exec(select(MemorySourceRef).where(MemorySourceRef.source_id == i["id"])).all() == []


def test_concurrent_claims_allow_only_one_writer(mem_api):
    c = mem_api.customer()
    i = mem_api.interaction(c["id"])
    engine = mem_api.c.app.state.engine
    with Session(engine) as s:
        mw = s.exec(select(MemoryWrite).where(MemoryWrite.source_id == i["id"])).one()
        mw.status = "pending"
        s.add(mw)
        s.commit()
        mw_id = mw.id
    assert _claim(engine, mw_id) is True
    assert _claim(engine, mw_id) is False


def test_memory_status_endpoint_and_capabilities(mem_api):
    status = mem_api.c.get("/api/system/memory").json()
    assert status["backend"] == "hindsight" and status["available"] is True
    assert status["customer_bank_prefix"] == "deal-rescue-cust-"
    assert status["outcomes_bank_id"] == "deal-rescue-outcomes"


def test_bank_router_rejects_unsafe_or_too_long_ids():
    router = BankRouter("deal-rescue-cust-", "deal-rescue-outcomes")
    with pytest.raises(ValueError):
        router.customer_bank("cus/../other")
    with pytest.raises(ValueError):
        router.customer_bank("c" * 60)


def test_sync_endpoint_when_memory_disabled_returns_503(client, api):
    c = api.customer()
    i = api.interaction(c["id"])
    r = client.post(f"/api/customers/{c['id']}/interactions/{i['id']}/memory/sync")
    assert r.status_code == 503 and r.json()["error"]["code"] == "memory_unavailable"
    with Session(client.app.state.engine) as s:
        assert s.get(Interaction, i["id"]) is not None
