from fastapi.testclient import TestClient

from app.main import app
from tests.conftest import UNREACHABLE_URL


def test_health_returns_200(monkeypatch):
    monkeypatch.setenv("HINDSIGHT_BASE_URL", UNREACHABLE_URL)
    with TestClient(app) as client:
        response = client.get("/api/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_dependency_health_reports_unreachable_hindsight(monkeypatch):
    monkeypatch.setenv("HINDSIGHT_BASE_URL", UNREACHABLE_URL)
    with TestClient(app) as client:
        response = client.get("/api/health/dependencies")
    assert response.status_code == 200
    body = response.json()
    assert body["hindsight"]["reachable"] is False
    assert body["hindsight"]["healthy"] is False


def test_dependency_health_never_leaks_keys(monkeypatch):
    key = "sk-ant-test-not-a-real-key-456"
    monkeypatch.setenv("HINDSIGHT_BASE_URL", UNREACHABLE_URL)
    monkeypatch.setenv("ANTHROPIC_API_KEY", key)
    monkeypatch.setenv("HINDSIGHT_API_KEY", key)
    with TestClient(app) as client:
        response = client.get("/api/health/dependencies")
    assert key not in response.text
    assert response.json()["anthropic"]["api_key"] == "set"
