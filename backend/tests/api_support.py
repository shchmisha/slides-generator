"""Helpers for API tests (the fixtures live in conftest.py)."""

from __future__ import annotations

import threading
from typing import Any

from fastapi.testclient import TestClient

HEADERS = {"X-Requested-With": "fetch"}
PASSWORD = "correct horse battery"


class FakeCounter:
    """The Redis commands the rate limiter uses."""

    def __init__(self) -> None:
        self.values: dict[str, int] = {}
        self._lock = threading.Lock()  # Redis INCR is atomic; parallel tests rely on that

    def incr(self, name: str) -> int:
        with self._lock:
            self.values[name] = self.values.get(name, 0) + 1
            return self.values[name]

    def decr(self, name: str) -> int:
        with self._lock:
            self.values[name] = self.values.get(name, 0) - 1
            return self.values[name]

    def expire(self, name: str, time: int) -> bool:
        return True

    def delete(self, *names: str) -> int:
        return sum(1 for name in names if self.values.pop(name, None) is not None)


def new_client(app: Any, ip: str = "203.0.113.10") -> TestClient:
    return TestClient(app, headers=HEADERS, client=(ip, 50000))


def signed_up(app: Any, email: str = "anna@example.com") -> TestClient:
    client = new_client(app)
    resp = client.post("/api/auth/register", json={"email": email, "password": PASSWORD})
    assert resp.status_code == 201, resp.text
    return client
