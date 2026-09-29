from collections.abc import Iterator
from pathlib import Path

from fastapi import Request
from sqlalchemy import Engine, event
from sqlmodel import Session, create_engine


def sqlite_path(database_url: str) -> Path | None:
    """File path of a sqlite:/// URL, or None for in-memory databases."""
    path = database_url.removeprefix("sqlite:///")
    if not path or path == ":memory:":
        return None
    return Path(path)


def create_db_engine(database_url: str) -> Engine:
    path = sqlite_path(database_url)
    if path is not None:
        path.parent.mkdir(parents=True, exist_ok=True)
    engine = create_engine(database_url, connect_args={"check_same_thread": False, "timeout": 10})

    @event.listens_for(engine, "connect")
    def _sqlite_pragmas(dbapi_connection, _record) -> None:
        # SQLite ignores foreign keys unless enabled on every connection.
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA busy_timeout=10000")
        if path is not None:
            cursor.execute("PRAGMA journal_mode=WAL")
        cursor.close()

    return engine


def get_session(request: Request) -> Iterator[Session]:
    with Session(request.app.state.engine) as session:
        yield session
