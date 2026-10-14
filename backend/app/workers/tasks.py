"""Celery tasks: thin wrappers that hand a job id to app.jobs. All state is in Postgres."""

import logging
import shutil
import uuid
from typing import Any

from celery.signals import worker_process_init, worker_ready
from sqlalchemy import select
from sqlalchemy.exc import InterfaceError, OperationalError

from app.db import get_engine, get_session_factory
from app.jobs.plan import run_plan
from app.jobs.queue import recover
from app.jobs.render import run_render
from app.llm.client import OpenAICompatibleModel
from app.models import Job
from app.redis_client import get_redis
from app.storage import scratch_dirs
from app.workers.celery_app import QUEUE_AI, QUEUE_RENDER, celery_app

logger = logging.getLogger(__name__)


@celery_app.task(name="system.ping_ai", queue=QUEUE_AI)
def ping_ai() -> str:
    return "pong"


@celery_app.task(name="system.ping_render", queue=QUEUE_RENDER)
def ping_render() -> str:
    return "pong"


# The database unreachable while taking a job (restart, too many connections): retry the
# message rather than let Celery acknowledge it and leave the job queued forever. Jobs catch
# everything else themselves and record it as a failure.
DB_DOWN = {
    "autoretry_for": (OperationalError, InterfaceError),
    "retry_backoff": 2,
    "retry_backoff_max": 60,
    "retry_jitter": True,
    "max_retries": 20,
}


# Up to four model calls of LLM_TIMEOUT_S (240 s) each in the worst case.
@celery_app.task(
    name="jobs.plan", queue=QUEUE_AI, soft_time_limit=18 * 60, time_limit=19 * 60, **DB_DOWN
)
def plan(job_id: str) -> None:
    run_plan(uuid.UUID(job_id), get_session_factory(), OpenAICompatibleModel)


# A video is recorded frame by frame: up to VIDEO_TIMEOUT_S (15 min) in the renderer.
@celery_app.task(
    name="jobs.render", queue=QUEUE_RENDER, soft_time_limit=17 * 60, time_limit=18 * 60, **DB_DOWN
)
def render(job_id: str) -> None:
    run_render(uuid.UUID(job_id), get_session_factory())


@worker_process_init.connect
def _fresh_connections(**_: Any) -> None:
    # A forked pool process must not share the parent's database connections.
    get_engine().dispose(close=False)


@worker_ready.connect
def _recover_jobs(**_: Any) -> None:
    """A worker that (re)starts picks up what a dead worker or a Redis restart left behind:
    jobs to resend or requeue, and scratch folders of renders killed mid-build."""
    try:
        with get_session_factory()() as db:
            touched = recover(db, get_redis(), force=True)
            if touched:
                logger.info("recovered %d jobs on startup", touched)
            running = set(db.scalars(select(Job.id).where(Job.status == "running")))
        for job_id, path in scratch_dirs():
            if job_id not in running:
                shutil.rmtree(path, ignore_errors=True)
    except Exception:
        logger.exception("job recovery on startup failed")
