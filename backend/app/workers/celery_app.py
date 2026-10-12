"""Celery application.

Two queues with different resource profiles:
- `ai`     — file parsing and LLM calls: I/O-bound, runs with high concurrency.
- `render` — PPTX and MP4 rendering: CPU/RAM-heavy (headless Chrome + FFmpeg), low concurrency.

Redis is only the transport. Job status, progress and history live in Postgres, so they
survive a Redis restart and can be queried with ownership checks like any other resource.
"""

from celery import Celery

from app.config import get_settings
from app.log_config import configure_logging

QUEUE_AI = "ai"
QUEUE_RENDER = "render"

_settings = get_settings()
configure_logging(_settings.log_level)

celery_app = Celery(
    "slides",
    broker=_settings.redis_url.get_secret_value(),
    include=["app.workers.tasks"],
)

celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_backend=None,
    task_ignore_result=True,
    task_default_queue=QUEUE_AI,
    # A task is acknowledged only after it finishes, so a crashed worker hands it back.
    task_acks_late=True,
    task_reject_on_worker_lost=True,
    # One task at a time per process: render tasks are long and must not queue up
    # behind each other inside a single worker.
    worker_prefetch_multiplier=1,
    # Hard kill for anything stuck (e.g. a hung headless Chrome).
    task_soft_time_limit=15 * 60,
    task_time_limit=16 * 60,
    broker_connection_retry_on_startup=True,
    worker_hijack_root_logger=False,
)
