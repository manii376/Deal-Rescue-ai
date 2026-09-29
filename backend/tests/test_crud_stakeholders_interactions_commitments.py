from datetime import UTC, datetime, timedelta

from tests.conftest import iso


def test_stakeholder_crud_and_validation(client, api):
    c = api.customer()
    s = api.stakeholder(c["id"], influence="high", priorities=["security", "cost"], email="priya@example.com")
    base = f"/api/customers/{c['id']}/stakeholders"
    assert client.get(f"{base}/{s['id']}").json()["priorities"] == ["security", "cost"]
    assert client.get(f"{base}?influence=high").json()["total"] == 1
    assert client.post(base, json={"name": "X", "email": "not-an-email"}).status_code == 422
    assert client.post(base, json={"name": "X", "influence": "extreme"}).status_code == 422
    r = client.patch(f"{base}/{s['id']}", json={"role": "Chief Financial Officer"})
    assert r.json()["role"] == "Chief Financial Officer"
    assert client.delete(f"{base}/{s['id']}").status_code == 204


def test_interaction_crud_with_participants(client, api):
    c = api.customer()
    d = api.deal(c["id"])
    s1, s2 = api.stakeholder(c["id"]), api.stakeholder(c["id"], "Marcus Lee")
    i = api.interaction(c["id"], deal_id=d["id"], participant_ids=[s1["id"], s2["id"], s1["id"]])
    assert sorted(i["participant_ids"]) == sorted([s1["id"], s2["id"]])
    assert i["memory"]["status"] == "disabled"

    base = f"/api/customers/{c['id']}/interactions"
    r = client.patch(f"{base}/{i['id']}", json={"participant_ids": [s2["id"]], "title": "Budget call"})
    assert r.status_code == 200 and r.json()["participant_ids"] == [s2["id"]]

    # a stakeholder who participated cannot be deleted (history is preserved)
    assert client.delete(f"/api/customers/{c['id']}/stakeholders/{s2['id']}").status_code == 409
    assert client.delete(f"{base}/{i['id']}").status_code == 204
    assert client.get(f"{base}/{i['id']}").status_code == 404
    assert client.delete(f"/api/customers/{c['id']}/stakeholders/{s2['id']}").status_code == 204


def test_interaction_validation(client, api):
    c = api.customer()
    base = f"/api/customers/{c['id']}/interactions"
    ok = {"occurred_at": iso(1), "channel": "call", "notes": "n"}
    assert client.post(base, json={**ok, "occurred_at": "2026-09-01T10:00:00"}).status_code == 422  # naive
    future = (datetime.now(UTC) + timedelta(days=3)).isoformat()
    assert client.post(base, json={**ok, "occurred_at": future}).status_code == 422
    assert client.post(base, json={**ok, "channel": "fax"}).status_code == 422
    assert client.post(base, json={**ok, "notes": ""}).status_code == 422
    assert client.post(base, json={**ok, "customer_id": "cus_other"}).status_code == 422


def test_interaction_filters_and_order(client, api):
    c = api.customer()
    d = api.deal(c["id"])
    api.interaction(c["id"], notes="old", occurred_at=iso(10))
    api.interaction(c["id"], notes="new", occurred_at=iso(1), deal_id=d["id"], channel="email")
    base = f"/api/customers/{c['id']}/interactions"
    items = client.get(base).json()["items"]
    assert [i["notes"] for i in items] == ["new", "old"]
    assert client.get(f"{base}?deal_id={d['id']}").json()["total"] == 1
    assert client.get(f"{base}?channel=email").json()["total"] == 1
    since = (datetime.now(UTC) - timedelta(days=5)).isoformat().replace("+00:00", "Z")
    assert client.get(base, params={"occurred_from": since}).json()["total"] == 1


def test_interaction_idempotency_key(client, api):
    c = api.customer()
    base = f"/api/customers/{c['id']}/interactions"
    body = {"occurred_at": iso(1), "channel": "call", "notes": "Budget capped"}
    first = client.post(base, json=body, headers={"Idempotency-Key": "retry-key-0001"})
    again = client.post(base, json=body, headers={"Idempotency-Key": "retry-key-0001"})
    assert first.status_code == 201 and again.status_code == 200
    assert again.json()["id"] == first.json()["id"]
    assert again.headers["Idempotent-Replayed"] == "true"
    assert client.get(base).json()["total"] == 1
    other = client.post(base, json={**body, "notes": "different"}, headers={"Idempotency-Key": "retry-key-0001"})
    assert other.status_code == 409


def test_commitment_crud_status_and_overdue(client, api):
    c = api.customer()
    d = api.deal(c["id"])
    i = api.interaction(c["id"], deal_id=d["id"])
    yesterday = (datetime.now(UTC) - timedelta(days=1)).date().isoformat()
    tomorrow = (datetime.now(UTC) + timedelta(days=1)).date().isoformat()
    late = api.commitment(c["id"], d["id"], due_date=yesterday, source_interaction_id=i["id"])
    api.commitment(c["id"], d["id"], due_date=tomorrow, owner_party="customer")
    api.commitment(c["id"], d["id"])

    base = f"/api/customers/{c['id']}/commitments"
    assert [x["id"] for x in client.get(f"{base}?overdue=true").json()["items"]] == [late["id"]]
    assert client.get(f"{base}?overdue=false").json()["total"] == 2
    assert client.get(f"{base}?owner_party=customer").json()["total"] == 1

    done = client.patch(f"{base}/{late['id']}", json={"status": "done"}).json()
    assert done["completed_at"] is not None
    assert client.get(f"{base}?overdue=true").json()["total"] == 0
    reopened = client.patch(f"{base}/{late['id']}", json={"status": "open"}).json()
    assert reopened["completed_at"] is None

    # an interaction that is the source of a commitment cannot be deleted
    assert client.delete(f"/api/customers/{c['id']}/interactions/{i['id']}").status_code == 409
    assert client.delete(f"{base}/{late['id']}").status_code == 204


def test_commitment_validation(client, api):
    c = api.customer()
    d = api.deal(c["id"])
    base = f"/api/customers/{c['id']}/commitments"
    assert client.post(base, json={"deal_id": d["id"], "description": "x", "owner_party": "them"}).status_code == 422
    assert client.post(base, json={"description": "x", "owner_party": "us"}).status_code == 422
    r = client.post(base, json={"deal_id": "deal_missing", "description": "x", "owner_party": "us"})
    assert r.status_code == 422 and r.json()["error"]["code"] == "invalid_reference"


def test_commitment_source_interaction_must_match_deal(client, api):
    c = api.customer()
    d1, d2 = api.deal(c["id"], "one"), api.deal(c["id"], "two")
    i = api.interaction(c["id"], deal_id=d1["id"])
    r = client.post(f"/api/customers/{c['id']}/commitments",
                    json={"deal_id": d2["id"], "description": "x", "owner_party": "us",
                          "source_interaction_id": i["id"]})
    assert r.status_code == 422 and r.json()["error"]["code"] == "invalid_reference"
