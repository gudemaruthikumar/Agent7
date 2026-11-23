from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import create_engine, event, inspect
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from rmte_spine.config import database_url
from rmte_spine.models import Base

_ENGINE: Engine | None = None
_SESSION: sessionmaker[Session] | None = None


def _sqlite_foreign_keys(dbapi_conn, _connection_record) -> None:
    cursor = dbapi_conn.cursor()
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.close()


def get_engine(url: str | None = None) -> Engine:
    global _ENGINE, _SESSION
    resolved = url or database_url()
    if _ENGINE is not None and str(_ENGINE.url) == resolved:
        return _ENGINE
    kwargs: dict = {"future": True}
    if resolved.startswith("sqlite"):
        kwargs["connect_args"] = {"check_same_thread": False}
    engine = create_engine(resolved, **kwargs)
    if resolved.startswith("sqlite"):
        event.listen(engine, "connect", _sqlite_foreign_keys)
    _ENGINE = engine
    _SESSION = sessionmaker(bind=engine, expire_on_commit=False, future=True)
    return engine


def reset_engine() -> None:
    global _ENGINE, _SESSION
    if _ENGINE is not None:
        _ENGINE.dispose()
    _ENGINE = None
    _SESSION = None


def create_schema(engine: Engine | None = None) -> None:
    eng = engine or get_engine()
    Base.metadata.create_all(eng)


def schema_ready(engine: Engine | None = None) -> bool:
    eng = engine or get_engine()
    return inspect(eng).has_table("library_releases")


@contextmanager
def session_scope(url: str | None = None) -> Iterator[Session]:
    get_engine(url)
    assert _SESSION is not None
    session = _SESSION()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
