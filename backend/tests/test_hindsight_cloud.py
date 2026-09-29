"""Hindsight Cloud configuration and error handling, against a local fake Cloud server.

No request leaves the machine. The fake key below is not a real credential.
"""

import logging
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.config import Settings, get_settings
from tests.conftest import Api
from tests.fake_cloud import FakeCloud

FAKE_KEY = "hsk_test_fake_key_not_real_0123456789"


@pytest.fixture
def cloud():
    server = FakeCloud().start()
    yield server
    server.stop()


def _cloud_env(monkeypatch, url: str, **extra):
    monkeypatch.setenv("MEMORY_BACKEND", "hindsight")
    monkeypatch.setenv("HINDSIGHT_DEPLOYMENT", "cloud")
    monkeypatch.setenv("HINDSIGHT_CLOUD_BASE_URL", url)
    monkeypatch.setenv("HINDSIGHT_API_KEY", FAKE_KEY)
    monkeypatch.setenv("MEMORY_MAX_ATTEMPTS", "2")
    monkeypatch.setenv("HINDSIGHT_TIMEOUT_SECONDS", "5")
    for key, value in extra.items():
        monkeypatch.setenv(key, value)
    get_settings.cache_clear()


def _app():
    from app.main import create_app

    return TestClient(create_app())


def _ledger(api: Api, cid: str, iid: str) -> dict:
    return api.c.get(f"/api/customers/{cid}/memory/writes", params={"source_id": iid}).json()["items"][0]


# -- configuration ------------------------------------------------------------------------------


def test_default_is_self_hosted_and_never_sends_the_cloud_key():
    s = Settings(_env_file=None, hindsight_api_key=FAKE_KEY)
    assert s.hindsight_deployment == "self_hosted"
    assert s.hindsight_effective_base_url == "http://127.0.0.1:8888"
    assert s.hindsight_client_key() is None  # the Cloud key is not for the local server


def test_cloud_uses_documented_endpoint_and_bearer_key():
    s = Settings(_env_file=None, hindsight_deployment="cloud", hindsight_api_key=FAKE_KEY)
    assert s.hindsight_effective_base_url == "https://api.hindsight.vectorize.io"
    assert s.hindsight_client_key() == FAKE_KEY
    assert FAKE_KEY not in repr(s) and FAKE_KEY not in str(s.describe())
    assert s.describe()["hindsight_deployment"] == "cloud"


@pytest.mark.parametrize("bad", [
    {"hindsight_cloud_base_url": "http://api.hindsight.vectorize.io"},   # key would travel in clear text
    {"hindsight_cloud_base_url": "not-a-url"},
    {"hindsight_extraction_mode": "chunks"},                              # undocumented for Cloud
])
def test_unsafe_cloud_configuration_is_rejected(bad):
    with pytest.raises(ValidationError):
        Settings(_env_file=None, hindsight_deployment="cloud", hindsight_api_key=FAKE_KEY, **bad)


def test_self_hosted_optional_key_is_separate():
    s = Settings(_env_file=None, hindsight_api_key=FAKE_KEY, hindsight_self_hosted_api_key="local-secret")
    assert s.hindsight_client_key() == "local-secret"


# -- missing credentials --------------------------------------------------------------------------


def test_cloud_without_key_starts_disabled_and_crud_still_works(monkeypatch, cloud):
    _cloud_env(monkeypatch, cloud.url, HINDSIGHT_API_KEY="")
    with _app() as client:
        api = Api(client)
        memory = client.get("/api/system/memory").json()
        assert memory["backend"] == "disabled" and memory["available"] is False
        assert "HINDSIGHT_API_KEY is not set" in memory["reason"]
        c = api.customer()
        i = api.interaction(c["id"], deal_id=api.deal(c["id"])["id"])
        assert i["memory"]["status"] == "disabled"
        r = client.post(f"/api/customers/{c['id']}/memory/recall", json={"query": "x"})
        assert r.status_code == 503
    assert cloud.v1_requests() == []  # nothing was sent anywhere


# -- authentication headers --------------------------------------------------------------------------


def test_every_cloud_request_carries_the_bearer_key(monkeypatch, cloud):
    cloud.expected_key = FAKE_KEY
    _cloud_env(monkeypatch, cloud.url)
    with _app() as client:
        api = Api(client)
        assert client.get("/api/system/memory").json()["available"] is True
        c = api.customer()
        i = api.interaction(c["id"], notes="SYNTHETIC budget 42k")
        assert _ledger(api, c["id"], i["id"])["status"] == "stored"
        hits = client.post(f"/api/customers/{c['id']}/memory/recall", json={"query": "budget"}).json()["hits"]
        assert hits[0]["provenance"] == "linked" and hits[0]["source"]["source_id"] == i["id"]
    v1 = cloud.v1_requests()
    assert {r.path.split("/")[4] if len(r.path.split("/")) > 4 else "banks" for r in v1}  # something was called
    assert all(r.authorization == f"Bearer {FAKE_KEY}" for r in v1), [(r.path, r.authorization) for r in v1]
    paths = {(r.method, "/".join(r.path.split("/")[5:])) for r in v1}
    assert ("PUT", "") in paths and ("POST", "memories") in paths and ("POST", "memories/recall") in paths


def test_self_hosted_mode_sends_no_authorization_header(monkeypatch, cloud):
    monkeypatch.setenv("MEMORY_BACKEND", "hindsight")
    monkeypatch.setenv("HINDSIGHT_BASE_URL", cloud.url)  # the fake acting as a self-hosted server
    monkeypatch.setenv("HINDSIGHT_API_KEY", FAKE_KEY)    # present in .env, must NOT be sent
    get_settings.cache_clear()
    with _app() as client:
        api = Api(client)
        c = api.customer()
        api.interaction(c["id"])
    assert cloud.v1_requests() and all(r.authorization is None for r in cloud.v1_requests())


# -- documented Cloud errors ------------------------------------------------------------------------------


@pytest.mark.parametrize("status,kind,text", [
    (401, "auth_failed", "authentication failed"),
    (402, "no_credits", "credits exhausted"),
    (403, "forbidden", "not permitted"),
])
def test_auth_credit_and_permission_errors_are_not_retried(monkeypatch, cloud, caplog, status, kind, text):
    cloud.fail_status = status
    _cloud_env(monkeypatch, cloud.url)
    caplog.set_level(logging.DEBUG)
    with _app() as client:
        api = Api(client)
        status_body = client.get("/api/system/memory").json()
        assert status_body["available"] is False and text in status_body["reason"]
        before = len(cloud.v1_requests())
        c = api.customer()
        i = api.interaction(c["id"])
        entry = _ledger(api, c["id"], i["id"])
        assert entry["status"] == "failed" and entry["last_error_kind"] == kind
        assert text in entry["last_error"] and entry["auto_retry_scheduled"] is False
        assert len(cloud.v1_requests()) - before == 1  # one request (bank creation), no retries

        # the durable worker does not retry it either, even much later
        worker = client.app.state.memory_worker
        worker.clock = lambda: datetime.now(UTC) + timedelta(days=2)
        client.portal.call(worker.run_once)
        assert len(cloud.v1_requests()) - before == 1

        # after the problem is fixed, an explicit retry succeeds
        cloud.fail_status = None
        r = client.post(f"/api/customers/{c['id']}/memory/writes/{entry['id']}/retry")
        assert r.status_code == 202 and _ledger(api, c["id"], i["id"])["status"] == "stored"
        body = client.get(f"/api/customers/{c['id']}/memory/writes/{entry['id']}").json()
        assert FAKE_KEY not in str(body) and FAKE_KEY not in str(status_body)
    assert FAKE_KEY not in caplog.text


def test_server_errors_are_retried_with_a_bound_then_scheduled(monkeypatch, cloud):
    cloud.fail_status = 503
    _cloud_env(monkeypatch, cloud.url)  # MEMORY_MAX_ATTEMPTS=2 quick retries
    with _app() as client:
        api = Api(client)
        c = api.customer()
        before = len(cloud.v1_requests())
        i = api.interaction(c["id"])
        entry = _ledger(api, c["id"], i["id"])
        assert entry["status"] == "failed" and entry["last_error_kind"] == "unavailable"
        assert entry["auto_retry_scheduled"] is True
        assert len(cloud.v1_requests()) - before == 2  # bounded: exactly MEMORY_MAX_ATTEMPTS calls


def test_timeouts_are_reported_as_unavailable(monkeypatch, cloud):
    cloud.delay_seconds = 3
    _cloud_env(monkeypatch, cloud.url, HINDSIGHT_TIMEOUT_SECONDS="0.5", MEMORY_MAX_ATTEMPTS="1")
    with _app() as client:
        api = Api(client)
        status_body = client.get("/api/system/memory").json()
        assert status_body["available"] is False and "not reachable" in status_body["reason"]
        c = api.customer()
        i = api.interaction(c["id"])
        entry = _ledger(api, c["id"], i["id"])
        assert entry["last_error_kind"] == "unavailable" and "cannot reach" in entry["last_error"]
        assert client.get(f"/api/customers/{c['id']}").status_code == 200  # CRUD unaffected


def test_response_bodies_and_keys_never_reach_the_ledger(monkeypatch, cloud):
    cloud.fail_status = 401
    cloud.fail_count = 1
    _cloud_env(monkeypatch, cloud.url)
    with _app() as client:
        api = Api(client)
        c = api.customer()
        i = api.interaction(c["id"])
        entry = _ledger(api, c["id"], i["id"])
        assert "Invalid API key" not in entry["last_error"] and "fake failure" not in entry["last_error"]
        assert FAKE_KEY not in entry["last_error"]


def test_redaction_covers_cloud_keys():
    from app.services.hindsight_memory import _redact

    text = f"error: {FAKE_KEY} and hsk_another_key_value and Bearer abc.def"
    redacted = _redact(text, [])
    assert "hsk_" not in redacted and "abc.def" not in redacted


def test_customer_banks_are_separate_on_cloud(monkeypatch, cloud):
    _cloud_env(monkeypatch, cloud.url)
    with _app() as client:
        api = Api(client)
        a, b = api.customer("Aurora"), api.customer("Borealis")
        api.interaction(a["id"], notes="Aurora SOC 2")
        api.interaction(b["id"], notes="Borealis on-prem")
        assert set(cloud.banks) == {f"deal-rescue-cust-{a['id']}", f"deal-rescue-cust-{b['id']}"}
        hits = client.post(f"/api/customers/{b['id']}/memory/recall", json={"query": "Aurora"}).json()["hits"]
        assert hits and all("Aurora" not in h["text"] for h in hits)


def test_low_level_calls_honour_the_timeout():
    """Regression: generated low-level APIs ignored the client timeout before M4.5."""
    import asyncio
    import time

    from app.services.hindsight_memory import HindsightMemory, MemoryUnavailableError

    server = FakeCloud(delay_seconds=3).start()
    try:
        async def run():
            mem = HindsightMemory(server.url, "timeout-bank", api_key=FAKE_KEY, timeout_seconds=0.5)
            try:
                for call in (mem.probe(), mem.list_memories(document_id="d"), mem.get_memory("m"),
                             mem.delete_document("d")):
                    started = time.monotonic()
                    with pytest.raises(MemoryUnavailableError):
                        await call
                    assert time.monotonic() - started < 2.5
            finally:
                await mem.aclose()

        asyncio.run(run())
    finally:
        server.stop()
