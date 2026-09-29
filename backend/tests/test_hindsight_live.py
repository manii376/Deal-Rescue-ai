"""Live contract tests against a running Hindsight server.

Skipped unless HINDSIGHT_LIVE=1. Uses the spike in "chunks" extraction mode so
it needs no LLM credentials; the LLM-dependent checks (reflect, extraction,
observation history) are asserted only when HINDSIGHT_LIVE_LLM=1.
"""

import os

import pytest

pytestmark = [
    pytest.mark.live,
    pytest.mark.skipif(os.getenv("HINDSIGHT_LIVE") != "1", reason="set HINDSIGHT_LIVE=1 to run"),
]

NO_LLM_CHECKS = ["C01", "C02", "C03", "C04", "C05", "C06", "C08a", "C09", "C10a", "C10b"]


async def test_spike_checks_without_llm():
    from scripts.hindsight_spike import run_spike

    spike, _ = await run_spike(["--extraction-mode", "chunks", "--observation-wait", "0",
                                "--latency-samples", "1", "--cleanup"])
    statuses = {cid: spike.checks[cid].status for cid in NO_LLM_CHECKS}
    assert all(s == "PASSED" for s in statuses.values()), statuses


async def _server_forces_chunks_mode() -> bool:
    """True if the server has no LLM (e.g. HINDSIGHT_API_LLM_PROVIDER=none forces chunks mode).

    In that mode "LLM-backed" retain silently stores raw chunks, so LLM checks would pass
    without any LLM involved.
    """
    from app.config import get_settings
    from app.services.hindsight_memory import HindsightMemory

    probe = HindsightMemory(get_settings().hindsight_base_url, "deal-rescue-llm-probe")
    try:
        await probe.ensure_bank(name="LLM capability probe", retain_mission="probe", reflect_mission="probe")
        resolved = (await probe._client.aget_bank_config("deal-rescue-llm-probe"))["config"]
        return resolved.get("retain_extraction_mode") == "chunks"
    finally:
        try:
            await probe.delete_bank()
        finally:
            await probe.aclose()


@pytest.mark.skipif(os.getenv("HINDSIGHT_LIVE_LLM") != "1", reason="set HINDSIGHT_LIVE_LLM=1 (needs a real key)")
async def test_spike_checks_with_llm():
    from scripts.hindsight_spike import run_spike

    if await _server_forces_chunks_mode():
        pytest.fail("BLOCKED: the Hindsight server has no LLM (chunks mode forced); "
                    "LLM-backed behaviour cannot be verified against it")
    spike, _ = await run_spike(["--observation-wait", "20", "--cleanup"])
    for cid in ["C03", "C04", "C05", "C06", "C07", "C08a", "C09"]:
        assert spike.checks[cid].status == "PASSED", (cid, spike.checks[cid].detail)


async def test_memory_service_against_real_server_without_llm(monkeypatch):
    """M2: the app's MemoryService + sync ledger against a real Hindsight, chunks mode (no LLM).

    Verifies real bank ids (prefix + "cus_<32 hex>", 53 chars), retain -> (bank_id, memory_id)
    refs, no duplicate on unchanged resync, recall mapping, and deletion.
    """
    from httpx import ASGITransport, AsyncClient

    from app.config import get_settings
    from app.main import create_app
    from app.services.hindsight_memory import HindsightMemory

    monkeypatch.setenv("MEMORY_BACKEND", "hindsight")
    monkeypatch.setenv("HINDSIGHT_EXTRACTION_MODE", "chunks")
    get_settings.cache_clear()
    app = create_app()
    async with app.router.lifespan_context(app):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            assert (await c.get("/api/system/memory")).json()["available"] is True
            cid = (await c.post("/api/customers", json={"name": "Live test (synthetic)", "is_synthetic": True})).json()["id"]
            bank_id = f"deal-rescue-cust-{cid}"
            try:
                base = f"/api/customers/{cid}/interactions"
                i = (await c.post(base, json={"occurred_at": "2026-09-20T10:00:00Z", "channel": "call",
                                              "notes": "SYNTHETIC: budget capped at USD 42,000"})).json()
                mem = (await c.get(f"{base}/{i['id']}/memory")).json()
                assert mem["status"] == "stored", mem
                assert mem["bank_id"] == bank_id and len(mem["memory_refs"]) == 1
                first_ref = mem["memory_refs"][0]

                again = await c.post(f"{base}/{i['id']}/memory/sync")
                assert again.status_code == 200 and again.json()["attempts"] == 1  # not rewritten

                probe = HindsightMemory(get_settings().hindsight_base_url, bank_id)
                try:
                    assert len(await probe.list_memories(document_id=f"interaction:{i['id']}")) == 1
                finally:
                    await probe.aclose()

                hits = (await c.post(f"/api/customers/{cid}/memory/recall", json={"query": "budget"})).json()["hits"]
                assert hits and hits[0]["ref"] == first_ref
                assert {k: hits[0]["source"][k] for k in ("source_type", "source_id")} == {
                    "source_type": "interaction", "source_id": i["id"]}
                assert hits[0]["provenance"] == "linked"
                assert hits[0]["source"]["occurred_at"] == "2026-09-20T10:00:00Z"

                assert (await c.delete(f"{base}/{i['id']}")).status_code == 204
                probe = HindsightMemory(get_settings().hindsight_base_url, bank_id)
                try:
                    assert await probe.list_memories(document_id=f"interaction:{i['id']}") == []
                finally:
                    await probe.aclose()
            finally:
                cleanup = HindsightMemory(get_settings().hindsight_base_url, bank_id)
                try:
                    await cleanup.delete_bank()
                finally:
                    await cleanup.aclose()


# -- M3: real-server checks in no-LLM "chunks" mode ---------------------------------------------


def _chunks_env(monkeypatch, **extra):
    from app.config import get_settings

    monkeypatch.setenv("MEMORY_BACKEND", "hindsight")
    monkeypatch.setenv("HINDSIGHT_EXTRACTION_MODE", "chunks")
    for key, value in extra.items():
        monkeypatch.setenv(key, value)
    get_settings.cache_clear()
    return get_settings()


async def _delete_banks(base_url: str, bank_ids: list[str]) -> None:
    from app.services.hindsight_memory import HindsightMemory

    for bank_id in bank_ids:
        client = HindsightMemory(base_url, bank_id)
        try:
            await client.delete_bank()
        except Exception:
            pass
        finally:
            await client.aclose()


async def test_m3_update_replaces_document_on_real_server(monkeypatch):
    from httpx import ASGITransport, AsyncClient

    from app.main import create_app
    from app.services.hindsight_memory import HindsightMemory

    settings = _chunks_env(monkeypatch)
    app = create_app()
    banks = []
    async with app.router.lifespan_context(app):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            cid = (await c.post("/api/customers", json={"name": "M3 live (synthetic)", "is_synthetic": True})).json()["id"]
            banks.append(f"deal-rescue-cust-{cid}")
            try:
                base = f"/api/customers/{cid}/interactions"
                i = (await c.post(base, json={"occurred_at": "2026-09-20T10:00:00Z", "channel": "call",
                                              "notes": "SYNTHETIC version one: budget 42k"})).json()
                first = (await c.get(f"{base}/{i['id']}/memory")).json()
                assert first["status"] == "stored"
                await c.patch(f"{base}/{i['id']}", json={"notes": "SYNTHETIC version two: budget 35k"})
                second = (await c.get(f"{base}/{i['id']}/memory")).json()
                assert second["status"] == "stored" and second["attempts"] == 2

                probe = HindsightMemory(settings.hindsight_base_url, banks[0])
                try:
                    records = await probe.list_memories(document_id=f"interaction:{i['id']}")
                finally:
                    await probe.aclose()
                assert len(records) == 1, [r.text for r in records]
                assert "version two" in records[0].text and "version one" not in records[0].text
                assert records[0].metadata["source_id"] == i["id"]
                assert second["memory_refs"] == [{"bank_id": banks[0], "memory_id": records[0].id}]
                assert first["memory_refs"][0]["memory_id"] != records[0].id
            finally:
                await _delete_banks(settings.hindsight_base_url, banks)


async def test_m3_customer_isolation_on_real_server(monkeypatch):
    from httpx import ASGITransport, AsyncClient

    from app.main import create_app

    settings = _chunks_env(monkeypatch)
    app = create_app()
    banks = []
    async with app.router.lifespan_context(app):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            ids = {}
            for name, notes in (("Aurora (synthetic)", "SYNTHETIC Aurora: SOC 2 report required, budget 42k"),
                                ("Borealis (synthetic)", "SYNTHETIC Borealis: on-prem deployment required")):
                cid = (await c.post("/api/customers", json={"name": name, "is_synthetic": True})).json()["id"]
                banks.append(f"deal-rescue-cust-{cid}")
                await c.post(f"/api/customers/{cid}/interactions", json={
                    "occurred_at": "2026-09-20T10:00:00Z", "channel": "call", "notes": notes})
                ids[name.split()[0]] = cid
            try:
                for own, other_terms in (("Borealis", "Aurora SOC 2 budget 42k"), ("Aurora", "Borealis on-prem")):
                    body = (await c.post(f"/api/customers/{ids[own]}/memory/recall",
                                         json={"query": other_terms})).json()
                    other = "Aurora" if own == "Borealis" else "Borealis"
                    assert all(other not in h["text"] for h in body["hits"]), body
                    assert all(h["ref"]["bank_id"] == f"deal-rescue-cust-{ids[own]}" for h in body["hits"])
                    assert all(h["provenance"] == "linked" for h in body["hits"])
            finally:
                await _delete_banks(settings.hindsight_base_url, banks)


async def test_m3_outage_then_restart_recovers_on_real_server(monkeypatch):
    import asyncio

    from httpx import ASGITransport, AsyncClient

    from app.main import create_app

    # Phase 1: Hindsight "down" (unreachable URL) — the interaction is saved, memory fails.
    settings = _chunks_env(monkeypatch, HINDSIGHT_BASE_URL="http://127.0.0.1:9", MEMORY_MAX_ATTEMPTS="1",
                           HINDSIGHT_TIMEOUT_SECONDS="5", MEMORY_RETRY_BASE_SECONDS="0.2")
    app1 = create_app()
    async with app1.router.lifespan_context(app1):
        async with AsyncClient(transport=ASGITransport(app=app1), base_url="http://test") as c:
            cid = (await c.post("/api/customers", json={"name": "Outage (synthetic)", "is_synthetic": True})).json()["id"]
            i = (await c.post(f"/api/customers/{cid}/interactions", json={
                "occurred_at": "2026-09-20T10:00:00Z", "channel": "call", "notes": "SYNTHETIC outage note"})).json()
            mem = (await c.get(f"/api/customers/{cid}/interactions/{i['id']}/memory")).json()
            assert mem["status"] == "failed" and "cannot reach" in mem["last_error"]

    # Phase 2: "restart" against the real server with the durable worker on (same database).
    settings = _chunks_env(monkeypatch, HINDSIGHT_BASE_URL="http://127.0.0.1:8888", MEMORY_WORKER_ENABLED="true",
                           MEMORY_WORKER_POLL_SECONDS="0.2", MEMORY_RETRY_BASE_SECONDS="0.2")
    app2 = create_app()
    try:
        async with app2.router.lifespan_context(app2):
            async with AsyncClient(transport=ASGITransport(app=app2), base_url="http://test") as c:
                for _ in range(100):
                    mem = (await c.get(f"/api/customers/{cid}/interactions/{i['id']}/memory")).json()
                    if mem["status"] == "stored":
                        break
                    await asyncio.sleep(0.1)
                assert mem["status"] == "stored" and mem["attempts"] == 2, mem
                assert len(mem["memory_refs"]) == 1
    finally:
        await _delete_banks(settings.hindsight_base_url, [f"deal-rescue-cust-{cid}"])


@pytest.mark.skipif(os.getenv("HINDSIGHT_LIVE_LLM") == "1", reason="only meaningful when the server has no LLM")
async def test_m3_reflect_without_llm_is_an_explicit_error_not_a_fabrication(monkeypatch):
    """With HINDSIGHT_API_LLM_PROVIDER=none the server rejects reflect; the API must say so."""
    from httpx import ASGITransport, AsyncClient

    from app.main import create_app

    settings = _chunks_env(monkeypatch)
    app = create_app()
    async with app.router.lifespan_context(app):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            cid = (await c.post("/api/customers", json={"name": "Reflect (synthetic)", "is_synthetic": True})).json()["id"]
            try:
                await c.post(f"/api/customers/{cid}/interactions", json={
                    "occurred_at": "2026-09-20T10:00:00Z", "channel": "call", "notes": "SYNTHETIC budget 42k"})
                r = await c.post(f"/api/customers/{cid}/memory/reflect", json={"question": "What is the budget?"})
                assert r.status_code == 502, r.text
                assert r.json()["error"]["code"] == "memory_backend_error" and "HTTP 400" in r.json()["error"]["message"]
            finally:
                await _delete_banks(settings.hindsight_base_url, [f"deal-rescue-cust-{cid}"])
