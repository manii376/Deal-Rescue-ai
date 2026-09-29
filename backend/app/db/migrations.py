"""Versioned schema evolution for the SQLite database (no extra dependency).

Strategy
--------
* ``schema_migrations`` records every applied version.
* Fresh database: ``SQLModel.metadata.create_all`` builds the current schema and the
  database is stamped with ``LATEST_VERSION`` (like ``alembic stamp head``).
* Existing database at version N < LATEST: the file is copied to
  ``<db>.bak-v<N>-<timestamp>`` first, then each ``MIGRATIONS[v]`` for v in N+1..LATEST
  runs in its own transaction and is recorded.
* Database newer than the code, or domain tables without a version: startup refuses to
  continue instead of guessing.

Rules for adding a migration
----------------------------
1. Change the SQLModel table definitions.
2. Increment ``LATEST_VERSION`` and add ``MIGRATIONS[new_version] = fn`` that transforms a
   version N-1 database into the new shape (plain SQL via ``conn.exec_driver_sql``).
3. Prefer additive changes (new table, new nullable column, new index). SQLite cannot
   alter constraints in place; for those, create a new table, copy rows, drop, rename
   inside the migration function.
4. Never delete business data in a migration.
5. Add a test that applies the migration to a database created at the previous version.
"""

from __future__ import annotations

import logging
import shutil
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import Connection, Engine, inspect, text
from sqlmodel import SQLModel

import app.domain.models  # noqa: F401  (registers tables on SQLModel.metadata)

logger = logging.getLogger("deal_rescue.migrations")

LATEST_VERSION = 2


def _v2_durable_memory_sync(conn: Connection) -> None:
    """M3: lease, retry scheduling and tombstone columns on memory_writes (additive only)."""
    for ddl in (
        "ALTER TABLE memory_writes ADD COLUMN claim_token VARCHAR(36)",
        "ALTER TABLE memory_writes ADD COLUMN lease_expires_at DATETIME",
        "ALTER TABLE memory_writes ADD COLUMN next_attempt_at DATETIME",
        "ALTER TABLE memory_writes ADD COLUMN max_attempts INTEGER NOT NULL DEFAULT 5",
        "ALTER TABLE memory_writes ADD COLUMN last_error_kind VARCHAR(16)",
        "ALTER TABLE memory_writes ADD COLUMN source_deleted_at DATETIME",
        "CREATE INDEX ix_memory_writes_status_next_attempt ON memory_writes (status, next_attempt_at)",
    ):
        conn.exec_driver_sql(ddl)
    # Rows interrupted mid-write under v1 had no lease; make them immediately reclaimable.
    conn.exec_driver_sql("UPDATE memory_writes SET status = 'pending' WHERE status = 'in_progress'")


# version -> function upgrading a database at (version - 1) to version.
MIGRATIONS: dict[int, Callable[[Connection], None]] = {2: _v2_durable_memory_sync}

DOMAIN_TABLES = {"customers", "deals", "stakeholders", "interactions", "commitments"}


class SchemaError(RuntimeError):
    pass


def _current_version(conn: Connection) -> int | None:
    conn.exec_driver_sql(
        "CREATE TABLE IF NOT EXISTS schema_migrations ("
        "version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL)"
    )
    return conn.exec_driver_sql("SELECT MAX(version) FROM schema_migrations").scalar()


def _record(conn: Connection, version: int) -> None:
    conn.execute(
        text("INSERT INTO schema_migrations (version, applied_at) VALUES (:v, :t)"),
        {"v": version, "t": datetime.now(UTC).isoformat()},
    )


def migrate(engine: Engine, db_path: Path | None) -> int:
    """Bring the database to LATEST_VERSION. Returns the resulting version."""
    with engine.begin() as conn:
        current = _current_version(conn)
        tables = set(inspect(conn).get_table_names())

    if current is None:
        if tables & DOMAIN_TABLES:
            raise SchemaError(
                "Database has domain tables but no schema_migrations version; refusing to guess. "
                "Back it up and stamp or recreate it."
            )
        SQLModel.metadata.create_all(engine)
        with engine.begin() as conn:
            _record(conn, LATEST_VERSION)
        logger.info("Created database schema at version %s", LATEST_VERSION)
        return LATEST_VERSION

    if current > LATEST_VERSION:
        raise SchemaError(
            f"Database schema version {current} is newer than this code ({LATEST_VERSION})."
        )

    if current < LATEST_VERSION:
        if db_path is not None and db_path.exists():
            stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
            backup = db_path.with_name(f"{db_path.name}.bak-v{current}-{stamp}")
            shutil.copy2(db_path, backup)
            logger.info("Backed up database to %s before migrating", backup.name)
        for version in range(current + 1, LATEST_VERSION + 1):
            upgrade = MIGRATIONS.get(version)
            if upgrade is None:
                raise SchemaError(f"No migration registered for version {version}")
            with engine.begin() as conn:
                upgrade(conn)
                _record(conn, version)
            logger.info("Applied schema migration %s", version)
    return LATEST_VERSION
