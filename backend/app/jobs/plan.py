"""The `plan` job: a project's files → a checked scenario (version N, written by the model).

Stages and their timings: reading the files, cleaning the data (deck), the model call with its
repair rounds, saving. Every stage reports progress, so the user sees where the time goes; the
brief asks for exactly this split (parsing, model, render).
"""

from __future__ import annotations

import logging
import threading
import uuid
from collections.abc import Callable
from time import perf_counter
from typing import Any

from celery.exceptions import SoftTimeLimitExceeded
from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from app.agent.planner import Attempt, PlanningError, plan_deck, plan_video
from app.agent.review import deck_basis, deck_report, video_basis, video_report
from app.agent.settings import DeckSettings, VideoSettings
from app.facts.build import build_facts
from app.ingest.errors import IngestError
from app.ingest.extract import extract_file, extract_text
from app.ingest.models import Document
from app.jobs.store import (
    Claim,
    JobLostError,
    claim,
    fail,
    heartbeat,
    lock_owned,
    mark_succeeded,
    report_progress,
)
from app.llm.client import ChatModel, LLMError
from app.models import Job, Project, Scenario, StoredFile
from app.storage import upload_path

logger = logging.getLogger(__name__)

ModelFactory = Callable[[], ChatModel]

_LLM_START, _LLM_SPAN = 25, 60  # progress while the model works: 25 % → 85 %


class _JobError(Exception):
    """A failure with a message for the user."""

    def __init__(self, message: str, report: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.report = report or {}


def _ms(started: float) -> int:
    return round((perf_counter() - started) * 1000)


def _read(files: list[StoredFile], project_id: uuid.UUID) -> list[Document]:
    documents = []
    for stored in files:
        data = upload_path(project_id, stored.id, stored.kind).read_bytes()
        try:
            if stored.kind == "text":
                documents.append(extract_text(data.decode("utf-8"), stored.filename))
            else:
                documents.append(extract_file(data, stored.filename))
        except IngestError as exc:
            raise _JobError(f"Файл «{stored.filename}»: {exc}") from exc
    return documents


def _attempts(attempts: list[Attempt]) -> list[dict[str, Any]]:
    """For the job report: no model replies (they can be long), only what tuning needs."""
    return [
        {
            "latency_ms": round(a.latency_ms),
            "prompt_tokens": a.prompt_tokens,
            "completion_tokens": a.completion_tokens,
            "errors": a.errors[:10],
            "findings": a.findings[:10],
        }
        for a in attempts
    ]


def run_plan(job_id: uuid.UUID, sessions: sessionmaker[Session], make_model: ModelFactory) -> None:
    with sessions() as db:
        claimed = claim(db, job_id, "разбор файлов")
        if claimed is None:
            logger.info("plan job %s is not queued any more, skipping", job_id)
            return
        job = db.get(Job, job_id)
        queue_ms = (
            round((job.started_at - job.created_at).total_seconds() * 1000)
            if job and job.started_at and job.created_at and job.created_at.tzinfo
            else None
        )
    started = perf_counter()
    timings: dict[str, Any] = {"queue_ms": queue_ms} if queue_ms is not None else {}
    with heartbeat(sessions, claimed) as lost:
        try:
            with sessions() as db:
                _plan(db, claimed, make_model, timings, lost)
        except JobLostError:
            logger.info("plan job %s was deleted or taken over, stopping", job_id)
        except _JobError as exc:
            timings["total_ms"] = _ms(started)
            fail(sessions, claimed, str(exc), timings=timings, report=exc.report)
        except SoftTimeLimitExceeded:
            fail(sessions, claimed, "Генерация заняла слишком много времени", timings=timings)
        except Exception:
            logger.exception("plan job %s crashed", job_id)
            fail(sessions, claimed, "Внутренняя ошибка при составлении сценария", timings=timings)
        else:
            logger.info("plan job %s done in %d ms", job_id, _ms(started))


def _plan(
    db: Session,
    claimed: Claim,
    make_model: ModelFactory,
    timings: dict[str, Any],
    lost: threading.Event,
) -> None:
    started = perf_counter()
    job = db.get(Job, claimed.job_id)
    project = db.get(Project, job.project_id) if job else None
    if job is None or project is None:
        raise JobLostError(str(claimed.job_id))
    files = list(project.files)
    if not files:
        raise _JobError("В проекте нет файлов и текста: добавьте материалы")

    t = perf_counter()
    documents = _read(files, project.id)
    timings["parse_ms"] = _ms(t)

    def on_attempt(number: int) -> None:
        if lost.is_set():  # the heartbeat saw the job deleted or taken over: stop paying
            raise JobLostError(str(claimed.job_id))
        stage = "модель пишет сценарий" if number == 1 else f"модель исправляет сценарий ({number})"
        report_progress(db, claimed, _LLM_START + _LLM_SPAN * (number - 1) // 3, stage)

    model = make_model()
    try:
        if project.kind == "deck":
            report_progress(db, claimed, 15, "подготовка данных")
            t = perf_counter()
            store, notes = build_facts(documents)
            timings["facts_ms"] = _ms(t)
            deck_settings = DeckSettings.model_validate(project.settings)
            deck = plan_deck(documents, store, notes, deck_settings, model, on_attempt)
            if deck.resolved is None:  # plan_deck returns only resolved scenarios
                raise PlanningError(["сценарий не разрешён"], deck.attempts)
            plan: Any = deck
            content = deck.scenario.model_dump(mode="json", exclude_none=True)
            report = deck_report(deck.resolved, deck.warnings, deck.notes)
            basis = deck_basis(deck, deck_settings).model_dump(mode="json")
        else:
            video_settings = VideoSettings.model_validate(project.settings)
            video = plan_video(documents, video_settings, model, on_attempt)
            plan = video
            content = video.scenario.model_dump(mode="json", exclude_none=True)
            report = video_report(video.scenario, video.warnings, video.notes)
            basis = video_basis(video, video_settings).model_dump(mode="json")
    except PlanningError as exc:
        timings["llm_ms"] = round(sum(a.latency_ms for a in exc.attempts))
        raise _JobError(
            str(exc), {"errors": exc.errors[:30], "attempts": _attempts(exc.attempts)}
        ) from exc
    except LLMError as exc:
        raise _JobError(str(exc)) from exc
    timings["llm_ms"] = round(plan.llm_ms)
    timings["attempts"] = len(plan.attempts)

    report_progress(db, claimed, 92, "сохранение сценария")
    t = perf_counter()
    # Locks in the API's order, project before job, so saving cannot deadlock with an upload,
    # an edit or a delete. FOR NO KEY UPDATE orders this version number against the user's
    # own edits without blocking foreign-key checks.
    locked = db.scalar(
        select(Project.id).where(Project.id == project.id).with_for_update(key_share=True)
    )
    if locked is None:
        raise JobLostError(str(claimed.job_id))
    job = lock_owned(db, claimed)
    version = (
        db.scalar(select(func.max(Scenario.version)).where(Scenario.project_id == project.id)) or 0
    ) + 1
    scenario = Scenario(
        id=uuid.uuid4(),
        project_id=project.id,
        user_id=project.user_id,
        version=version,
        source="model",
        content=content,
        report=report,
        basis=basis,
    )
    db.add(scenario)
    db.flush()  # no ORM relationship orders the insert before the job's foreign key
    job.scenario_id = scenario.id
    timings["save_ms"] = _ms(t)
    timings["total_ms"] = _ms(started)
    mark_succeeded(job, timings=timings, report={"attempts": _attempts(plan.attempts)})
    db.commit()
