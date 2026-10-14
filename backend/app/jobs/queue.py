"""Sending jobs to the workers, and getting them back when a message or a worker is lost.

Redis runs without persistence (it is only a transport), so a Redis restart silently drops
every queued message. To notice that, Redis holds an *epoch* key: a random id created on first
use. Each job records the epoch it was sent under; after a restart the key is gone, a new epoch
appears, and every queued job from an older epoch is sent again. As a last resort, a job still
queued RESEND_AFTER_S after it was sent is sent again whatever the epoch (a message can also
be lost when a worker takes it and dies before claiming). Sending twice is safe: only one
worker can claim a job (app.jobs.store).

`recover` also returns jobs whose worker died (no heartbeat) to the queue. It runs when a worker
starts and, at most every RECOVER_EVERY_S seconds, when someone looks at their jobs, so
nothing needs a separate scheduler.
"""

from __future__ import annotations

import logging
import uuid
from datetime import timedelta
from typing import Any

from redis import Redis
from redis.exceptions import RedisError
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.jobs.store import MAX_ATTEMPTS, STALE_AFTER_S, now
from app.models import Job
from app.storage import remove_scratch
from app.workers.celery_app import QUEUE_AI, QUEUE_RENDER, celery_app

logger = logging.getLogger(__name__)

EPOCH_KEY = "jobs:epoch"
RECOVER_LOCK_KEY = "jobs:recover"
RECOVER_EVERY_S = 15
RESEND_AFTER_S = 10 * 60  # far longer than a normal wait in the queue

TASKS = {"plan": ("jobs.plan", QUEUE_AI), "render": ("jobs.render", QUEUE_RENDER)}
# Fail fast when Redis is down: the job stays queued and is sent again by recover().
PUBLISH_RETRY = {"max_retries": 2, "interval_start": 0, "interval_step": 0.5, "interval_max": 1}


def current_epoch(redis: Redis) -> str:
    epoch: Any = redis.get(EPOCH_KEY)
    if epoch is None:
        redis.set(EPOCH_KEY, str(uuid.uuid4()), nx=True)
        epoch = redis.get(EPOCH_KEY)
    return epoch.decode() if isinstance(epoch, bytes) else str(epoch)


def publish(db: Session, redis: Redis, job: Job) -> bool:
    """Send the job's message. Call after the job row is committed: the worker reads it.

    The epoch is read *before* sending: if Redis restarts in between, the job carries the old
    epoch and is sent again, rather than carrying the new one with its message lost."""
    try:
        epoch = current_epoch(redis)
        name, queue = TASKS[job.type]
        celery_app.send_task(
            name, args=[str(job.id)], queue=queue, retry=True, retry_policy=PUBLISH_RETRY
        )
    except Exception:  # Redis or kombu errors: the job waits for recover()
        logger.warning("could not send job %s to the queue", job.id, exc_info=True)
        return False
    job.enqueued_epoch = epoch
    job.enqueued_at = now()
    db.commit()
    return True


def recover(db: Session, redis: Redis, *, force: bool = False) -> int:
    """Requeue jobs of dead workers and resend jobs whose message was lost. Returns how many
    jobs were touched. Without `force`, runs at most once per RECOVER_EVERY_S across all
    API processes."""
    try:
        if not force and not redis.set(RECOVER_LOCK_KEY, "1", nx=True, ex=RECOVER_EVERY_S):
            return 0
        epoch = current_epoch(redis)
    except RedisError:
        logger.warning("job recovery skipped: redis unavailable")
        return 0

    touched = 0
    stale = now() - timedelta(seconds=STALE_AFTER_S)
    dead = db.scalars(
        select(Job)
        .where(Job.status == "running", Job.heartbeat_at < stale)
        .with_for_update(skip_locked=True)
    ).all()
    for job in dead:
        touched += 1
        remove_scratch(job.id)  # files of the dead attempt
        if job.attempts >= MAX_ATTEMPTS:
            logger.error("job %s lost its worker %d times, giving up", job.id, job.attempts)
            job.status = "failed"
            job.stage = "ошибка"
            job.error = (
                "Обработка прерывалась несколько раз. Попробуйте ещё раз; если повторится — "
                "уменьшите файлы или их число"
            )
            job.finished_at = now()
        else:
            logger.warning("job %s lost its worker, back to the queue", job.id)
            job.status = "queued"
            job.stage = "в очереди (повторный запуск)"
            job.enqueued_epoch = None
    db.commit()

    lost = db.scalars(
        select(Job).where(
            Job.status == "queued",
            or_(
                Job.enqueued_epoch.is_(None),
                Job.enqueued_epoch != epoch,
                Job.enqueued_at < now() - timedelta(seconds=RESEND_AFTER_S),
            ),
        )
    ).all()
    for job in lost:
        logger.warning("job %s was not in the queue, sending again", job.id)
        publish(db, redis, job)
        touched += 1
    return touched
