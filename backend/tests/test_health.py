from collections.abc import Iterator
from typing import Any
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient
from redis.exceptions import ConnectionError as RedisConnectionError
from sqlalchemy.exc import OperationalError

from app.db import get_db
from app.main import create_app
from app.redis_client import get_redis


@pytest.fixture
def db() -> MagicMock:
    return MagicMock()


@pytest.fixture
def redis() -> MagicMock:
    return MagicMock()


@pytest.fixture(autouse=True)
def fresh_worker_cache(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("app.api.health._workers_cache", None)


@pytest.fixture
def client(db: MagicMock, redis: MagicMock) -> Iterator[TestClient]:
    app = create_app()
    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[get_redis] = lambda: redis
    with TestClient(app) as c:
        yield c


def test_health_ok(client: TestClient) -> None:
    resp = client.get("/api/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok", "database": "ok", "redis": "ok"}


def test_health_reports_database_failure(client: TestClient, db: MagicMock) -> None:
    db.execute.side_effect = OperationalError("SELECT 1", {}, Exception("down"))
    resp = client.get("/api/health")
    assert resp.status_code == 503
    assert resp.json()["database"] == "fail"
    assert resp.json()["redis"] == "ok"


def test_health_reports_redis_failure(client: TestClient, redis: MagicMock) -> None:
    redis.ping.side_effect = RedisConnectionError("down")
    resp = client.get("/api/health")
    assert resp.status_code == 503
    assert resp.json()["redis"] == "fail"


def test_workers_counts_queues(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    replies: dict[str, list[dict[str, Any]]] = {
        "ai@host": [{"name": "ai"}],
        "render@host": [{"name": "render"}],
    }
    inspector = MagicMock()
    inspector.active_queues.return_value = replies
    monkeypatch.setattr("app.api.health.celery_app.control.inspect", lambda timeout: inspector)

    resp = client.get("/api/health/workers")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok", "queues": {"ai": 1, "render": 1}}


def test_workers_missing_render_worker(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    inspector = MagicMock()
    inspector.active_queues.return_value = {"ai@host": [{"name": "ai"}]}
    monkeypatch.setattr("app.api.health.celery_app.control.inspect", lambda timeout: inspector)

    resp = client.get("/api/health/workers")
    assert resp.status_code == 503
    assert resp.json()["queues"]["render"] == 0


def test_workers_answer_is_cached(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    inspector = MagicMock()
    inspector.active_queues.return_value = {"ai@host": [{"name": "ai"}, {"name": "render"}]}
    calls = []

    def inspect(timeout: float) -> MagicMock:
        calls.append(timeout)
        return inspector

    monkeypatch.setattr("app.api.health.celery_app.control.inspect", inspect)
    for _ in range(5):
        assert client.get("/api/health/workers").status_code == 200
    assert len(calls) == 1


def test_unhandled_error_does_not_leak_details() -> None:
    app = create_app()

    @app.get("/api/boom")
    def boom() -> None:
        raise RuntimeError("secret internal detail")

    with TestClient(app, raise_server_exceptions=False) as c:
        resp = c.get("/api/boom")
    assert resp.status_code == 500
    assert "secret" not in resp.text
