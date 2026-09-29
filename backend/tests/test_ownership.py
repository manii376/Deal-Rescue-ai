"""Cross-customer access attempts must fail, through the API and at the database level."""

from datetime import UTC, datetime

import pytest
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session

from app.domain.models import Commitment, Interaction, InteractionParticipant


@pytest.fixture
def two_customers(api):
    a, b = api.customer("Aurora"), api.customer("Borealis")
    a_deal, b_deal = api.deal(a["id"]), api.deal(b["id"])
    a_stk, b_stk = api.stakeholder(a["id"]), api.stakeholder(b["id"], "Marcus Lee")
    a_int = api.interaction(a["id"], deal_id=a_deal["id"])
    b_int = api.interaction(b["id"], deal_id=b_deal["id"])
    a_com = api.commitment(a["id"], a_deal["id"])
    return {"a": a, "b": b, "a_deal": a_deal, "b_deal": b_deal, "a_stk": a_stk, "b_stk": b_stk,
            "a_int": a_int, "b_int": b_int, "a_com": a_com}


@pytest.mark.parametrize("collection,key", [
    ("deals", "a_deal"), ("stakeholders", "a_stk"), ("interactions", "a_int"), ("commitments", "a_com"),
])
def test_other_customers_records_are_not_found_via_path(client, two_customers, collection, key):
    b_id, record_id = two_customers["b"]["id"], two_customers[key]["id"]
    url = f"/api/customers/{b_id}/{collection}/{record_id}"
    for method in ("get", "patch", "delete"):
        kwargs = {"json": {}} if method == "patch" else {}
        r = getattr(client, method)(url, **kwargs)
        assert r.status_code == 404, (method, r.text)
        assert r.json()["error"]["code"] == "not_found"
    # still intact for its owner
    assert client.get(f"/api/customers/{two_customers['a']['id']}/{collection}/{record_id}").status_code == 200


def test_listing_never_includes_other_customers_records(client, two_customers):
    b_id = two_customers["b"]["id"]
    for collection in ("deals", "stakeholders", "interactions", "commitments"):
        items = client.get(f"/api/customers/{b_id}/{collection}").json()["items"]
        assert all(i["customer_id"] == b_id for i in items), collection
    # filtering B's list by A's deal id returns nothing
    assert client.get(f"/api/customers/{b_id}/interactions?deal_id={two_customers['a_deal']['id']}").json()["total"] == 0


def test_body_references_to_other_customer_are_rejected(client, two_customers):
    a_id = two_customers["a"]["id"]
    base = f"/api/customers/{a_id}"
    cases = [
        (f"{base}/interactions", {"occurred_at": two_customers["a_int"]["occurred_at"], "channel": "call",
                                  "notes": "x", "deal_id": two_customers["b_deal"]["id"]}, "deal_id"),
        (f"{base}/interactions", {"occurred_at": two_customers["a_int"]["occurred_at"], "channel": "call",
                                  "notes": "x", "participant_ids": [two_customers["b_stk"]["id"]]}, "participant_ids"),
        (f"{base}/commitments", {"deal_id": two_customers["b_deal"]["id"], "description": "x",
                                 "owner_party": "us"}, "deal_id"),
        (f"{base}/commitments", {"deal_id": two_customers["a_deal"]["id"], "description": "x", "owner_party": "us",
                                 "source_interaction_id": two_customers["b_int"]["id"]}, "source_interaction_id"),
        (f"{base}/memory/recall", {"query": "x", "deal_id": two_customers["b_deal"]["id"]}, "deal_id"),
    ]
    for url, body, field in cases:
        r = client.post(url, json=body)
        assert r.status_code == 422, (url, field, r.text)
        err = r.json()["error"]
        assert err["code"] == "invalid_reference" and err["details"] == [{"field": field}]
        # same message as a non-existent id: no information about other customers leaks
        assert "does not refer to a record of this customer" in err["message"]


def test_patch_cannot_move_interaction_to_other_customers_deal(client, two_customers):
    a_id, a_int = two_customers["a"]["id"], two_customers["a_int"]["id"]
    r = client.patch(f"/api/customers/{a_id}/interactions/{a_int}",
                     json={"deal_id": two_customers["b_deal"]["id"]})
    assert r.status_code == 422 and r.json()["error"]["code"] == "invalid_reference"


def test_database_rejects_cross_customer_rows_even_if_api_is_bypassed(client, two_customers):
    engine = client.app.state.engine
    a_id = two_customers["a"]["id"]
    bad_rows = [
        Interaction(customer_id=a_id, deal_id=two_customers["b_deal"]["id"],
                    occurred_at=datetime.now(UTC),
                    channel="call", notes="x"),
        Commitment(customer_id=a_id, deal_id=two_customers["b_deal"]["id"], description="x", owner_party="us"),
        InteractionParticipant(interaction_id=two_customers["a_int"]["id"],
                               stakeholder_id=two_customers["b_stk"]["id"], customer_id=a_id),
    ]
    for row in bad_rows:
        with Session(engine) as session:
            session.add(row)
            with pytest.raises(IntegrityError):
                session.commit()
