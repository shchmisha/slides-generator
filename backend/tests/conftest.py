import os

# Settings are read at import time by the Celery app; give tests harmless values
# before any `app.*` module is imported. Nothing here connects to a real service.
os.environ.setdefault("DATABASE_URL", "postgresql+psycopg://test:test@localhost:1/test")
os.environ.setdefault("REDIS_URL", "redis://localhost:1/0")


# ------------------------------------------------------------------ a real API over SQLite/Postgres
# (imports below the env setup on purpose: app settings are read at import time)


from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.config import get_settings
from app.db import get_db
from app.main import create_app
from app.models import Base
from app.redis_client import get_redis

from .api_support import FakeCounter


def _enable_foreign_keys(dbapi_connection: Any, _: Any) -> None:
    dbapi_connection.execute("PRAGMA foreign_keys=ON")


@pytest.fixture
def engine() -> Iterator[Engine]:
    """SQLite in memory by default; set TEST_DATABASE_URL to run the same tests on Postgres."""
    url = os.environ.get("TEST_DATABASE_URL")
    if url:
        engine = create_engine(url)
    else:
        engine = create_engine(
            "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
        )
        event.listen(engine, "connect", _enable_foreign_keys)
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    yield engine
    Base.metadata.drop_all(engine)
    engine.dispose()


@pytest.fixture
def session_factory(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(bind=engine, expire_on_commit=False)


@pytest.fixture
def data_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr(get_settings(), "data_dir", tmp_path)
    return tmp_path


@pytest.fixture
def counter() -> FakeCounter:
    return FakeCounter()


@pytest.fixture(autouse=True)
def sent(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, list[Any], str]]:
    """Messages "sent" to Celery: no test talks to a real broker."""
    messages: list[tuple[str, list[Any], str]] = []

    def send_task(name: str, args: list[Any], queue: str, **_: Any) -> None:
        messages.append((name, args, queue))

    monkeypatch.setattr("app.jobs.queue.celery_app.send_task", send_task)
    return messages


@pytest.fixture
def app(session_factory: sessionmaker[Session], counter: FakeCounter, data_dir: Path) -> Any:
    application = create_app()

    def db() -> Iterator[Session]:
        session = session_factory()
        try:
            yield session
        finally:
            session.close()

    application.dependency_overrides[get_db] = db
    application.dependency_overrides[get_redis] = lambda: counter
    return application
