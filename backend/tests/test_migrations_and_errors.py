import pytest
from fastapi.testclient import TestClient
from sqlalchemy import inspect, text

from app.db import migrations
from app.db.engine import create_db_engine
from app.db.migrations import SchemaError, migrate


def _engine(tmp_path, name="m.db"):
    path = tmp_path / name
    return create_db_engine(f"sqlite:///{path.as_posix()}"), path


def _version(engine):
    with engine.connect() as conn:
        return conn.execute(text("SELECT MAX(version) FROM schema_migrations")).scalar()


def test_fresh_database_is_created_and_stamped(tmp_path):
    engine, path = _engine(tmp_path)
    assert migrate(engine, path) == migrations.LATEST_VERSION
    assert _version(engine) == migrations.LATEST_VERSION
    assert {"customers", "deals", "interactions", "memory_writes"} <= set(inspect(engine).get_table_names())
    assert migrate(engine, path) == migrations.LATEST_VERSION  # idempotent
    with engine.connect() as conn:
        assert conn.execute(text("SELECT COUNT(*) FROM schema_migrations")).scalar() == 1


def test_database_newer_than_code_is_refused(tmp_path):
    engine, path = _engine(tmp_path)
    migrate(engine, path)
    with engine.begin() as conn:
        conn.execute(text("INSERT INTO schema_migrations VALUES (99, 'x')"))
    with pytest.raises(SchemaError, match="newer"):
        migrate(engine, path)


def test_unversioned_database_with_domain_tables_is_refused(tmp_path):
    engine, path = _engine(tmp_path)
    with engine.begin() as conn:
        conn.execute(text("CREATE TABLE customers (id TEXT PRIMARY KEY)"))
    with pytest.raises(SchemaError, match="no schema_migrations"):
        migrate(engine, path)


def test_upgrade_backs_up_file_runs_migration_and_keeps_data(tmp_path, monkeypatch):
    engine, path = _engine(tmp_path)
    migrate(engine, path)
    with engine.begin() as conn:
        conn.execute(text("INSERT INTO customers (id, name, is_synthetic, created_at, updated_at) "
                          "VALUES ('cus_1', 'Aurora', 1, '2026-09-28 00:00:00', '2026-09-28 00:00:00')"))

    current = migrations.LATEST_VERSION
    nxt = current + 1  # a hypothetical next migration, whatever the current version is

    def add_website(conn):
        conn.exec_driver_sql("ALTER TABLE customers ADD COLUMN website VARCHAR(200)")

    monkeypatch.setattr(migrations, "LATEST_VERSION", nxt)
    monkeypatch.setitem(migrations.MIGRATIONS, nxt, add_website)
    assert migrate(engine, path) == nxt
    assert _version(engine) == nxt
    assert "website" in {c["name"] for c in inspect(engine).get_columns("customers")}
    with engine.connect() as conn:
        assert conn.execute(text("SELECT name FROM customers WHERE id='cus_1'")).scalar() == "Aurora"
    assert list(tmp_path.glob(f"m.db.bak-v{current}-*")), "backup file was not created"


def test_error_envelope_for_framework_errors(client):
    r = client.get("/api/does-not-exist")
    assert r.status_code == 404 and r.json()["error"]["code"] == "not_found"
    r = client.put("/api/customers")
    assert r.status_code == 405 and r.json()["error"]["code"] == "method_not_allowed"
    r = client.get("/api/customers?limit=0")
    assert r.status_code == 422 and r.json()["error"]["code"] == "validation_error"
    assert r.json()["error"]["details"][0]["loc"] == ["query", "limit"]


def test_validation_errors_do_not_echo_submitted_content(client, api):
    c = api.customer()
    secret_note = "confidential negotiation position " * 3
    r = client.post(f"/api/customers/{c['id']}/interactions",
                    json={"occurred_at": "not-a-date", "channel": "call", "notes": secret_note})
    assert r.status_code == 422
    assert "confidential" not in r.text and "input" not in r.json()["error"]["details"][0]


def test_unhandled_errors_return_generic_500():
    from app.main import create_app

    app = create_app()

    @app.get("/api/boom")
    def boom():
        raise RuntimeError("internal detail with customer data")

    with TestClient(app, raise_server_exceptions=False) as c:
        r = c.get("/api/boom")
    assert r.status_code == 500
    assert r.json() == {"error": {"code": "internal_error", "message": "Internal server error", "details": None}}
