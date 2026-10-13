import logging
import threading
import time
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Response, status
from pydantic import BaseModel
from redis import Redis
from redis.exceptions import RedisError
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.db import get_db
from app.redis_client import get_redis
from app.workers.celery_app import QUEUE_AI, QUEUE_RENDER, celery_app

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/health", tags=["health"])

CheckStatus = Literal["ok", "fail"]


class HealthOut(BaseModel):
    status: CheckStatus
    database: CheckStatus
    redis: CheckStatus


class WorkersOut(BaseModel):
    status: CheckStatus
    queues: dict[str, int]


def _check_db(db: Session) -> CheckStatus:
    try:
        db.execute(text("SELECT 1"))
    except SQLAlchemyError:
        logger.exception("database health check failed")
        return "fail"
    return "ok"


def _check_redis(redis: Redis) -> CheckStatus:
    try:
        redis.ping()
    except RedisError:
        logger.exception("redis health check failed")
        return "fail"
    return "ok"


@router.get("", response_model=HealthOut)
def health(
    response: Response,
    db: Annotated[Session, Depends(get_db)],
    redis: Annotated[Redis, Depends(get_redis)],
) -> HealthOut:
    """Readiness of the API's own dependencies. Used by the Docker healthcheck."""
    result = HealthOut(status="ok", database=_check_db(db), redis=_check_redis(redis))
    if "fail" in (result.database, result.redis):
        result.status = "fail"
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return result


# A broadcast to the workers waits up to a second for replies. One request per interval asks;
# everyone else gets the cached answer, so polling UIs cannot tie up the API's threads.
WORKERS_CACHE_S = 10.0
_workers_lock = threading.Lock()
_workers_cache: tuple[float, dict[str, int]] | None = None


def _inspect_queues() -> dict[str, int]:
    queues = {QUEUE_AI: 0, QUEUE_RENDER: 0}
    try:
        replies = celery_app.control.inspect(timeout=1.0).active_queues() or {}
    except Exception:  # broker down or timeout — report, don't crash the endpoint
        logger.exception("worker inspection failed")
        replies = {}
    for worker_queues in replies.values():
        for queue in worker_queues:
            if queue["name"] in queues:
                queues[queue["name"]] += 1
    return queues


def _queues() -> dict[str, int]:
    global _workers_cache
    with _workers_lock:
        if _workers_cache is None or time.monotonic() - _workers_cache[0] > WORKERS_CACHE_S:
            _workers_cache = (time.monotonic(), _inspect_queues())
        return dict(_workers_cache[1])


@router.get("/workers", response_model=WorkersOut)
def workers(response: Response) -> WorkersOut:
    """How many live workers consume each queue. Lets the UI warn before a job would hang."""
    queues = _queues()
    ok = all(count > 0 for count in queues.values())
    if not ok:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return WorkersOut(status="ok" if ok else "fail", queues=queues)
