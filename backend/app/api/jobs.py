"""Starting generations and following them: plan (files → scenario) and render (scenario →
file). A job is answered at once with 202 and its id; the client polls it.

Limits: one active job per project (a unique index, so two clicks cannot start two),
MAX_ACTIVE_JOBS_PER_USER per user, and JOB_PER_USER starts per hour.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.projects import OwnedProject, RedisDep, lock_user, storage_used
from app.auth.deps import DB, CurrentUser
from app.config import get_settings
from app.jobs.queue import publish, recover
from app.jobs.store import now
from app.models import Job, Project, Scenario, User
from app.models.entities import ACTIVE_STATUSES
from app.security import rate_limit

logger = logging.getLogger(__name__)

router = APIRouter(tags=["jobs"])

Limit = Annotated[int, Query(ge=1, le=200)]


class JobOut(BaseModel):
    id: uuid.UUID
    project_id: uuid.UUID
    type: str
    status: str
    progress: int
    stage: str
    error: str | None
    scenario_version: int | None  # plan: the version written; render: the version built
    timings: dict[str, Any]
    report: dict[str, Any]
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None


class RenderIn(BaseModel):
    version: int | None = Field(default=None, ge=1, le=2**31 - 1)  # default: the latest


def _out(db: Session, jobs: list[Job]) -> list[JobOut]:
    ids = {job.scenario_id for job in jobs if job.scenario_id}
    versions: dict[uuid.UUID, int] = {}
    if ids:
        rows = db.execute(select(Scenario.id, Scenario.version).where(Scenario.id.in_(ids)))
        versions = {row.id: row.version for row in rows}
    return [
        JobOut(
            id=job.id,
            project_id=job.project_id,
            type=job.type,
            status=job.status,
            progress=job.progress,
            stage=job.stage,
            error=job.error,
            scenario_version=versions.get(job.scenario_id) if job.scenario_id else None,
            timings=job.timings,
            report=job.report,
            created_at=job.created_at,
            started_at=job.started_at,
            finished_at=job.finished_at,
        )
        for job in jobs
    ]


def _recover_quietly(db: Session, redis: RedisDep) -> None:
    """Lost messages and dead workers are noticed while someone is watching their jobs."""
    try:
        recover(db, redis)
    except Exception:
        db.rollback()
        logger.exception("job recovery failed")


def _start(
    db: Session,
    redis: RedisDep,
    user: User,
    project: Project,
    kind: str,
    scenario_id: uuid.UUID | None = None,
) -> JobOut:
    lock_user(db, user)
    active = db.scalar(
        select(func.count())
        .select_from(Job)
        .where(Job.user_id == user.id, Job.status.in_(ACTIVE_STATUSES))
    )
    limit = get_settings().max_active_jobs_per_user
    if (active or 0) >= limit:
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS,
            detail=f"У вас уже {active} генерации в работе: дождитесь, пока одна закончится",
        )
    job = Job(
        id=uuid.uuid4(),
        project_id=project.id,
        user_id=user.id,
        type=kind,
        status="queued",
        progress=0,
        stage="в очереди",
        scenario_id=scenario_id,
        created_at=now(),  # microseconds on any database: history is ordered by it
    )
    db.add(job)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            detail="Для этого проекта уже идёт генерация: дождитесь её окончания",
        ) from None
    publish(db, redis, job)  # if Redis is down the job waits; recovery sends it later
    return _out(db, [job])[0]


@router.post("/projects/{project_id}/plan", status_code=status.HTTP_202_ACCEPTED)
def start_plan(project: OwnedProject, db: DB, user: CurrentUser, redis: RedisDep) -> JobOut:
    rate_limit.check(redis, rate_limit.JOB_PER_USER, str(user.id))
    if not project.files:
        raise HTTPException(
            status.HTTP_409_CONFLICT, detail="Добавьте файлы или текст, чтобы составить сценарий"
        )
    return _start(db, redis, user, project, "plan")


@router.post("/projects/{project_id}/render", status_code=status.HTTP_202_ACCEPTED)
def start_render(
    project: OwnedProject,
    db: DB,
    user: CurrentUser,
    redis: RedisDep,
    body: RenderIn | None = None,
) -> JobOut:
    rate_limit.check(redis, rate_limit.JOB_PER_USER, str(user.id))
    if storage_used(db, user) >= get_settings().max_bytes_per_user:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            detail="Место для ваших файлов закончилось — удалите ненужные проекты",
        )
    query = select(Scenario).where(Scenario.project_id == project.id)
    if body is not None and body.version is not None:
        query = query.where(Scenario.version == body.version)
    scenario = db.scalar(query.order_by(Scenario.version.desc()).limit(1))
    if scenario is None:
        raise HTTPException(
            status.HTTP_409_CONFLICT, detail="Сначала составьте сценарий и проверьте его"
        )
    return _start(db, redis, user, project, "render", scenario.id)


@router.get("/projects/{project_id}/jobs")
def project_jobs(project: OwnedProject, db: DB, redis: RedisDep, limit: Limit = 50) -> list[JobOut]:
    _recover_quietly(db, redis)
    jobs = db.scalars(
        select(Job)
        .where(Job.project_id == project.id)
        .order_by(Job.created_at.desc(), Job.id)
        .limit(limit)
    ).all()
    return _out(db, list(jobs))


@router.get("/jobs")
def my_jobs(db: DB, user: CurrentUser, redis: RedisDep, limit: Limit = 50) -> list[JobOut]:
    _recover_quietly(db, redis)
    jobs = db.scalars(
        select(Job)
        .where(Job.user_id == user.id)
        .order_by(Job.created_at.desc(), Job.id)
        .limit(limit)
    ).all()
    return _out(db, list(jobs))


@router.get("/jobs/{job_id}")
def get_job(job_id: uuid.UUID, db: DB, user: CurrentUser, redis: RedisDep) -> JobOut:
    _recover_quietly(db, redis)
    job = db.scalar(select(Job).where(Job.id == job_id, Job.user_id == user.id))
    if job is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Задача не найдена")
    return _out(db, [job])[0]
