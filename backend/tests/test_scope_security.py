"""Regression tests for the post-M2 security follow-up.

The app has no authentication yet. Path scoping is NOT authorization: it only makes
sure that every response containing customer-owned records is about exactly one,
explicitly named customer, so records of several customers can never be mixed in one
response by accident. These tests pin that property down.
"""

from app.main import create_app

CUSTOMER_SCOPE = "/api/customers/{customer_id}"

# Response schemas that contain customer-owned records or content.
OWNED_SCHEMAS = {
    "CustomerRead", "DealRead", "StakeholderRead", "InteractionRead", "CommitmentRead",
    "MemoryWriteRead", "MemoryRefRead", "EvidenceHitRead", "RecallResponse", "ReflectResponse",
    "MemoryLedgerEntry", "SourceLink",
    "Signal", "SignalList", "DealIntelligence", "DealAttentionList", "DealAttentionSummary",
    "BriefingResponse", "EvidenceSource", "DealBriefing",
}
# Unscoped operations that are allowed to return customer data, and why.
ALLOWED_UNSCOPED = {
    ("post", "/api/customers"),  # returns only the record the caller just created
}


def _refs(node, components, seen):
    """Names of all component schemas reachable from ``node``."""
    if isinstance(node, dict):
        ref = node.get("$ref")
        if ref:
            name = ref.rsplit("/", 1)[-1]
            if name not in seen:
                seen.add(name)
                _refs(components.get(name, {}), components, seen)
        for value in node.values():
            _refs(value, components, seen)
    elif isinstance(node, list):
        for value in node:
            _refs(value, components, seen)
    return seen


def unscoped_owned_operations(spec: dict) -> list[str]:
    """Operations outside /api/customers/{customer_id} whose 2xx responses contain owned schemas."""
    components = spec.get("components", {}).get("schemas", {})
    violations = []
    for path, operations in spec["paths"].items():
        for method, op in operations.items():
            responses = {code: r for code, r in op.get("responses", {}).items() if code.startswith("2")}
            owned = _refs(responses, components, set()) & OWNED_SCHEMAS
            scoped = path == CUSTOMER_SCOPE or path.startswith(CUSTOMER_SCOPE + "/")
            if owned and not scoped and (method, path) not in ALLOWED_UNSCOPED:
                violations.append(f"{method.upper()} {path} -> {sorted(owned)}")
    return violations


def test_no_unscoped_operation_returns_customer_owned_records():
    violations = unscoped_owned_operations(create_app().openapi())
    assert violations == [], "unscoped endpoints return customer-owned data: " + "; ".join(violations)


def test_cross_customer_deal_listing_endpoint_does_not_exist(client, api):
    a, b = api.customer("Aurora"), api.customer("Borealis")
    api.deal(a["id"], "Aurora pilot")
    api.deal(b["id"], "Borealis rollout")
    for url in ("/api/deals", "/api/deals?status=open", "/api/deals?stage=proposal&limit=200",
                f"/api/deals?customer_id={a['id']}"):
        r = client.get(url)
        assert r.status_code == 404, url
        assert r.json()["error"]["code"] == "not_found"
        assert "Aurora" not in r.text and "Borealis" not in r.text
    assert "/api/deals" not in client.app.openapi()["paths"]


def test_customer_scoped_deal_listing_returns_only_that_customers_deals(client, api):
    a, b = api.customer("Aurora"), api.customer("Borealis")
    a_ids = {api.deal(a["id"], f"A{i}")["id"] for i in range(3)}
    b_ids = {api.deal(b["id"], f"B{i}", status="won")["id"] for i in range(2)}

    a_page = client.get(f"/api/customers/{a['id']}/deals?limit=200").json()
    b_page = client.get(f"/api/customers/{b['id']}/deals?limit=200").json()
    assert {d["id"] for d in a_page["items"]} == a_ids and a_page["total"] == 3
    assert {d["id"] for d in b_page["items"]} == b_ids and b_page["total"] == 2
    assert {d["customer_id"] for d in a_page["items"]} == {a["id"]}

    # Filters that match only the other customer's deals return nothing, not them.
    assert client.get(f"/api/customers/{a['id']}/deals?status=won").json() == {
        "items": [], "total": 0, "limit": 50, "offset": 0}
    # Pagination never spills into another customer's records.
    tail = client.get(f"/api/customers/{b['id']}/deals?limit=1&offset=1").json()
    assert tail["total"] == 2 and {d["id"] for d in tail["items"]} <= b_ids
    assert client.get(f"/api/customers/{b['id']}/deals?offset=2").json()["items"] == []


def test_unknown_customer_scope_does_not_fall_back_to_any_records(client, api):
    a = api.customer("Aurora")
    api.deal(a["id"])
    for scope in ("cus_does_not_exist", "%2A", "*", "' OR 1=1 --"):
        r = client.get(f"/api/customers/{scope}/deals")
        assert r.status_code == 404, scope
        assert "items" not in r.json()


def test_customer_directory_does_not_return_private_notes(client, api):
    a = api.customer("Aurora", notes="CONFIDENTIAL: walk-away price USD 38,000")
    api.customer("Borealis", notes="CONFIDENTIAL: competitor is cheaper")
    listing = client.get("/api/customers?limit=200")
    assert listing.status_code == 200 and listing.json()["total"] == 2
    assert "CONFIDENTIAL" not in listing.text
    assert all("notes" not in item for item in listing.json()["items"])
    # The owning record still exposes its notes through its own scoped endpoint.
    detail = client.get(f"/api/customers/{a['id']}").json()
    assert detail["notes"] == "CONFIDENTIAL: walk-away price USD 38,000"


def test_openapi_lists_customer_directory_with_summary_schema():
    spec = create_app().openapi()
    schema = spec["paths"]["/api/customers"]["get"]["responses"]["200"]["content"]["application/json"]["schema"]
    page = spec["components"]["schemas"][schema["$ref"].rsplit("/", 1)[-1]]
    assert page["properties"]["items"]["items"]["$ref"].endswith("/CustomerSummary")
    assert "notes" not in spec["components"]["schemas"]["CustomerSummary"]["properties"]


def test_guard_fails_on_reintroduced_unscoped_listings():
    """The guard must flag both patterns this follow-up removed, if they come back."""
    from fastapi import APIRouter

    from app.api.pagination import Page
    from app.api.schemas import CustomerRead, DealRead

    app = create_app()
    rogue = APIRouter()

    @rogue.get("/api/deals", response_model=Page[DealRead])
    def all_deals():  # pragma: no cover - never called
        raise AssertionError

    @rogue.get("/api/customer-directory", response_model=Page[CustomerRead])
    def directory_with_notes():  # pragma: no cover - never called
        raise AssertionError

    app.include_router(rogue)
    assert unscoped_owned_operations(app.openapi()) == [
        "GET /api/deals -> ['DealRead']",
        "GET /api/customer-directory -> ['CustomerRead']",
    ]
