"""The review step: read the scenario the model wrote, edit it, save a new version.

An edit goes through exactly the checks the model's reply went through (app.agent.review), so
the user cannot put a number on a slide that does not come from the data any more than the
model can. Every save is a new version; renders name the version they were built from.
Saving needs the version the edit started from: a stale tab gets 409 instead of silently
overwriting a newer plan or edit.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, HTTPException, Query, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, TypeAdapter
from sqlalchemy import func, select
from sqlalchemy.orm import undefer

from app.agent.review import MAX_SCENARIO_CHARS, Basis, review
from app.api.projects import OwnedProject
from app.auth.deps import DB, CurrentUser
from app.config import get_settings
from app.models import Project, Scenario

router = APIRouter(prefix="/projects/{project_id}", tags=["scenarios"])

_BASIS = TypeAdapter[Any](Basis)
MAX_ERRORS = 30
MAX_VERSION = 2**31 - 1  # the column is a 32-bit integer
Version = Annotated[int, Field(ge=1, le=MAX_VERSION)]


class ScenarioSummary(BaseModel):
    id: uuid.UUID
    version: int
    source: str  # "model" or "user"
    created_at: datetime


class ScenarioOut(ScenarioSummary):
    content: dict[str, Any]
    report: dict[str, Any]


class ScenarioIn(BaseModel):
    base_version: Version
    content: dict[str, Any]


def _out(s: Scenario) -> ScenarioOut:
    return ScenarioOut(
        id=s.id,
        version=s.version,
        source=s.source,
        created_at=s.created_at,
        content=s.content,
        report=s.report,
    )


@router.get("/scenario")
def get_scenario(
    project: OwnedProject,
    db: DB,
    version: Annotated[int | None, Query(ge=1, le=MAX_VERSION)] = None,
) -> ScenarioOut:
    query = select(Scenario).where(Scenario.project_id == project.id)
    if version is not None:
        query = query.where(Scenario.version == version)
    scenario = db.scalar(query.order_by(Scenario.version.desc()).limit(1))
    if scenario is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Сценария ещё нет")
    return _out(scenario)


@router.get("/scenarios")
def list_versions(project: OwnedProject, db: DB) -> list[ScenarioSummary]:
    rows = db.scalars(
        select(Scenario).where(Scenario.project_id == project.id).order_by(Scenario.version.desc())
    )
    return [
        ScenarioSummary(id=s.id, version=s.version, source=s.source, created_at=s.created_at)
        for s in rows
    ]


@router.put(
    "/scenario",
    status_code=status.HTTP_201_CREATED,
    response_model=ScenarioOut,
    responses={422: {"description": "The scenario did not pass the checks; see `errors`"}},
)
def save_scenario(
    body: ScenarioIn, project: OwnedProject, db: DB, user: CurrentUser
) -> ScenarioOut | JSONResponse:
    if len(json.dumps(body.content, ensure_ascii=False)) > MAX_SCENARIO_CHARS:
        raise HTTPException(status.HTTP_413_CONTENT_TOO_LARGE, detail="Сценарий слишком большой")
    # Orders this version number against a plan job finishing at the same moment (the same
    # lock the job takes; NO KEY UPDATE does not block foreign-key checks).
    db.execute(select(Project.id).where(Project.id == project.id).with_for_update(key_share=True))
    latest = db.scalar(
        select(Scenario)
        .options(undefer(Scenario.basis))
        .where(Scenario.project_id == project.id)
        .order_by(Scenario.version.desc())
        .limit(1)
    )
    if latest is None:
        raise HTTPException(status.HTTP_409_CONFLICT, detail="Сначала составьте сценарий")
    if body.base_version != latest.version:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            detail=f"Сценарий уже изменён (сейчас версия {latest.version}): обновите страницу",
        )
    # Edits since the last plan: a new plan starts the count again.
    last_plan = (
        db.scalar(
            select(func.max(Scenario.version)).where(
                Scenario.project_id == project.id, Scenario.source == "model"
            )
        )
        or 0
    )
    edits = latest.version - last_plan
    if edits >= get_settings().max_scenario_versions:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            detail=(
                f"Это уже {edits}-я правка подряд: составьте сценарий заново, "
                "счёт правок начнётся с нуля"
            ),
        )

    checked = review(body.content, _BASIS.validate_python(latest.basis))
    if not checked.ok or checked.content is None:
        return JSONResponse(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            content={
                "detail": "Сценарий не прошёл проверку",
                "errors": checked.errors[:MAX_ERRORS],
            },
        )
    scenario = Scenario(
        id=uuid.uuid4(),
        project_id=project.id,
        user_id=user.id,
        version=latest.version + 1,
        source="user",
        content=checked.content,
        report=checked.report,
        basis=latest.basis,
    )
    db.add(scenario)
    db.commit()
    db.refresh(scenario)
    return _out(scenario)
