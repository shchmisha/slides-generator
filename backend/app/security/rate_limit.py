"""Fixed-window rate limits in Redis for the endpoints worth abusing: sign-in, sign-up,
uploads, texts, projects and starting generations. nginx also limits every /api request per IP
at the edge.

Failed sign-ins are counted per (email, IP) with a strict limit and per email with a much
higher backstop. Only failures count: someone who knows your email cannot lock you out by
spamming wrong passwords from one place, and your own correct password always goes through
unless the email is under attack from many addresses at once. Each attempt is counted
atomically *before* the password is checked and the count is taken back on success, so a
burst of parallel guesses cannot all slip under the limit.
"""

import logging
import time
from dataclasses import dataclass
from typing import Any, Protocol

from fastapi import HTTPException, status
from redis.exceptions import RedisError

logger = logging.getLogger(__name__)


class Counter(Protocol):
    """The Redis commands used (redis-py types them loosely, hence Any)."""

    def incr(self, name: str) -> Any: ...
    def decr(self, name: str) -> Any: ...
    def expire(self, name: str, time: int) -> Any: ...
    def delete(self, *names: str) -> Any: ...


@dataclass(frozen=True)
class Limit:
    name: str
    requests: int
    window_s: int


LOGIN_PER_IP = Limit("login-ip", 30, 300)
LOGIN_FAILURES_PER_EMAIL_IP = Limit("login-fail-email-ip", 8, 900)
LOGIN_FAILURES_PER_EMAIL = Limit("login-fail-email", 100, 3600)
REGISTER_PER_IP = Limit("register-ip", 10, 3600)
UPLOAD_PER_USER = Limit("upload", 60, 3600)
PROJECT_PER_USER = Limit("project", 60, 3600)
JOB_PER_USER = Limit("job", 30, 3600)


def _key(limit: Limit, key: str) -> str:
    return f"rl:{limit.name}:{key}:{int(time.time()) // limit.window_s}"


def _too_many(limit: Limit) -> HTTPException:
    retry = limit.window_s - int(time.time()) % limit.window_s
    return HTTPException(
        status.HTTP_429_TOO_MANY_REQUESTS,
        detail="Слишком много запросов. Попробуйте позже",
        headers={"Retry-After": str(retry)},
    )


def _unavailable() -> HTTPException:
    # Fail closed: without the counters, sign-in and uploads would be unlimited.
    return HTTPException(
        status.HTTP_503_SERVICE_UNAVAILABLE,
        detail="Сервис временно недоступен, попробуйте через минуту",
        headers={"Retry-After": "30"},
    )


def _count(counter: Counter, limit: Limit, key: str) -> int:
    name = _key(limit, key)
    try:
        count = int(counter.incr(name))
        if count == 1:
            counter.expire(name, limit.window_s)
    except RedisError:
        logger.warning("rate limiter unavailable (redis error)")
        raise _unavailable() from None
    return count


def check(counter: Counter, limit: Limit, key: str) -> None:
    """Count one request; raise 429 with Retry-After when the window is used up."""
    if _count(counter, limit, key) > limit.requests:
        raise _too_many(limit)


def undo(counter: Counter, limit: Limit, key: str) -> None:
    """Take back one counted request (e.g. a sign-in that turned out to be correct)."""
    try:
        counter.decr(_key(limit, key))
    except RedisError:
        logger.warning("rate limiter unavailable (redis error)")


def reset(counter: Counter, limit: Limit, key: str) -> None:
    try:
        counter.delete(_key(limit, key))
    except RedisError:
        logger.warning("rate limiter unavailable (redis error)")
