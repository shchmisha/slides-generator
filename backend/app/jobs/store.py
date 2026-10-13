"""Job state transitions. Postgres is the only source of truth for a job.

A worker *claims* a queued job by raising its `attempts` counter in one conditional UPDATE;
that number is the worker's fencing token. Every later write (progress, result, failure)
matches on it, so if the job was deleted, or handed to another worker because this one was
presumed dead, the write changes nothing and the worker stops. A Redis message delivered twice
is harmless: the second claim finds the job no longer queued.

While working, a background thread refreshes `heartbeat_at`. A running job with an old
heartbeat lost its worker (killed, out of memory, container restarted); app.jobs.queue
puts it back in the queue, at most MAX_ATTEMPTS times in total.
"""

from __future__ import annotations

import logging
import threading
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker

from app.models import Job

logger = logging.getLogger(__name__)

HEARTBEAT_EVERY_S = 15
HEARTBEAT_RETRY_S = 2  # after a failed beat (database outage): catch up before recover() looks
STALE_AFTER_S = 90  # six missed heartbeats: the worker is gone
MAX_ATTEMPTS = 2  # a job that brought its worker down twice fails instead of looping


class JobLostError(RuntimeError):
    """The job was deleted or is now owned by a newer attempt: stop without writing."""


@dataclass(frozen=True)
class Claim:
    job_id: uuid.UUID
    attempt: int


def now() -> datetime:
    return datetime.now(UTC)


def _owned(claim: Claim) -> Any:
    return (Job.id == claim.job_id) & (Job.attempts == claim.attempt) & (Job.status == "running")


def claim(db: Session, job_id: uuid.UUID, stage: str) -> Claim | None:
    """Take a queued job. None: it is already running elsewhere, finished, or deleted."""
    attempt = db.scalar(
        update(Job)
        .where(Job.id == job_id, Job.status == "queued")
        .values(
            status="running",
            attempts=Job.attempts + 1,
            started_at=now(),
            heartbeat_at=now(),
            progress=1,
            stage=stage,
        )
        .returning(Job.attempts)
    )
    db.commit()
    return None if attempt is None else Claim(job_id, attempt)


def report_progress(db: Session, claim: Claim, progress: int, stage: str) -> None:
    result = db.execute(
        update(Job)
        .where(_owned(claim))
        .values(progress=min(max(progress, 0), 99), stage=stage, heartbeat_at=now())
    )
    db.commit()
    if result.rowcount == 0:  # type: ignore[attr-defined]
        raise JobLostError(str(claim.job_id))


def lock_owned(db: Session, claim: Claim) -> Job:
    """Lock the job row for the final transaction (results + status in one commit)."""
    job = db.scalar(select(Job).where(_owned(claim)).with_for_update())
    if job is None:
        raise JobLostError(str(claim.job_id))
    return job


def mark_succeeded(
    job: Job, *, timings: dict[str, Any], report: dict[str, Any] | None = None
) -> None:
    """Within the caller's transaction, after lock_owned()."""
    job.status = "succeeded"
    job.progress = 100
    job.stage = "готово"
    job.finished_at = now()
    job.timings = timings
    job.report = report or {}


def mark_failed(
    db: Session,
    claim: Claim,
    message: str,
    *,
    timings: dict[str, Any] | None = None,
    report: dict[str, Any] | None = None,
) -> None:
    """Record a user-safe message. Silently does nothing if the job is no longer ours.

    Pass a fresh session: the one the job used may be broken (a time limit can interrupt a
    query in the middle)."""
    db.rollback()
    db.execute(
        update(Job)
        .where(_owned(claim))
        .values(
            status="failed",
            stage="ошибка",
            error=message[:2000],
            finished_at=now(),
            timings=timings or {},
            report=report or {},
        )
    )
    db.commit()


def fail(sessions: sessionmaker[Session], claim: Claim, message: str, **details: Any) -> None:
    """mark_failed in a fresh session; never raises (the caller is already handling an error)."""
    try:
        with sessions() as db:
            mark_failed(db, claim, message, **details)
    except Exception:
        logger.exception("could not record the failure of job %s", claim.job_id)


@contextmanager
def heartbeat(sessions: sessionmaker[Session], claim: Claim) -> Iterator[threading.Event]:
    """Refresh heartbeat_at in the background. The yielded event is set if the job is lost."""
    stop = threading.Event()
    lost = threading.Event()

    def beat() -> None:
        wait = HEARTBEAT_EVERY_S
        while not stop.wait(wait):
            try:
                with sessions() as db:
                    result = db.execute(update(Job).where(_owned(claim)).values(heartbeat_at=now()))
                    db.commit()
                    if result.rowcount == 0:  # type: ignore[attr-defined]
                        lost.set()
                        return
                wait = HEARTBEAT_EVERY_S
            except SQLAlchemyError:
                logger.warning("heartbeat for job %s failed", claim.job_id, exc_info=True)
                wait = HEARTBEAT_RETRY_S

    thread = threading.Thread(target=beat, name=f"heartbeat-{claim.job_id}", daemon=True)
    thread.start()
    try:
        yield lost
    finally:
        stop.set()
        thread.join(timeout=5)
