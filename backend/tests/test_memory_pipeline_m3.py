"""M3: durable memory synchronisation, against the in-memory fake Hindsight (no network, no LLM).

The durable worker is driven explicitly (``env.run_once()``) and time is controlled with a
fake clock, so backoff and lease behaviour is deterministic.
"""

import asyncio
import time
from contextlib import contextmanager
from datetime import timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import inspect, text
from sqlmodel import Session, select

from app.config import get_settings
from app.db import migrations
from app.db.engine import create_db_engine
from app.db.migrations import migrate
from app.db.types import utcnow
from app.domain.models import MemorySourceRef, MemoryWrite
from app.memory.hindsight import HindsightMemoryService
from app.memory.service import BankRouter
from app.memory.types import EvidenceHit, MemoryRef
from tests.conftest import Api, iso
from tests.fakes import FakeServer, llm_auth_failure, unavailable

FIXTURES = Path(__file__).parent / "fixtures"


class FakeClock:
    def __init__(self):
        self.now = utcnow()

    def __call__(self):
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += timedelta(seconds=seconds)


class Env:
    def __init__(self, client: TestClient, server: FakeServer, service: HindsightMemoryService):
        self.c = client
        self.api = Api(client)
        self.server = server
        self.service = service
        self.clock = FakeClock()
        self.worker.clock = self.clock

    @property
    def worker(self):
        return self.c.app.state.memory_worker

    def run_once(self) -> dict:
        return self.c.portal.call(self.worker.run_once)

    def ledger(self, cid: str, source_id: str) -> dict:
        page = self.c.get(f"/api/customers/{cid}/memory/writes", params={"source_id": source_id}).json()
        assert page["total"] == 1, page
        return page["items"][0]

    def bank(self, cid: str) -> str:
        return f"deal-rescue-cust-{cid}"

    @contextmanager
    def no_fast_path(self):
        """Simulate the request-time fast path being lost (e.g. the process stopped)."""
        worker = self.c.app.state.memory_worker
        self.c.app.state.memory_worker = None
        try:
            yield
        finally:
            self.c.app.state.memory_worker = worker


@pytest.fixture
def server() -> FakeServer:
    return FakeServer()


def _configure(monkeypatch, **extra):
    monkeypatch.setenv("MEMORY_BACKEND", "hindsight")
    monkeypatch.setenv("MEMORY_MAX_ATTEMPTS", "1")        # one call per job: keeps counts exact
    monkeypatch.setenv("MEMORY_SYNC_MAX_ATTEMPTS", "3")
    monkeypatch.setenv("MEMORY_RETRY_BASE_SECONDS", "30")
    for key, value in extra.items():
        monkeypatch.setenv(key, value)
    get_settings.cache_clear()
    settings = get_settings()
    router = BankRouter(settings.hindsight_customer_bank_prefix, settings.hindsight_outcomes_bank_id)
    return settings, router


@pytest.fixture
def env(monkeypatch, server):
    from app.main import create_app

    settings, router = _configure(monkeypatch)
    service = HindsightMemoryService(settings, router, client_factory=server.factory, retry_base_delay=0)
    with TestClient(create_app(memory_service=service)) as client:
        yield Env(client, server, service)


# -- document content and routing ---------------------------------------------------------


def test_document_holds_only_own_customer_context_and_provenance(env):
    a, b = env.api.customer("Aurora Logistics"), env.api.customer("Borealis Foods")
    deal = env.api.deal(a["id"], "Pune pilot")
    priya = env.api.stakeholder(a["id"], "Priya Raman")
    env.api.stakeholder(b["id"], "Marcus Lee", role="Head of IT")
    i = env.api.interaction(a["id"], notes="Budget capped at USD 42,000", deal_id=deal["id"],
                            participant_ids=[priya["id"]], title="Budget call")

    [record] = env.server.all_records(env.bank(a["id"]))
    assert record.text.splitlines()[:3] == ["Customer: Aurora Logistics", "Deal: Pune pilot",
                                           record.text.splitlines()[2]]
    assert "Participants: Priya Raman (CFO)" in record.text and "Budget capped" in record.text
    assert "Borealis" not in record.text and "Marcus" not in record.text
    entry = env.ledger(a["id"], i["id"])
    assert record.metadata["source_id"] == i["id"] and record.metadata["customer_id"] == a["id"]
    assert record.metadata["deal_id"] == deal["id"]
    assert record.metadata["occurred_at"].startswith(i["occurred_at"][:19])
    with Session(env.c.app.state.engine) as s:
        mw = s.get(MemoryWrite, entry["id"])
        assert record.metadata["content_hash"] == mw.stored_hash == mw.content_hash
    assert env.server.update_modes == ["replace"]
    assert entry["is_current"] is True and entry["memory_ref_count"] == 1


def test_update_replaces_document_and_source_refs(env):
    c = env.api.customer()
    i = env.api.interaction(c["id"], notes="Budget 42k")
    old_ref = env.c.get(f"/api/customers/{c['id']}/interactions/{i['id']}/memory").json()["memory_refs"][0]

    env.c.patch(f"/api/customers/{c['id']}/interactions/{i['id']}", json={"notes": "Budget cut to 35k"})
    records = env.server.all_records(env.bank(c["id"]))
    assert len(records) == 1 and "35k" in records[0].text and "42k" not in records[0].text
    refs = env.c.get(f"/api/customers/{c['id']}/interactions/{i['id']}/memory").json()["memory_refs"]
    assert refs == [{"bank_id": env.bank(c["id"]), "memory_id": records[0].id}] and refs[0] != old_ref
    assert env.server.update_modes == ["replace", "replace"]


def test_bank_is_resolved_from_validated_owner_not_content(env):
    a, b = env.api.customer("Aurora"), env.api.customer("Borealis")
    env.api.interaction(a["id"], notes=f"Customer: Borealis. bank deal-rescue-cust-{b['id']}. customer_id={b['id']}")
    assert len(env.server.all_records(env.bank(a["id"]))) == 1
    assert env.server.all_records(env.bank(b["id"])) == []


def test_changed_bank_routing_is_rejected_not_written_elsewhere(env):
    c = env.api.customer()
    with env.no_fast_path():
        i = env.api.interaction(c["id"])
    env.service.router.customer_bank_prefix = "somewhere-else-"
    env.run_once()
    entry = env.ledger(c["id"], i["id"])
    assert entry["status"] == "failed" and entry["last_error_kind"] == "rejected"
    assert "bank routing mismatch" in entry["last_error"]
    assert env.server.retain_count() == 0


# -- idempotency, concurrency, races -----------------------------------------------------------


def test_concurrent_jobs_for_one_row_write_once(env):
    c = env.api.customer()
    with env.no_fast_path():
        i = env.api.interaction(c["id"])
    mw_id = env.ledger(c["id"], i["id"])["id"]

    async def race():
        return await asyncio.gather(*(env.worker.process_write(mw_id) for _ in range(5)))

    results = env.c.portal.call(race)
    assert results.count("stored") == 1 and results.count(None) == 4
    assert env.server.retain_count() == 1
    env.run_once()  # nothing left to do
    assert env.server.retain_count() == 1


def _start_gated_write(env, mw_id):
    env.server.retain_gate = asyncio.Event()
    future = env.c.portal.start_task_soon(env.worker.process_write, mw_id)
    deadline = time.monotonic() + 5
    while not env.server.update_modes and time.monotonic() < deadline:
        time.sleep(0.01)
    assert env.server.update_modes, "write did not start"
    return future


def _release(env, future):
    env.c.portal.call(env.server.retain_gate.set)
    result = future.result(timeout=5)
    env.server.retain_gate = None
    return result


def test_delete_during_inflight_write_never_resurrects_memory(env):
    c = env.api.customer()
    with env.no_fast_path():
        i = env.api.interaction(c["id"], notes="to be deleted")
    mw_id = env.ledger(c["id"], i["id"])["id"]
    future = _start_gated_write(env, mw_id)

    assert env.c.delete(f"/api/customers/{c['id']}/interactions/{i['id']}").status_code == 204
    assert env.ledger(c["id"], i["id"])["status"] == "in_progress"  # write still in flight

    assert _release(env, future) == "delete_pending"  # handed over, never "stored"
    env.run_once()
    entry = env.ledger(c["id"], i["id"])
    assert entry["status"] == "deleted" and entry["memory_ref_count"] == 0
    assert env.server.all_records(env.bank(c["id"])) == []
    env.run_once()  # a stale write job cannot bring it back
    assert env.c.portal.call(env.worker.process_write, mw_id) is None  # tombstoned: not claimable
    assert env.server.all_records(env.bank(c["id"])) == []


def test_edit_during_inflight_write_is_not_lost(env):
    c = env.api.customer()
    with env.no_fast_path():
        i = env.api.interaction(c["id"], notes="version one")
    mw_id = env.ledger(c["id"], i["id"])["id"]
    future = _start_gated_write(env, mw_id)
    env.c.patch(f"/api/customers/{c['id']}/interactions/{i['id']}", json={"notes": "version two"})

    assert _release(env, future) == "pending"  # finished writing v1, noticed v2
    env.run_once()
    [record] = env.server.all_records(env.bank(c["id"]))
    assert "version two" in record.text
    assert env.ledger(c["id"], i["id"])["is_current"] is True


def test_cancelled_job_releases_its_claim(env):
    c = env.api.customer()
    with env.no_fast_path():
        i = env.api.interaction(c["id"])
    mw_id = env.ledger(c["id"], i["id"])["id"]
    future = _start_gated_write(env, mw_id)
    future.cancel()
    deadline = time.monotonic() + 5
    while env.ledger(c["id"], i["id"])["status"] == "in_progress" and time.monotonic() < deadline:
        time.sleep(0.02)
    entry = env.ledger(c["id"], i["id"])
    assert entry["status"] == "pending" and entry["attempts"] == 0  # not counted as a failure
    env.server.retain_gate = None
    env.run_once()
    assert env.ledger(c["id"], i["id"])["status"] == "stored"


# -- retries and failure states --------------------------------------------------------------


def test_transient_failures_back_off_and_stop_at_the_bound(env):
    c = env.api.customer()
    env.server.fail_next = [unavailable()] * 3
    i = env.api.interaction(c["id"])  # fast path = attempt 1, fails
    entry = env.ledger(c["id"], i["id"])
    assert (entry["status"], entry["attempts"], entry["auto_retry_scheduled"]) == ("failed", 1, True)

    env.run_once()  # not due yet (30 s backoff)
    assert env.server.retain_count() == 1
    env.clock.advance(31)
    env.run_once()  # attempt 2 fails; next backoff 60 s
    env.clock.advance(31)
    env.run_once()
    assert env.server.retain_count() == 2
    env.clock.advance(30)
    env.run_once()  # attempt 3 fails: budget exhausted
    entry = env.ledger(c["id"], i["id"])
    assert (entry["attempts"], entry["max_attempts"], entry["auto_retry_scheduled"]) == (3, 3, False)
    env.clock.advance(86400)
    env.run_once()
    assert env.server.retain_count() == 3  # bounded: no further automatic attempts

    r = env.c.post(f"/api/customers/{c['id']}/memory/writes/{entry['id']}/retry")
    assert r.status_code == 202
    entry = env.ledger(c["id"], i["id"])
    assert entry["status"] == "stored" and entry["attempts"] == 4 and entry["max_attempts"] == 6


def test_rejected_failures_wait_for_explicit_retry(env):
    c = env.api.customer()
    env.server.fail_next = [llm_auth_failure()]
    i = env.api.interaction(c["id"])
    entry = env.ledger(c["id"], i["id"])
    assert entry["last_error_kind"] == "rejected" and entry["auto_retry_scheduled"] is False
    env.clock.advance(86400)
    env.run_once()
    assert env.server.retain_count() == 1
    env.c.post(f"/api/customers/{c['id']}/memory/writes/{entry['id']}/retry")
    assert env.ledger(c["id"], i["id"])["status"] == "stored"


def test_delete_failures_are_retried_automatically_and_manually(env):
    c = env.api.customer()
    i1, i2 = env.api.interaction(c["id"], notes="one"), env.api.interaction(c["id"], notes="two")

    env.server.fail_next = [unavailable()]
    env.c.delete(f"/api/customers/{c['id']}/interactions/{i1['id']}")
    e1 = env.ledger(c["id"], i1["id"])
    assert e1["status"] == "delete_failed" and e1["auto_retry_scheduled"] is True
    assert e1["source_deleted_at"] is not None
    listed = env.c.get(f"/api/customers/{c['id']}/memory/writes?status=delete_failed").json()
    assert [e["source_id"] for e in listed["items"]] == [i1["id"]]
    env.run_once()  # backoff not elapsed: no retry yet
    assert env.ledger(c["id"], i1["id"])["status"] == "delete_failed"
    # backoff grows with the row's total attempts (1 write + 1 delete -> 60 s)
    env.clock.advance(61)
    env.run_once()
    assert env.ledger(c["id"], i1["id"])["status"] == "deleted"

    env.server.fail_next = [llm_auth_failure()]
    env.c.delete(f"/api/customers/{c['id']}/interactions/{i2['id']}")
    e2 = env.ledger(c["id"], i2["id"])
    assert e2["status"] == "delete_failed" and e2["auto_retry_scheduled"] is False
    assert env.c.post(f"/api/customers/{c['id']}/memory/writes/{e2['id']}/retry").status_code == 202
    assert env.ledger(c["id"], i2["id"])["status"] == "deleted"
    assert env.server.all_records(env.bank(c["id"])) == []
    # retrying a finished deletion is a no-op
    assert env.c.post(f"/api/customers/{c['id']}/memory/writes/{e2['id']}/retry").status_code == 200


def test_deleting_a_never_synced_interaction_needs_no_memory_call(env):
    c = env.api.customer()
    with env.no_fast_path():
        i = env.api.interaction(c["id"])
    env.c.delete(f"/api/customers/{c['id']}/interactions/{i['id']}")
    assert env.ledger(c["id"], i["id"])["status"] == "deleted"
    env.run_once()
    assert [op for op, _ in env.server.calls if op in ("retain", "delete_document")] == []


def test_exhausted_expired_leases_become_failed_not_reclaimed_forever(env):
    c = env.api.customer()
    with env.no_fast_path():
        i = env.api.interaction(c["id"])
    with Session(env.c.app.state.engine) as s:
        mw = s.exec(select(MemoryWrite).where(MemoryWrite.source_id == i["id"])).one()
        mw.status, mw.attempts, mw.max_attempts = "in_progress", 3, 3
        mw.claim_token, mw.lease_expires_at = "crashed-worker", env.clock() - timedelta(seconds=1)
        s.add(mw)
        s.commit()
    assert env.run_once()["expired"] == 1
    entry = env.ledger(c["id"], i["id"])
    assert entry["status"] == "failed" and entry["last_error_kind"] == "unexpected"
    assert env.server.retain_count() == 0


# -- durability across restarts ---------------------------------------------------------------


def test_restart_recovers_lost_fast_path_and_interrupted_jobs(monkeypatch, server, tmp_path):
    from app.main import create_app

    settings, router = _configure(monkeypatch)
    service = HindsightMemoryService(settings, router, client_factory=server.factory, retry_base_delay=0)
    with TestClient(create_app(memory_service=service)) as client:
        env = Env(client, server, service)
        c = env.api.customer()
        with env.no_fast_path():
            lost = env.api.interaction(c["id"], notes="fast path lost")
            crashed = env.api.interaction(c["id"], notes="worker crashed mid-write")
        with Session(client.app.state.engine) as s:  # simulate a crash while holding the lease
            mw = s.exec(select(MemoryWrite).where(MemoryWrite.source_id == crashed["id"])).one()
            mw.status, mw.attempts = "in_progress", 1
            mw.claim_token, mw.lease_expires_at = "dead-process", utcnow() - timedelta(seconds=1)
            s.add(mw)
            s.commit()
    assert server.retain_count() == 0

    # "Restart": a new app on the same database with the durable worker enabled.
    monkeypatch.setenv("MEMORY_WORKER_ENABLED", "true")
    get_settings.cache_clear()
    service2 = HindsightMemoryService(get_settings(), router, client_factory=server.factory, retry_base_delay=0)
    with TestClient(create_app(memory_service=service2)) as client:
        env = Env(client, server, service2)
        assert client.app.state.memory_worker.running
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            statuses = {env.ledger(c["id"], x["id"])["status"] for x in (lost, crashed)}
            if statuses == {"stored"}:
                break
            time.sleep(0.05)
        assert statuses == {"stored"}
        queue = client.get("/api/system/memory/queue").json()
        assert queue["worker_running"] is True and queue["due_writes"] == 0
    assert server.retain_count() == 2  # exactly one write per interaction


# -- isolation and provenance -----------------------------------------------------------------


def test_recall_is_isolated_per_customer_and_carries_provenance(env):
    a, b = env.api.customer("Aurora"), env.api.customer("Borealis")
    ia = env.api.interaction(a["id"], notes="Aurora: SOC 2 report required, budget 42k", occurred_at=iso(3))
    ib = env.api.interaction(b["id"], notes="Borealis: on-prem deployment required")

    hits = env.c.post(f"/api/customers/{b['id']}/memory/recall", json={"query": "Aurora SOC 2 budget 42k"}).json()
    assert [h["source"]["source_id"] for h in hits["hits"]] == [ib["id"]]
    assert all(h["ref"]["bank_id"] == env.bank(b["id"]) and "Aurora" not in h["text"] for h in hits["hits"])

    hits = env.c.post(f"/api/customers/{a['id']}/memory/recall", json={"query": "requirements"}).json()["hits"]
    [hit] = hits
    assert hit["provenance"] == "linked"
    assert hit["source"]["source_id"] == ia["id"] and hit["source"]["occurred_at"] == ia["occurred_at"]
    assert hit["source"]["memory_is_current"] is True

    with env.no_fast_path():  # edited but not yet re-synced: flagged, not hidden
        env.c.patch(f"/api/customers/{a['id']}/interactions/{ia['id']}", json={"notes": "Aurora: budget now 35k"})
    [hit] = env.c.post(f"/api/customers/{a['id']}/memory/recall", json={"query": "budget"}).json()["hits"]
    assert hit["source"]["memory_is_current"] is False


def test_recall_drops_foreign_bank_and_deleted_source_hits(env, monkeypatch):
    a, b = env.api.customer("Aurora"), env.api.customer("Borealis")
    ia = env.api.interaction(a["id"])
    env.server.fail_next = []
    with env.no_fast_path():
        env.c.delete(f"/api/customers/{a['id']}/interactions/{ia['id']}")  # delete_pending, memory still there
    stale_ref = env.server.all_records(env.bank(a["id"]))[0]

    async def fake_recall(request):
        return [
            EvidenceHit(ref=MemoryRef(bank_id=env.bank(b["id"]), memory_id="mem-x"), text="foreign",
                        memory_type="world", document_id=None, source_type=None, source_id=None),
            EvidenceHit(ref=MemoryRef(bank_id=env.bank(a["id"]), memory_id=stale_ref.id), text=stale_ref.text,
                        memory_type="world", document_id=stale_ref.document_id, source_type="interaction",
                        source_id=ia["id"]),
            EvidenceHit(ref=MemoryRef(bank_id=env.bank(a["id"]), memory_id="obs-1"), text="an observation",
                        memory_type="observation", document_id=None, source_type=None, source_id=None),
        ]

    monkeypatch.setattr(env.service, "recall_customer_evidence", fake_recall)
    body = env.c.post(f"/api/customers/{a['id']}/memory/recall", json={"query": "x"}).json()
    assert body["excluded_foreign_bank"] == 1 and body["excluded_deleted_sources"] == 1
    assert [(h["ref"]["memory_id"], h["provenance"], h["source"]) for h in body["hits"]] == [("obs-1", "unlinked", None)]


def test_ledger_endpoints_are_customer_scoped(env):
    a, b = env.api.customer("Aurora"), env.api.customer("Borealis")
    ia = env.api.interaction(a["id"])
    write_id = env.ledger(a["id"], ia["id"])["id"]
    assert env.c.get(f"/api/customers/{b['id']}/memory/writes/{write_id}").status_code == 404
    assert env.c.post(f"/api/customers/{b['id']}/memory/writes/{write_id}/retry").status_code == 404
    assert env.c.get(f"/api/customers/{b['id']}/memory/writes").json()["total"] == 0
    queue = env.c.get("/api/system/memory/queue").json()
    assert set(queue) == {"backend", "worker_running", "by_status", "due_writes", "due_deletes",
                          "needs_manual_retry"}
    assert a["id"] not in str(queue) and ia["id"] not in str(queue)


def test_renaming_customer_or_deal_resyncs_its_documents(env):
    c = env.api.customer("Aurora")
    d = env.api.deal(c["id"], "Pune pilot")
    env.api.interaction(c["id"], deal_id=d["id"], notes="deal note")
    env.api.interaction(c["id"], notes="customer note")
    assert env.server.retain_count() == 2

    env.c.patch(f"/api/customers/{c['id']}/deals/{d['id']}", json={"title": "Mumbai rollout"})
    assert env.server.retain_count() == 3  # only the deal's interaction
    env.c.patch(f"/api/customers/{c['id']}", json={"name": "Aurora Freight"})
    assert env.server.retain_count() == 5
    texts = [r.text for r in env.server.all_records(env.bank(c["id"]))]
    assert all(t.startswith("Customer: Aurora Freight") for t in texts)
    assert any("Deal: Mumbai rollout" in t for t in texts)
    env.c.patch(f"/api/customers/{c['id']}/deals/{d['id']}", json={"stage": "closing"})  # not in the document
    assert env.server.retain_count() == 5


def test_crud_unaffected_when_memory_service_fails_everything(env):
    env.server.fail_next = [unavailable()] * 50
    c = env.api.customer()
    d = env.api.deal(c["id"])
    i = env.api.interaction(c["id"], deal_id=d["id"])
    assert env.c.patch(f"/api/customers/{c['id']}/interactions/{i['id']}", json={"notes": "edit"}).status_code == 200
    assert env.c.delete(f"/api/customers/{c['id']}/interactions/{i['id']}").status_code == 204
    assert env.c.get(f"/api/customers/{c['id']}/deals/{d['id']}").status_code == 200
    assert env.ledger(c["id"], i["id"])["status"] in ("delete_pending", "delete_failed")


# -- schema migration v1 -> v2 ----------------------------------------------------------------


def test_v1_database_migrates_to_v2_and_matches_a_fresh_schema(tmp_path):
    old_path = tmp_path / "v1.db"
    old = create_db_engine(f"sqlite:///{old_path.as_posix()}")
    with old.begin() as conn:
        lines = (FIXTURES / "schema_v1.sql").read_text().splitlines()
        sql = " ".join(line for line in lines if not line.startswith("--"))
        for statement in sql.split(";"):
            if statement.strip():
                conn.exec_driver_sql(statement)
        conn.exec_driver_sql("INSERT INTO schema_migrations VALUES (1, '2026-09-28T00:00:00')")
        conn.exec_driver_sql("INSERT INTO customers (id, name, is_synthetic, created_at, updated_at) "
                             "VALUES ('cus_1', 'Aurora', 1, '2026-09-28 00:00:00', '2026-09-28 00:00:00')")
        conn.exec_driver_sql(
            "INSERT INTO memory_writes (id, customer_id, source_type, source_id, bank_id, document_id, status, "
            "attempts, created_at, updated_at) VALUES ('mw_1', 'cus_1', 'interaction', 'int_1', 'b', 'd', "
            "'in_progress', 1, '2026-09-28 00:00:00', '2026-09-28 00:00:00')")

    assert migrate(old, old_path) == 2 == migrations.LATEST_VERSION
    assert list(tmp_path.glob("v1.db.bak-v1-*"))
    with old.connect() as conn:
        row = conn.execute(text("SELECT status, max_attempts, claim_token, source_deleted_at "
                                "FROM memory_writes WHERE id='mw_1'")).one()
    assert tuple(row) == ("pending", 5, None, None)  # interrupted v1 job becomes reclaimable

    fresh_path = tmp_path / "fresh.db"
    fresh = create_db_engine(f"sqlite:///{fresh_path.as_posix()}")
    migrate(fresh, fresh_path)
    old_i, fresh_i = inspect(old), inspect(fresh)
    assert set(old_i.get_table_names()) == set(fresh_i.get_table_names())
    for table in fresh_i.get_table_names():
        cols = lambda insp: {(c["name"], c["nullable"]) for c in insp.get_columns(table)}  # noqa: E731
        idx = lambda insp: {i["name"] for i in insp.get_indexes(table)}  # noqa: E731
        assert cols(old_i) == cols(fresh_i), table
        assert idx(old_i) == idx(fresh_i), table


def test_source_refs_store_bank_and_memory_id_with_source(env):
    c = env.api.customer()
    i = env.api.interaction(c["id"])
    with Session(env.c.app.state.engine) as s:
        [ref] = s.exec(select(MemorySourceRef).where(MemorySourceRef.source_id == i["id"])).all()
    assert (ref.bank_id, ref.source_type, ref.customer_id) == (env.bank(c["id"]), "interaction", c["id"])
    assert ref.memory_id == env.server.all_records(env.bank(c["id"]))[0].id
