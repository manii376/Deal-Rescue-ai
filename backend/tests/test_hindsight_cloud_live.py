"""Opt-in live Hindsight Cloud checks (costs a few cents of Cloud credits).

Runs only with HINDSIGHT_CLOUD_LIVE=1 and a real key in the repo-root .env
(HINDSIGHT_API_KEY). Uses synthetic data, unique per-run bank ids and deletes its banks
afterwards. Writes a JSON report (no key) to backend/spike-reports/.

Operations per run (kept deliberately small): ~5 retains of short texts, ~5 recalls,
1 reflect, a few list/get/delete calls, 3 bank creations/deletions.
"""

import json
import os
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.config import get_settings
from tests.conftest import Api

pytestmark = pytest.mark.skipif(os.getenv("HINDSIGHT_CLOUD_LIVE") != "1",
                                reason="set HINDSIGHT_CLOUD_LIVE=1 (uses Hindsight Cloud credits)")

REPORT_DIR = Path(__file__).resolve().parents[1] / "spike-reports"
PRICES = {"retain_per_m_input_tokens": 10.00, "reflect_per_call": 0.05, "recall_per_m_output_tokens": 0.75}


class Results:
    def __init__(self, run_id: str):
        self.run_id = run_id
        self.checks: list[dict] = []
        self.latency: dict[str, list[float]] = {}
        self.usage: dict[str, int] = {"retain_input_tokens": 0, "reflect_calls": 0, "recall_calls": 0}

    def check(self, cid: str, name: str, ok: bool | None, detail: str = "", data=None):
        status = "UNVERIFIED" if ok is None else ("PASSED" if ok else "FAILED")
        self.checks.append({"id": cid, "name": name, "status": status, "detail": detail, "data": data})
        print(f"{status:<10} {cid:<4} {name}: {detail}")

    def timed(self, label: str, func):
        started = time.perf_counter()
        try:
            return func()
        finally:
            self.latency.setdefault(label, []).append(round(time.perf_counter() - started, 3))

    def write(self, key: str | None):
        est = (self.usage["retain_input_tokens"] / 1e6 * PRICES["retain_per_m_input_tokens"]
               + self.usage["reflect_calls"] * PRICES["reflect_per_call"])
        report = {"run_id": self.run_id, "ran_at": datetime.now(UTC).isoformat(),
                  "endpoint": get_settings().hindsight_cloud_base_url, "checks": self.checks,
                  "latency_seconds": self.latency, "usage": self.usage,
                  "estimated_cost_usd_at_list_price": round(est, 4),
                  "note": "Recall output tokens are not reported by the client; recall cost excluded (tiny)."}
        text = json.dumps(report, indent=2, default=str)
        if key:
            assert key not in text, "API key must never be written to reports"
        REPORT_DIR.mkdir(exist_ok=True)
        path = REPORT_DIR / f"m45-cloud-live-{self.run_id}.json"
        path.write_text(text, encoding="utf-8")
        print(f"report: {path}")


def test_live_hindsight_cloud(monkeypatch):
    from app.main import create_app
    from app.services.hindsight_memory import HindsightMemory, customer_tag, kind_tag

    run = uuid.uuid4().hex[:6]
    monkeypatch.delenv("HINDSIGHT_API_KEY", raising=False)  # let the real key come from .env
    monkeypatch.setenv("MEMORY_BACKEND", "hindsight")
    monkeypatch.setenv("HINDSIGHT_DEPLOYMENT", "cloud")
    monkeypatch.setenv("HINDSIGHT_CUSTOMER_BANK_PREFIX", f"drt{run}-")
    monkeypatch.setenv("HINDSIGHT_OUTCOMES_BANK_ID", f"drt{run}-outcomes")
    monkeypatch.setenv("MEMORY_MAX_ATTEMPTS", "1")  # never silently repeat a billed call
    get_settings.cache_clear()
    settings = get_settings()
    key = settings.hindsight_client_key()
    if not key:
        pytest.fail("BLOCKED: HINDSIGHT_API_KEY is not set in the repo-root .env")
    assert settings.hindsight_effective_base_url == "https://api.hindsight.vectorize.io"

    results = Results(run)
    banks: list[str] = []
    try:
        with TestClient(create_app()) as client:
            api = Api(client)
            service = client.app.state.memory
            original_retain = service.retain_interaction

            async def counting_retain(payload, content_hash=None):
                result = await original_retain(payload, content_hash=content_hash)
                results.usage["retain_input_tokens"] += int((result.usage or {}).get("input_tokens") or 0)
                return result

            service.retain_interaction = counting_retain

            status = results.timed("status_probe", lambda: client.get("/api/system/memory").json())
            results.check("L1", "Authenticated status probe", status["available"] is True,
                          status.get("reason") or "available")
            if not status["available"]:
                pytest.fail(f"BLOCKED: Cloud not available: {status.get('reason')}")

            a, b = api.customer("Aurora Logistics (synthetic)"), api.customer("Borealis Foods (synthetic)")
            bank_a, bank_b = f"drt{run}-{a['id']}", f"drt{run}-{b['id']}"
            banks += [bank_a, bank_b, f"drt{run}-outcomes"]
            deal_a = api.deal(a["id"], "Pune warehouse pilot")

            ia = results.timed("interaction_with_first_retain", lambda: api.interaction(
                a["id"], deal_id=deal_a["id"], occurred_at="2026-09-20T10:00:00Z",
                notes="SYNTHETIC DEMO DATA. CFO Priya Raman said the annual budget is capped at USD 42,000 "
                      "and a SOC 2 Type II report is mandatory before signing."))
            ib = results.timed("interaction_with_first_retain", lambda: api.interaction(
                b["id"], occurred_at="2026-09-21T10:00:00Z",
                notes="SYNTHETIC DEMO DATA. Head of IT Marcus Lee insisted on an on-premises deployment "
                      "and mentioned a budget of USD 310,000."))
            ia2 = results.timed("interaction_retain_existing_bank", lambda: api.interaction(
                a["id"], deal_id=deal_a["id"], occurred_at="2026-09-22T10:00:00Z", channel="email",
                notes="SYNTHETIC DEMO DATA. Aurora asked for a revised quote by 30 September."))

            ledger = lambda cid, iid: client.get(f"/api/customers/{cid}/memory/writes",  # noqa: E731
                                                 params={"source_id": iid}).json()["items"][0]
            la, lb, la2 = ledger(a["id"], ia["id"]), ledger(b["id"], ib["id"]), ledger(a["id"], ia2["id"])
            ok = all(x["status"] == "stored" for x in (la, lb, la2))
            results.check("L2", "Retain (LLM extraction server-side) stores and records refs", ok,
                          f"statuses={[la['status'], lb['status'], la2['status']]}, "
                          f"refs={[la['memory_ref_count'], lb['memory_ref_count'], la2['memory_ref_count']]}, "
                          f"errors={[x['last_error'] for x in (la, lb, la2) if x['last_error']]}")
            results.check("L3", "Memory ids recorded with their bank", la["memory_ref_count"] >= 1 and
                          la["bank_id"] == bank_a and lb["bank_id"] == bank_b,
                          f"bank_a ok={la['bank_id'] == bank_a}, bank_b ok={lb['bank_id'] == bank_b}")

            recall_a = results.timed("recall", lambda: client.post(
                f"/api/customers/{a['id']}/memory/recall",
                json={"query": "What budget and compliance requirements were stated?"}).json())
            results.usage["recall_calls"] += 1
            hits = recall_a.get("hits", [])
            linked = [h for h in hits if h["provenance"] == "linked"]
            results.check("L4", "Recall returns this customer's evidence linked to source records",
                          bool(linked) and all(h["ref"]["bank_id"] == bank_a for h in hits)
                          and {h["source"]["source_id"] for h in linked} <= {ia["id"], ia2["id"]},
                          f"hits={len(hits)}, linked={len(linked)}, "
                          f"unlinked_types={sorted({h['memory_type'] for h in hits if h['provenance'] == 'unlinked'})}",
                          data=[{"type": h["memory_type"], "provenance": h["provenance"], "text": h["text"][:160]}
                                for h in hits[:6]])

            cross = results.timed("recall", lambda: client.post(
                f"/api/customers/{b['id']}/memory/recall",
                json={"query": "Aurora SOC 2 Priya Raman 42,000 budget"}).json())
            results.usage["recall_calls"] += 1
            leaked = [h for h in cross.get("hits", [])
                      if h["ref"]["bank_id"] != bank_b or any(t in h["text"] for t in ("Aurora", "Priya", "SOC 2", "42,000"))]
            results.check("L5", "Customer B's recall never returns customer A's memories", not leaked,
                          f"hits={len(cross.get('hits', []))}, leaked={len(leaked)}, "
                          f"excluded_foreign_bank={cross.get('excluded_foreign_bank')}")

            reflect = results.timed("reflect", lambda: client.post(
                f"/api/customers/{a['id']}/memory/reflect",
                json={"question": "What budget limit and compliance requirement has this customer stated?"}))
            results.usage["reflect_calls"] += 1
            body = reflect.json()
            if reflect.status_code == 200:
                ev = body["evidence"]
                ok = bool(ev) and all(e["ref"]["bank_id"] == bank_a for e in ev) and body["kind"] == "inference"
                results.check("L6", "Reflect answers with evidence mapped back to source records", ok,
                              f"evidence={len(ev)}, linked={sum(e['provenance'] == 'linked' for e in ev)}, "
                              f"unresolved={len(body['unresolved_memory_ids'])}",
                              data={"text_excerpt": body["text"][:300],
                                    "evidence": [{"provenance": e["provenance"], "type": e["memory_type"],
                                                  "source": e["source"]} for e in ev[:6]]})
            else:
                results.check("L6", "Reflect answers with evidence mapped back to source records", False,
                              f"HTTP {reflect.status_code}: {body.get('error', {}).get('message', '')[:200]}")

            # Update -> replace (update_mode="replace" on the same document id)
            client.patch(f"/api/customers/{a['id']}/interactions/{ia['id']}", json={
                "notes": "SYNTHETIC DEMO DATA. CFO Priya Raman said the annual budget is now capped at USD 35,000 "
                         "and a SOC 2 Type II report is mandatory before signing."})
            after = ledger(a["id"], ia["id"])

            async def list_doc(bank, doc):
                mem = HindsightMemory(settings.hindsight_effective_base_url, bank, api_key=key)
                try:
                    return await mem.list_memories(document_id=doc)
                finally:
                    await mem.aclose()

            records = client.portal.call(list_doc, bank_a, f"interaction:{ia['id']}")
            texts = [r.text for r in records]
            results.check("L7", "Editing an interaction replaces its Cloud document (no stale facts)",
                          after["status"] == "stored" and bool(texts) and not any("42,000" in t or "42000" in t
                                                                                 for t in texts),
                          f"status={after['status']}, memories={len(texts)}",
                          data=[t[:160] for t in texts])

            # Delete -> document removed
            client.delete(f"/api/customers/{b['id']}/interactions/{ib['id']}")
            deleted = ledger(b["id"], ib["id"])
            leftover = client.portal.call(list_doc, bank_b, f"interaction:{ib['id']}")
            results.check("L8", "Deleting an interaction removes its Cloud memories",
                          deleted["status"] == "deleted" and leftover == [],
                          f"status={deleted['status']}, remaining={len(leftover)}")

            # Shared outcomes bank (the app writes outcomes in M6; this checks bank support only)
            async def outcomes_roundtrip():
                mem = HindsightMemory(settings.hindsight_effective_base_url, f"drt{run}-outcomes", api_key=key)
                try:
                    await mem.ensure_bank(name="Deal Rescue outcomes (synthetic test)",
                                          retain_mission="Recorded sales deal outcomes.",
                                          reflect_mission="Answer only from recorded outcomes.")
                    started = time.perf_counter()
                    outcome = await mem.retain(
                        "SYNTHETIC DEMO DATA. Outcome: LOST. A 20% discount was offered late; the recorded reason "
                        "was that the security review never started.",
                        document_id=f"outcome:drt{run}-deal", tags=[customer_tag(a["id"]), kind_tag("outcome")],
                        metadata={"source_type": "outcome", "source_id": f"drt{run}-deal"})
                    results.latency.setdefault("retain_direct", []).append(round(time.perf_counter() - started, 3))
                    results.usage["retain_input_tokens"] += int((outcome.usage or {}).get("input_tokens") or 0)
                    hits = await mem.recall("deals lost after a late discount", tags=[kind_tag("outcome")],
                                            tags_match="all_strict")
                    results.usage["recall_calls"] += 1
                    return hits
                finally:
                    await mem.aclose()

            try:
                out_hits = client.portal.call(outcomes_roundtrip)
                results.check("L9", "Shared outcomes bank: retain + tag-filtered recall", bool(out_hits) and all(
                    h.document_id == f"outcome:drt{run}-deal" for h in out_hits),
                    f"hits={len(out_hits)}")
            except Exception as exc:  # report, do not hide
                results.check("L9", "Shared outcomes bank: retain + tag-filtered recall", False,
                              f"{type(exc).__name__}: {str(exc)[:200]}")
    finally:
        async def cleanup():
            deleted = []
            for bank in banks:
                mem = HindsightMemory(settings.hindsight_effective_base_url, bank, api_key=key)
                try:
                    await mem.delete_bank()
                    deleted.append(bank)
                except Exception as exc:
                    print(f"cleanup of {bank} failed: {type(exc).__name__}")
                finally:
                    await mem.aclose()
            return deleted

        import asyncio

        removed = asyncio.run(cleanup()) if banks else []
        results.check("L10", "Test banks deleted", len(removed) == len(banks), f"{len(removed)}/{len(banks)}")
        results.write(key)

    failed = [c for c in results.checks if c["status"] == "FAILED"]
    assert not failed, failed


def test_live_cloud_observations_after_update_and_delete(monkeypatch):
    """Characterise (not assume) what happens to consolidated observations when their source
    document is replaced or deleted. Records findings; asserts only that the calls worked."""
    import asyncio

    from app.services.hindsight_memory import HindsightMemory

    monkeypatch.delenv("HINDSIGHT_API_KEY", raising=False)
    monkeypatch.setenv("HINDSIGHT_DEPLOYMENT", "cloud")
    get_settings.cache_clear()
    settings = get_settings()
    key = settings.hindsight_client_key()
    if not key:
        pytest.fail("BLOCKED: HINDSIGHT_API_KEY is not set in the repo-root .env")
    run = uuid.uuid4().hex[:6]
    results = Results(f"obs-{run}")
    bank, doc = f"drt{run}-obs", f"interaction:obs-{run}"

    async def observations(mem, token):
        found = await mem.list_memories(memory_type="observation")
        return [o.text[:200] for o in found], [o.text for o in found if token in o.text]

    async def wait_for_observations(mem, token, want_present: bool, seconds: int = 90):
        texts, matching = [], []
        for _ in range(seconds // 5):
            texts, matching = await observations(mem, token)
            if bool(matching) == want_present and texts:
                break
            await asyncio.sleep(5)
        return texts, matching

    async def run_probe():
        mem = HindsightMemory(settings.hindsight_effective_base_url, bank, api_key=key)
        try:
            await mem.ensure_bank(name="Deal Rescue observation probe (synthetic)",
                                  retain_mission="Synthetic sales notes.", reflect_mission="Answer from memories.")
            r1 = await mem.retain("SYNTHETIC DEMO DATA. Customer Aurora (probe): CFO said the annual budget is "
                                  "capped at USD 42,000.", document_id=doc, tags=["kind:interaction"],
                                  update_mode="replace")
            results.usage["retain_input_tokens"] += int((r1.usage or {}).get("input_tokens") or 0)
            texts, old = await wait_for_observations(mem, "42,000", want_present=True)
            results.check("O1", "Observation formed from the original fact", bool(old) if texts else None,
                          f"observations={len(texts)}, mentioning 42,000={len(old)}", data=texts)

            r2 = await mem.retain("SYNTHETIC DEMO DATA. Customer Aurora (probe): CFO said the annual budget is "
                                  "now capped at USD 35,000.", document_id=doc, tags=["kind:interaction"],
                                  update_mode="replace")
            results.usage["retain_input_tokens"] += int((r2.usage or {}).get("input_tokens") or 0)
            texts, stale = await wait_for_observations(mem, "42,000", want_present=False)
            facts = [r.text[:160] for r in await mem.list_memories(document_id=doc)]
            results.check("O2", "After replace: no observation still states the old value", not stale,
                          f"observations={len(texts)}, still mentioning 42,000={len(stale)}; "
                          f"document facts={len(facts)}", data={"observations": texts, "document_facts": facts})

            await mem.delete_document(doc)
            texts, remaining = await wait_for_observations(mem, "Aurora", want_present=False, seconds=60)
            results.check("O3", "After deleting the document: no observation about it remains", not remaining,
                          f"observations={len(texts)}, still about Aurora={len(remaining)}", data=texts)
        finally:
            try:
                await mem.delete_bank()
            finally:
                await mem.aclose()

    try:
        asyncio.run(run_probe())
    finally:
        results.write(key)
