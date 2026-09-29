"""The app must start and CRUD must work with no Anthropic key and no usable Hindsight.

Scenario 1: default configuration (memory disabled, AI none).
Scenario 2: MEMORY_BACKEND=hindsight but the server is unreachable (as when Hindsight
cannot start without an LLM key).
"""

from fastapi.testclient import TestClient

from app.config import get_settings
from tests.conftest import UNREACHABLE_URL, Api


def _full_crud_round(api: Api) -> dict:
    c = api.customer()
    d = api.deal(c["id"])
    s = api.stakeholder(c["id"])
    i = api.interaction(c["id"], deal_id=d["id"], participant_ids=[s["id"]])
    api.commitment(c["id"], d["id"], source_interaction_id=i["id"])
    for coll in ("deals", "stakeholders", "interactions", "commitments"):
        assert api.c.get(f"/api/customers/{c['id']}/{coll}").json()["total"] == 1
    return {"customer": c, "interaction": i}


def test_default_config_without_any_keys(client, api):
    assert get_settings().anthropic_api_key is None
    assert client.get("/api/health").status_code == 200
    caps = client.get("/api/system/capabilities").json()
    assert caps["database"]["available"] is True
    assert caps["memory"] == {**caps["memory"], "backend": "disabled", "available": False}
    assert caps["ai"]["available"] is False
    ids = _full_crud_round(api)
    assert ids["interaction"]["memory"]["status"] == "disabled"
    r = client.post(f"/api/customers/{ids['customer']['id']}/memory/reflect", json={"question": "Budget?"})
    assert r.status_code == 503 and r.json()["error"]["code"] == "memory_unavailable"


def test_hindsight_configured_but_unreachable(monkeypatch):
    from app.main import create_app

    monkeypatch.setenv("MEMORY_BACKEND", "hindsight")
    monkeypatch.setenv("HINDSIGHT_BASE_URL", UNREACHABLE_URL)
    monkeypatch.setenv("MEMORY_MAX_ATTEMPTS", "1")
    monkeypatch.setenv("HINDSIGHT_TIMEOUT_SECONDS", "5")
    get_settings.cache_clear()

    with TestClient(create_app()) as client:
        api = Api(client)
        caps = client.get("/api/system/capabilities").json()
        assert caps["memory"]["backend"] == "hindsight" and caps["memory"]["available"] is False
        assert "not reachable" in caps["memory"]["reason"]

        ids = _full_crud_round(api)  # business records still work
        cid, iid = ids["customer"]["id"], ids["interaction"]["id"]
        mem = client.get(f"/api/customers/{cid}/interactions/{iid}/memory").json()
        assert mem["status"] == "failed" and "cannot reach" in mem["last_error"]
        assert client.get(f"/api/customers/{cid}/interactions/{iid}").json()["notes"]

        r = client.post(f"/api/customers/{cid}/memory/recall", json={"query": "budget"})
        assert r.status_code == 503 and r.json()["error"]["code"] == "memory_unavailable"
