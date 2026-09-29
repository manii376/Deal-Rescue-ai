def test_customer_crud_lifecycle(client, api):
    c = api.customer(industry="Logistics")
    assert c["id"].startswith("cus_") and c["created_at"].endswith("Z")

    assert client.get(f"/api/customers/{c['id']}").json()["name"] == "Aurora Logistics"

    r = client.patch(f"/api/customers/{c['id']}", json={"industry": "Freight"})
    assert r.status_code == 200 and r.json()["industry"] == "Freight"
    assert r.json()["updated_at"] >= c["updated_at"]

    assert client.delete(f"/api/customers/{c['id']}").status_code == 204
    assert client.get(f"/api/customers/{c['id']}").status_code == 404


def test_customer_list_pagination_and_filters(client, api):
    for name in ["Borealis Foods", "Aurora Logistics", "Cobalt Mining"]:
        api.customer(name)
    client.post("/api/customers", json={"name": "Real Co", "is_synthetic": False})

    page = client.get("/api/customers?limit=2&offset=0").json()
    assert page["total"] == 4 and page["limit"] == 2 and len(page["items"]) == 2
    assert [c["name"] for c in page["items"]] == ["Aurora Logistics", "Borealis Foods"]
    assert client.get("/api/customers?limit=2&offset=2").json()["items"][0]["name"] == "Cobalt Mining"
    assert client.get("/api/customers?q=bore").json()["total"] == 1
    assert client.get("/api/customers?is_synthetic=false").json()["total"] == 1


def test_customer_validation(client):
    assert client.post("/api/customers", json={"name": "   "}).status_code == 422
    assert client.post("/api/customers", json={"name": "x" * 201}).status_code == 422
    assert client.post("/api/customers", json={}).status_code == 422
    # unknown fields (e.g. trying to set the id) are rejected
    assert client.post("/api/customers", json={"name": "A", "id": "cus_mine"}).status_code == 422


def test_customer_patch_cannot_null_required_field(client, api):
    c = api.customer()
    r = client.patch(f"/api/customers/{c['id']}", json={"name": None})
    assert r.status_code == 422


def test_customer_delete_blocked_by_children(client, api):
    c = api.customer()
    api.deal(c["id"])
    r = client.delete(f"/api/customers/{c['id']}")
    assert r.status_code == 409
    assert r.json()["error"]["code"] == "conflict"
    assert {"related": "deals"} in r.json()["error"]["details"]


def test_deal_crud_and_money_validation(client, api):
    c = api.customer()
    d = api.deal(c["id"], value_minor=4_200_000, currency="usd", expected_close_date="2026-12-01")
    assert d["currency"] == "USD" and d["status"] == "open"

    base = f"/api/customers/{c['id']}/deals"
    assert client.post(base, json={"title": "X", "stage": "proposal", "value_minor": 5}).status_code == 422
    assert client.post(base, json={"title": "X", "stage": "proposal", "value_minor": -1,
                                   "currency": "USD"}).status_code == 422
    assert client.post(base, json={"title": "X", "stage": "unknown"}).status_code == 422
    assert client.post(base, json={"title": "X", "stage": "proposal", "currency": "US"}).status_code == 422

    r = client.patch(f"{base}/{d['id']}", json={"stage": "negotiation", "status": "lost"})
    assert r.status_code == 200 and r.json()["stage"] == "negotiation"
    # removing currency but keeping value makes the merged record invalid
    r = client.patch(f"{base}/{d['id']}", json={"currency": None})
    assert r.status_code == 422 and r.json()["error"]["code"] == "validation_error"
    assert client.get(f"{base}/{d['id']}").json()["currency"] == "USD"

    assert client.delete(f"{base}/{d['id']}").status_code == 204
    assert client.get(f"{base}/{d['id']}").status_code == 404


def test_deal_filters_are_customer_scoped(client, api):
    a, b = api.customer("A"), api.customer("B")
    api.deal(a["id"], "a1", status="won")
    api.deal(a["id"], "a2")
    api.deal(b["id"], "b1", stage="closing")
    assert client.get(f"/api/customers/{a['id']}/deals?status=open").json()["total"] == 1
    assert client.get(f"/api/customers/{a['id']}/deals?stage=closing").json()["total"] == 0
    assert client.get(f"/api/customers/{b['id']}/deals?stage=closing").json()["items"][0]["title"] == "b1"
    assert client.get(f"/api/customers/{a['id']}/deals?status=bogus").status_code == 422


def test_deal_delete_blocked_by_interactions(client, api):
    c = api.customer()
    d = api.deal(c["id"])
    api.interaction(c["id"], deal_id=d["id"])
    r = client.delete(f"/api/customers/{c['id']}/deals/{d['id']}")
    assert r.status_code == 409


def test_deal_under_missing_customer(client):
    r = client.post("/api/customers/cus_missing/deals", json={"title": "X", "stage": "proposal"})
    assert r.status_code == 404 and r.json()["error"]["message"] == "Customer not found"
