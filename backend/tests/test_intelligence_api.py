"""Deal-intelligence API: scoping, filters, pagination, determinism, query budget, configuration."""

from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import event
from sqlmodel import Session

from app.config import get_settings
from app.intelligence.rules import IntelConfig
from app.intelligence.service import IntelligenceService
from tests.conftest import Api

NOW = datetime.now(UTC).replace(microsecond=0)


def ts(days_ago: float) -> str:
    return (NOW - timedelta(days=days_ago)).isoformat()


AS_OF = NOW.isoformat()


@pytest.fixture
def scenario(api):
    """Customer A: one stalled deal with an overdue and a due-soon commitment, one healthy deal,
    one won deal; customer B: one stalled deal."""
    a, b = api.customer("Aurora"), api.customer("Borealis")
    api.stakeholder(a["id"])
    stalled = api.deal(a["id"], "Stalled pilot", stage="proposal", value_minor=100, currency="USD",
                       expected_close_date=(NOW + timedelta(days=30)).date().isoformat(), owner_name="Asha")
    healthy = api.deal(a["id"], "Healthy rollout", stage="negotiation", value_minor=200, currency="USD",
                       expected_close_date=(NOW + timedelta(days=30)).date().isoformat(), owner_name="Asha")
    won = api.deal(a["id"], "Won deal", stage="closing", status="won")
    last = api.interaction(a["id"], deal_id=stalled["id"], occurred_at=ts(12), notes="Budget call")
    api.interaction(a["id"], deal_id=healthy["id"], occurred_at=ts(1), notes="Recent call")
    overdue = api.commitment(a["id"], stalled["id"], due_date=(NOW - timedelta(days=3)).date().isoformat(),
                             source_interaction_id=last["id"], owner_name="Asha")
    soon = api.commitment(a["id"], healthy["id"], due_date=(NOW + timedelta(days=3)).date().isoformat(),
                          owner_party="customer")
    api.commitment(a["id"], won["id"])  # open commitment on a won deal
    b_deal = api.deal(b["id"], "B deal", value_minor=5, currency="USD", owner_name="Lee",
                      expected_close_date=(NOW + timedelta(days=30)).date().isoformat())
    api.stakeholder(b["id"], "Marcus Lee")
    api.interaction(b["id"], deal_id=b_deal["id"], occurred_at=ts(40))
    return {"a": a, "b": b, "stalled": stalled, "healthy": healthy, "won": won, "last": last,
            "overdue": overdue, "soon": soon, "b_deal": b_deal}


def signals(client, cid, **params):
    r = client.get(f"/api/customers/{cid}/intelligence/signals", params={"as_of": AS_OF, **params})
    assert r.status_code == 200, r.text
    return r.json()


def test_signals_are_explained_traceable_and_ordered(client, scenario):
    body = signals(client, scenario["a"]["id"])
    assert body["as_of"].startswith(NOW.strftime("%Y-%m-%dT%H:%M:%S")) and body["timezone"] == "UTC"
    got = [(s["type"], s["severity"], s["deal_id"]) for s in body["items"]]
    assert got == [
        ("commitment_overdue", "high", scenario["stalled"]["id"]),         # our commitment, 3 days late
        ("stalled_deal", "medium", scenario["stalled"]["id"]),             # 12 days >= 10, < 2 x 10
        ("commitment_due_soon", "low", scenario["healthy"]["id"]),         # low, urgency 7 - 3 = 4
        ("open_commitment_on_closed_deal", "low", scenario["won"]["id"]),  # low, urgency 0
    ]
    stall = next(s for s in body["items"] if s["type"] == "stalled_deal")
    assert stall["rule"]["thresholds"]["stall_days"] == 10 and stall["facts"]["days_inactive"] == 12
    assert stall["severity"] == "medium"  # 12 < 20 (2 x threshold)
    overdue = next(s for s in body["items"] if s["type"] == "commitment_overdue")
    assert overdue["facts"]["days_overdue"] == 3 and overdue["facts"]["owner_name"] == "Asha"
    assert {(r["type"], r["id"]) for r in overdue["sources"]} == {
        ("commitment", scenario["overdue"]["id"]), ("deal", scenario["stalled"]["id"]),
        ("interaction", scenario["last"]["id"])}
    # every source reference resolves to a record of this customer
    cid = scenario["a"]["id"]
    paths = {"deal": "deals", "interaction": "interactions", "commitment": "commitments"}
    for s in body["items"]:
        for ref in s["sources"]:
            url = f"/api/customers/{cid}" if ref["type"] == "customer" else f"/api/customers/{cid}/{paths[ref['type']]}/{ref['id']}"
            assert client.get(url).status_code == 200, (s["id"], ref)
    # no signals for the healthy deal's activity, none leak from customer B
    assert all(s["customer_id"] == cid for s in body["items"])
    assert [s["severity"] for s in body["items"]] == sorted(
        [s["severity"] for s in body["items"]], key=["high", "medium", "low"].index)


def test_filters_and_pagination(client, scenario):
    cid = scenario["a"]["id"]
    total = signals(client, cid)["total"]
    assert signals(client, cid, type="commitment_overdue")["total"] == 1
    assert signals(client, cid, type=["commitment_overdue", "stalled_deal"])["total"] == 2
    assert signals(client, cid, severity="low")["total"] == 2
    assert signals(client, cid, min_severity="medium")["total"] == 2
    assert signals(client, cid, category="commitment")["total"] == 2
    assert signals(client, cid, nature="finding")["total"] == total
    only_stalled = signals(client, cid, deal_id=scenario["stalled"]["id"])
    assert {s["deal_id"] for s in only_stalled["items"]} == {scenario["stalled"]["id"]}
    first, second = signals(client, cid, limit=2, offset=0), signals(client, cid, limit=2, offset=2)
    assert first["total"] == second["total"] == total
    assert [s["id"] for s in first["items"] + second["items"]] == [s["id"] for s in signals(client, cid)["items"]]
    assert signals(client, cid, offset=total)["items"] == []


def test_invalid_parameters(client, scenario):
    cid = scenario["a"]["id"]
    base = f"/api/customers/{cid}/intelligence/signals"
    assert client.get(base, params={"as_of": "2026-09-28T12:00:00"}).status_code == 422  # naive
    assert client.get(base, params={"type": "win_probability"}).status_code == 422
    assert client.get(base, params={"severity": "critical"}).status_code == 422
    assert client.get(base, params={"limit": 0}).status_code == 422


def test_customer_isolation_and_ownership(client, scenario):
    a, b = scenario["a"]["id"], scenario["b"]["id"]
    b_signals = signals(client, b)
    assert [(s["type"], s["deal_id"]) for s in b_signals["items"]] == [("stalled_deal", scenario["b_deal"]["id"])]
    # another customer's deal as a filter -> 422 invalid_reference, never its signals
    r = client.get(f"/api/customers/{b}/intelligence/signals", params={"deal_id": scenario["stalled"]["id"]})
    assert r.status_code == 422 and r.json()["error"]["code"] == "invalid_reference"
    r = client.get(f"/api/customers/{a}/intelligence/signals", params={"deal_id": "deal_missing"})
    assert r.status_code == 422
    # deal intelligence through the wrong customer path -> 404
    r = client.get(f"/api/customers/{b}/deals/{scenario['stalled']['id']}/intelligence")
    assert r.status_code == 404 and r.json()["error"]["code"] == "not_found"
    assert client.get(f"/api/customers/{a}/deals/deal_missing/intelligence").status_code == 404
    for path in ("intelligence/signals", "intelligence/deals"):
        r = client.get(f"/api/customers/cus_missing/{path}")
        assert r.status_code == 404 and r.json()["error"]["message"] == "Customer not found"
    # there is no unscoped listing
    assert client.get("/api/intelligence/signals").status_code == 404
    assert client.get("/api/intelligence/deals").status_code == 404


def test_deal_intelligence_detail(client, scenario):
    cid, did = scenario["a"]["id"], scenario["stalled"]["id"]
    body = client.get(f"/api/customers/{cid}/deals/{did}/intelligence", params={"as_of": AS_OF}).json()
    assert body["activity"]["last_meaningful_interaction_id"] == scenario["last"]["id"]
    assert body["activity"]["days_since_meaningful_activity"] == 12
    assert body["activity"]["stall_threshold_days"] == 10
    assert body["commitments"] == {"open": 1, "overdue": 1, "due_soon": 0, "undated": 0, "done": 0, "cancelled": 0}
    assert body["highest_severity"] == "high" and body["signal_counts"] == {"high": 1, "medium": 1, "low": 0}
    assert body["is_closed"] is False and body["customer_signals"] == []
    won = client.get(f"/api/customers/{cid}/deals/{scenario['won']['id']}/intelligence").json()
    assert won["is_closed"] is True and [s["type"] for s in won["signals"]] == ["open_commitment_on_closed_deal"]


def test_deal_attention_list_order_and_filters(client, scenario):
    cid = scenario["a"]["id"]
    url = f"/api/customers/{cid}/intelligence/deals"
    body = client.get(url, params={"as_of": AS_OF}).json()
    # stalled deal first (high); healthy and won tie (one low signal each) -> ordered by deal id
    tied = sorted([scenario["healthy"]["id"], scenario["won"]["id"]])
    assert [d["deal_id"] for d in body["items"]] == [scenario["stalled"]["id"], *tied]
    assert body["items"][0]["highest_severity"] == "high"
    assert client.get(url, params={"as_of": AS_OF, "status": "won"}).json()["total"] == 1
    assert client.get(url, params={"as_of": AS_OF, "min_severity": "high"}).json()["total"] == 1
    assert client.get(url, params={"as_of": AS_OF, "limit": 1, "offset": 1}).json()["items"][0]["deal_id"] == tied[0]


def test_completing_a_commitment_removes_its_signal(client, scenario):
    cid = scenario["a"]["id"]
    client.patch(f"/api/customers/{cid}/commitments/{scenario['overdue']['id']}", json={"status": "done"})
    assert signals(client, cid, type="commitment_overdue")["total"] == 0


def test_reference_time_makes_results_reproducible(client, scenario):
    cid = scenario["a"]["id"]
    first = client.get(f"/api/customers/{cid}/intelligence/signals", params={"as_of": AS_OF}).json()
    second = client.get(f"/api/customers/{cid}/intelligence/signals", params={"as_of": AS_OF}).json()
    assert first == second
    later = signals(client, cid, as_of=(NOW + timedelta(days=10)).isoformat())
    stall = next(s for s in later["items"] if s["type"] == "stalled_deal")
    assert stall["severity"] == "high" and stall["facts"]["days_inactive"] == 22  # moved past 2 x 10 days
    earlier = signals(client, cid, as_of=(NOW - timedelta(days=5)).isoformat())
    assert "stalled_deal" not in {s["type"] for s in earlier["items"]}


def test_query_count_does_not_grow_with_deals(client, api):
    small, large = api.customer("Small"), api.customer("Large")
    api.interaction(small["id"], deal_id=api.deal(small["id"])["id"])
    for n in range(12):
        d = api.deal(large["id"], f"deal {n}")
        api.interaction(large["id"], deal_id=d["id"])
        api.commitment(large["id"], d["id"])
    engine = client.app.state.engine
    service = IntelligenceService(IntelConfig())
    statements = []

    def count(*args, **kwargs):
        statements.append(1)

    event.listen(engine, "before_cursor_execute", count)
    try:
        for cid in (small["id"], large["id"]):
            statements.clear()
            with Session(engine) as session:
                service.evaluate_customer(session, cid, NOW)
            assert len(statements) == 4, (cid, len(statements))
    finally:
        event.remove(engine, "before_cursor_execute", count)


def test_rule_catalog_has_no_customer_data(client, scenario):
    body = client.get("/api/intelligence/rules").json()
    assert body["thresholds"]["stall_days_by_stage"]["proposal"] == 10
    assert {r["id"] for r in body["rules"]} >= {"stalled_deal", "commitment_overdue", "commitment_due_soon"}
    assert any("win probability" in line for line in body["not_included"])
    for key in ("a", "b"):
        assert scenario[key]["id"] not in str(body)


def test_thresholds_and_timezone_come_from_configuration(monkeypatch, api_factory):
    monkeypatch.setenv("INTEL_STALL_DAYS_BY_STAGE", '{"proposal": 30}')
    monkeypatch.setenv("INTEL_MEANINGFUL_CHANNELS", '["meeting"]')
    monkeypatch.setenv("INTEL_TIMEZONE", "Asia/Kolkata")
    monkeypatch.setenv("INTEL_DUE_SOON_DAYS", "1")
    with api_factory() as api:
        c = api.customer()
        d = api.deal(c["id"], owner_name="A", value_minor=1, currency="USD",
                     expected_close_date=(NOW + timedelta(days=60)).date().isoformat())
        api.stakeholder(c["id"])
        api.interaction(c["id"], deal_id=d["id"], occurred_at=ts(12), channel="meeting")
        api.interaction(c["id"], deal_id=d["id"], occurred_at=ts(1), channel="call")  # not meaningful now
        as_of = datetime(NOW.year, NOW.month, NOW.day, 20, 0, tzinfo=UTC)  # 01:30 next day in IST
        api.commitment(c["id"], d["id"], due_date=as_of.date().isoformat())
        body = api.c.get(f"/api/customers/{c['id']}/intelligence/signals", params={"as_of": as_of.isoformat()}).json()
        assert body["timezone"] == "Asia/Kolkata"
        assert body["business_date"] == (as_of.date() + timedelta(days=1)).isoformat()
        assert [s["type"] for s in body["items"]] == ["commitment_overdue"]  # 12 days < 30; due date passed in IST
        rules = api.c.get("/api/intelligence/rules").json()["thresholds"]
        assert rules["stall_days_by_stage"]["proposal"] == 30 and rules["meaningful_channels"] == ["meeting"]


def test_invalid_intelligence_configuration_fails_at_startup(monkeypatch):
    from app.main import create_app

    monkeypatch.setenv("INTEL_TIMEZONE", "Not/AZone")
    get_settings.cache_clear()
    with pytest.raises(Exception, match="INTEL_TIMEZONE"):
        with TestClient(create_app()):
            pass


def test_works_when_hindsight_is_configured_but_unreachable(monkeypatch, api_factory):
    monkeypatch.setenv("MEMORY_BACKEND", "hindsight")
    monkeypatch.setenv("HINDSIGHT_BASE_URL", "http://127.0.0.1:9")
    monkeypatch.setenv("MEMORY_MAX_ATTEMPTS", "1")
    monkeypatch.setenv("HINDSIGHT_TIMEOUT_SECONDS", "5")
    with api_factory() as api:
        c = api.customer()
        d = api.deal(c["id"])
        api.interaction(c["id"], deal_id=d["id"], occurred_at=ts(30))
        body = api.c.get(f"/api/customers/{c['id']}/intelligence/signals").json()
        assert "stalled_deal" in {s["type"] for s in body["items"]}


@pytest.fixture
def api_factory():
    from contextlib import contextmanager

    from app.main import create_app

    @contextmanager
    def make():
        get_settings.cache_clear()
        with TestClient(create_app()) as client:
            yield Api(client)

    return make
