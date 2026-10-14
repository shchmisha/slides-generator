"""The `render` job: an approved scenario version → deck.pptx and a PNG preview per slide, or
video.mp4 and a poster per scene.

The scenario is checked again against its stored basis (the same deterministic code that the
review step showed), so the file contains exactly what the user approved. The skill writes into
a scratch folder in the spool; the files are copied onto the data volume and recorded in the
same transaction that marks the job done, and removed again if that transaction fails.
"""

from __future__ import annotations

import hashlib
import json
import logging
import math
import shutil
import uuid
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, wait
from pathlib import Path
from time import perf_counter
from typing import Any

from celery.exceptions import SoftTimeLimitExceeded
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker, undefer

from app.agent.review import DeckBasis, Review, VideoBasis, review
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
from app.render.skill import (
    DeckBuilder,
    SkillError,
    VideoBuild,
    VideoBuilder,
    build_deck,
    build_video,
)
from app.storage import artifact_path, logo_path, read_spool_file, scratch_dir, take_from_spool

logger = logging.getLogger(__name__)

KEEP_RENDERS = 5  # per project; older files and previews are removed (the jobs stay in history)
MAX_OUTPUT_BYTES = {"pptx": 100 * 1024 * 1024, "mp4": 500 * 1024 * 1024, "preview": 5 * 1024 * 1024}
PROGRESS_EVERY_S = 2.0

# (artifact kind, position, file in the scratch folder)
Output = tuple[str, int, Path]


class _JobError(Exception):
    """A failure with a message for the user."""


def _ms(started: float) -> int:
    return round((perf_counter() - started) * 1000)


def run_render(
    job_id: uuid.UUID,
    sessions: sessionmaker[Session],
    build: DeckBuilder = build_deck,
    record: VideoBuilder = build_video,
) -> None:
    with sessions() as db:
        claimed = claim(db, job_id, "подготовка к сборке")
        job = db.get(Job, job_id) if claimed else None
    if claimed is None or job is None:
        logger.info("render job %s is not queued any more, skipping", job_id)
        return
    timings: dict[str, Any] = {}
    scratch = scratch_dir(job_id, claimed.attempt)
    with heartbeat(sessions, claimed):
        try:
            with sessions() as db:
                _render(db, sessions, claimed, (build, record), scratch, timings)
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
    builders: tuple[DeckBuilder, VideoBuilder],
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

    build, record = builders
    if project.kind == "deck":
        outputs, report = _deck(db, claimed, build, scenario, project, scratch, timings)
    else:
        outputs, report = _video(db, claimed, record, scenario, project, scratch, timings)
    report["scenario_version"] = scenario.version

    report_progress(db, claimed, 95, "сохранение файлов")
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
    stored = _store_outputs(db, job, project, outputs)
    try:
        timings["save_ms"] = _ms(t)
        timings["total_ms"] = _ms(started)
        mark_succeeded(job, timings=timings, report=report)
        db.commit()
        _prune(db, project.id)
    except BaseException:
        db.rollback()
        # A commit can fail after the server applied it (connection lost on the reply): keep
        # the files then, the rows point at them.
        if not _succeeded(sessions, claimed):
            for path in stored:
                path.unlink(missing_ok=True)
        raise


def _checked(scenario: Scenario, basis: DeckBasis | VideoBasis) -> Review:
    checked = review(scenario.content, basis)
    if not checked.ok:
        raise _JobError("Сценарий не проходит проверку: " + "; ".join(checked.errors[:5]))
    return checked


def _deck(
    db: Session,
    claimed: Claim,
    build: DeckBuilder,
    scenario: Scenario,
    project: Project,
    scratch: Path,
    timings: dict[str, Any],
) -> tuple[list[Output], dict[str, Any]]:
    t = perf_counter()
    checked = _checked(scenario, DeckBasis.model_validate(scenario.basis))
    spec = json.dumps(checked.report["preview"], ensure_ascii=False)
    theme = project.brand or DEFAULT_THEME
    logo = _logo(project)
    timings["prepare_ms"] = _ms(t)

    report_progress(db, claimed, 10, "вёрстка слайдов")
    t = perf_counter()
    result = build(spec, theme, logo, scratch)
    timings["render_ms"] = _ms(t)
    timings |= {key: round(value) for key, value in result.timings.items()}
    outputs: list[Output] = [("pptx", 0, result.pptx)]
    outputs += [("preview", n, path) for n, path in enumerate(result.previews, start=1)]
    report = {
        "slides": len(result.previews),
        "issues": result.issues,
        "warnings": result.font_warnings,
    }
    return outputs, report


def video_spec(content: dict[str, Any]) -> dict[str, Any]:
    """The video script's input: each scene with its start time on the whole video."""
    scenes, start = [], 0.0
    for scene in content["scenes"]:
        duration = float(scene["duration_s"])
        scenes.append(
            {
                "start": start,
                "duration": duration,
                "narration": scene["narration"],
                "visual": scene["visual"],
            }
        )
        start = round(start + duration, 3)  # durations are tenths: no drift over the scenes
    return {"title": content["title"], "scenes": scenes}


def _video(
    db: Session,
    claimed: Claim,
    record: VideoBuilder,
    scenario: Scenario,
    project: Project,
    scratch: Path,
    timings: dict[str, Any],
) -> tuple[list[Output], dict[str, Any]]:
    t = perf_counter()
    checked = _checked(scenario, VideoBasis.model_validate(scenario.basis))
    timeline = video_spec(checked.content or scenario.content)
    spec = json.dumps(timeline, ensure_ascii=False)
    theme = project.brand or DEFAULT_THEME
    logo = _logo(project)
    timings["prepare_ms"] = _ms(t)

    report_progress(db, claimed, 5, "запись видео")
    t = perf_counter()
    result = _while_recording(db, claimed, scratch, lambda: record(spec, theme, logo, scratch))
    timings["render_ms"] = _ms(t)
    timings |= {key: round(value) for key, value in result.timings.items()}
    outputs: list[Output] = [("mp4", 0, result.video)]
    outputs += [("preview", n, path) for n, path in enumerate(result.posters, start=1)]
    report = {
        "scenes": len(result.posters),
        "seconds": round(result.duration, 1),
        "starts": [scene["start"] for scene in timeline["scenes"]],  # the player jumps to scenes
        "warnings": list(checked.report.get("warnings", [])),
    }
    return outputs, report


def _while_recording(
    db: Session, claimed: Claim, scratch: Path, work: Callable[[], VideoBuild]
) -> VideoBuild:
    """Record in a thread and pass the script's own progress (out/progress.json in the spool)
    on to the job as 5–90 %. Only the number is used: the renderer is not trusted with text
    that the user sees."""
    pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="record")
    try:
        future = pool.submit(work)
        shown = 5
        while not wait([future], timeout=PROGRESS_EVERY_S).done:
            value = _recording_progress(scratch)
            if value is None:
                continue
            percent = 5 + round(value * 0.85)
            if percent > shown:
                shown = percent
                stage = "запись видео" if value < 97 else "кадры для предпросмотра"
                report_progress(db, claimed, percent, stage)  # JobLostError if deleted
        return future.result()
    finally:
        # On a time limit or a deleted job, stop waiting; the renderer has its own timeout.
        pool.shutdown(wait=False, cancel_futures=True)


def _recording_progress(scratch: Path) -> float | None:
    try:
        data = json.loads(read_spool_file(scratch / "out" / "progress.json", 4096))
        value = float(data["progress"])
    except (OSError, ValueError, KeyError, TypeError):
        return None  # not written yet, or caught mid-write
    return min(max(value, 0.0), 100.0) if math.isfinite(value) else None


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


def _store_outputs(db: Session, job: Job, project: Project, outputs: list[Output]) -> list[Path]:
    """Record an artifact per file and copy the file into place; returns the new paths."""
    stored: list[Path] = []
    try:
        for kind, position, source in outputs:
            artifact_id = uuid.uuid4()
            target = artifact_path(project.id, artifact_id, kind)
            size = take_from_spool(source, target, MAX_OUTPUT_BYTES[kind])
            stored.append(target)
            db.add(
                Artifact(
                    id=artifact_id,
                    project_id=project.id,
                    user_id=project.user_id,
                    job_id=job.id,
                    kind=kind,
                    position=position,
                    size=size,
                )
            )
    except BaseException:
        for path in stored:
            path.unlink(missing_ok=True)
        raise
    return stored
