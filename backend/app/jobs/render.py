"""The `render` job: an approved scenario version → deck.pptx and one PNG preview per slide.

The scenario is resolved again from its stored basis (the same deterministic code that the
review step showed), so the file contains exactly the numbers the user approved. The skill
writes into a scratch folder on the data volume; the files are moved into place and recorded in
the same transaction that marks the job done, and removed again if that transaction fails.
"""

from __future__ import annotations

import hashlib
import json
import logging
import shutil
import uuid
from pathlib import Path
from time import perf_counter
from typing import Any

from celery.exceptions import SoftTimeLimitExceeded
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker, undefer

from app.agent.review import DeckBasis, review
from app.brand import DEFAULT_THEME
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
from app.models import Artifact, Job, Project, Scenario
from app.render.skill import DeckBuild, DeckBuilder, SkillError, build_deck
from app.storage import artifact_path, logo_path, scratch_dir

logger = logging.getLogger(__name__)

KEEP_RENDERS = 5  # per project; older decks and previews are removed (the jobs stay in history)


class _JobError(Exception):
    """A failure with a message for the user."""


def _ms(started: float) -> int:
    return round((perf_counter() - started) * 1000)


def run_render(
    job_id: uuid.UUID, sessions: sessionmaker[Session], build: DeckBuilder = build_deck
) -> None:
    with sessions() as db:
        claimed = claim(db, job_id, "подготовка слайдов")
        job = db.get(Job, job_id) if claimed else None
    if claimed is None or job is None:
        logger.info("render job %s is not queued any more, skipping", job_id)
        return
    timings: dict[str, Any] = {}
    scratch = scratch_dir(job_id, claimed.attempt)
    with heartbeat(sessions, claimed):
        try:
            with sessions() as db:
                _render(db, sessions, claimed, build, scratch, timings)
        except JobLostError:
            logger.info("render job %s was deleted or taken over, stopping", job_id)
        except (_JobError, SkillError) as exc:
            fail(sessions, claimed, str(exc), timings=timings)
        except SoftTimeLimitExceeded:
            fail(sessions, claimed, "Сборка заняла слишком много времени", timings=timings)
        except Exception:
            logger.exception("render job %s crashed", job_id)
            fail(sessions, claimed, "Внутренняя ошибка при сборке файла", timings=timings)
        finally:
            shutil.rmtree(scratch, ignore_errors=True)


def _logo(project: Project) -> bytes | None:
    if project.logo_sha256 is None:
        return None
    data = logo_path(project.id).read_bytes()
    if hashlib.sha256(data).hexdigest() != project.logo_sha256:
        raise _JobError("Файл логотипа изменился или повреждён: загрузите логотип ещё раз")
    return data


def _render(
    db: Session,
    sessions: sessionmaker[Session],
    claimed: Claim,
    build: DeckBuilder,
    scratch: Path,
    timings: dict[str, Any],
) -> None:
    started = perf_counter()
    job = db.get(Job, claimed.job_id)
    project = db.get(Project, job.project_id) if job else None
    scenario = (
        db.scalar(
            select(Scenario).options(undefer(Scenario.basis)).where(Scenario.id == job.scenario_id)
        )
        if job and job.scenario_id
        else None
    )
    if job is None or project is None:
        raise JobLostError(str(claimed.job_id))
    if scenario is None:
        raise _JobError("Версия сценария не найдена")
    if project.kind != "deck":
        raise _JobError("Сборка видео пока не подключена")

    t = perf_counter()
    checked = review(scenario.content, DeckBasis.model_validate(scenario.basis))
    if not checked.ok:
        raise _JobError("Сценарий не проходит проверку: " + "; ".join(checked.errors[:5]))
    spec = json.dumps(checked.report["preview"], ensure_ascii=False)
    theme = project.brand or DEFAULT_THEME
    logo = _logo(project)
    timings["prepare_ms"] = _ms(t)

    report_progress(db, claimed, 10, "вёрстка слайдов")
    t = perf_counter()
    result = build(spec, theme, logo, scratch)
    timings["render_ms"] = _ms(t)
    timings |= {key: round(value) for key, value in result.timings.items()}

    report_progress(db, claimed, 90, "сохранение файлов")
    t = perf_counter()
    # Project before job, as in the API (no deadlock with a delete); FOR KEY SHARE only keeps
    # the project from disappearing while the artifacts are recorded.
    locked = db.scalar(
        select(Project.id)
        .where(Project.id == project.id)
        .with_for_update(read=True, key_share=True)
    )
    if locked is None:
        raise JobLostError(str(claimed.job_id))
    job = lock_owned(db, claimed)
    moved = _store_outputs(db, job, project, result)
    try:
        timings["save_ms"] = _ms(t)
        timings["total_ms"] = _ms(started)
        report = {
            "slides": len(result.previews),
            "issues": result.issues,
            "warnings": result.font_warnings,
            "scenario_version": scenario.version,
        }
        mark_succeeded(job, timings=timings, report=report)
        db.commit()
        _prune(db, project.id)
    except BaseException:
        db.rollback()
        # A commit can fail after the server applied it (connection lost on the reply): keep
        # the files then, the rows point at them.
        if not _succeeded(sessions, claimed):
            for path in moved:
                path.unlink(missing_ok=True)
        raise


def _prune(db: Session, project_id: uuid.UUID) -> None:
    """Keep the files of the last KEEP_RENDERS renders of a project; never fails the job."""
    try:
        old_jobs = select(Job.id).where(
            Job.project_id == project_id, Job.type == "render", Job.status == "succeeded"
        )
        stale = db.scalars(
            select(Artifact).where(
                Artifact.job_id.in_(
                    old_jobs.order_by(Job.finished_at.desc(), Job.id).offset(KEEP_RENDERS)
                )
            )
        ).all()
        paths = [artifact_path(a.project_id, a.id, a.kind) for a in stale]
        for artifact in stale:
            db.delete(artifact)
        db.commit()
        for path in paths:
            path.unlink(missing_ok=True)
    except Exception:
        db.rollback()
        logger.exception("could not remove old renders of project %s", project_id)


def _succeeded(sessions: sessionmaker[Session], claimed: Claim) -> bool:
    try:
        with sessions() as db:
            job = db.get(Job, claimed.job_id)
            return bool(job and job.status == "succeeded" and job.attempts == claimed.attempt)
    except Exception:
        logger.exception("could not check how render job %s ended", claimed.job_id)
        return True  # unknown: keeping files is safer than rows without files


def _store_outputs(db: Session, job: Job, project: Project, result: DeckBuild) -> list[Path]:
    """Record an artifact per file and move the file into place; returns the new paths."""
    outputs = [("pptx", 0, result.pptx)] + [
        ("preview", number, path) for number, path in enumerate(result.previews, start=1)
    ]
    moved: list[Path] = []
    try:
        for kind, position, source in outputs:
            artifact = Artifact(
                id=uuid.uuid4(),
                project_id=project.id,
                user_id=project.user_id,
                job_id=job.id,
                kind=kind,
                position=position,
                size=source.stat().st_size,
            )
            target = artifact_path(project.id, artifact.id, kind)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(source, target)  # the spool is another volume: copy, then delete
            moved.append(target)
            db.add(artifact)
    except BaseException:
        for path in moved:
            path.unlink(missing_ok=True)
        raise
    return moved
