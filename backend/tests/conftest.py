from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from app.config import get_settings

# A port nothing listens on, so "unreachable" tests fail fast and never touch
# a real Hindsight instance.
UNREACHABLE_URL = "http://127.0.0.1:9"


@pytest.fixture(autouse=True)
def _isolated_settings(monkeypatch, tmp_path):
    """Every test gets its own SQLite file and a keyless, memory-disabled config,
    independent of the developer's .env."""
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{(tmp_path / 'test.db').as_posix()}")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "")
    monkeypatch.setenv("HINDSIGHT_API_KEY", "")
    monkeypatch.setenv("AI_PROVIDER", "none")
    monkeypatch.setenv("MEMORY_BACKEND", "disabled")
    monkeypatch.setenv("HINDSIGHT_DEPLOYMENT", "self_hosted")  # the developer .env may select cloud
    monkeypatch.setenv("HINDSIGHT_EXTRACTION_MODE", "")
    # The durable worker loop is driven explicitly in tests (worker.run_once) for determinism.
    monkeypatch.setenv("MEMORY_WORKER_ENABLED", "false")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture
def client():
    from app.main import create_app

    with TestClient(create_app()) as c:
        yield c


def iso(days_ago: float = 0) -> str:
    return (datetime.now(UTC) - timedelta(days=days_ago)).isoformat()


class Api:
    """Small helpers so tests read as scenarios."""

    def __init__(self, client: TestClient):
        self.c = client

    def customer(self, name="Aurora Logistics", **extra) -> dict:
        r = self.c.post("/api/customers", json={"name": name, "is_synthetic": True, **extra})
        assert r.status_code == 201, r.text
        return r.json()

    def deal(self, cid, title="Pilot", **extra) -> dict:
        body = {"title": title, "stage": "proposal", **extra}
        r = self.c.post(f"/api/customers/{cid}/deals", json=body)
        assert r.status_code == 201, r.text
        return r.json()

    def stakeholder(self, cid, name="Priya Raman", **extra) -> dict:
        r = self.c.post(f"/api/customers/{cid}/stakeholders", json={"name": name, "role": "CFO", **extra})
        assert r.status_code == 201, r.text
        return r.json()

    def interaction(self, cid, notes="Budget capped at USD 42,000.", **extra) -> dict:
        body = {"occurred_at": iso(1), "channel": "call", "notes": notes, **extra}
        r = self.c.post(f"/api/customers/{cid}/interactions", json=body)
        assert r.status_code == 201, r.text
        return r.json()

    def commitment(self, cid, deal_id, **extra) -> dict:
        body = {"deal_id": deal_id, "description": "Send SOC 2 report", "owner_party": "us", **extra}
        r = self.c.post(f"/api/customers/{cid}/commitments", json=body)
        assert r.status_code == 201, r.text
        return r.json()


@pytest.fixture
def api(client) -> Api:
    return Api(client)
