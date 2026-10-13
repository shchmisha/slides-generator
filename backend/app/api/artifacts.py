"""Produced files: the deck (and later the video) and slide previews. Owner-only, like
everything else; another user's artifact answers 404."""

from __future__ import annotations

import re
import uuid
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, status
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sqlalchemy import select

from app.api.projects import OwnedProject
from app.auth.deps import DB, CurrentUser
from app.models import Artifact, Project
from app.storage import artifact_path

router = APIRouter(tags=["artifacts"])

MEDIA_TYPES = {
    "pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    "mp4": "video/mp4",
    "preview": "image/png",
}
EXTENSIONS = {"pptx": ".pptx", "mp4": ".mp4", "preview": ".png"}
_UNSAFE_NAME = re.compile(r'[\x00-\x1f\x7f\\/:*?"<>|]+')


class ArtifactOut(BaseModel):
    id: uuid.UUID
    job_id: uuid.UUID
    kind: str
    position: int
    size: int
    created_at: datetime
    url: str


def _out(a: Artifact) -> ArtifactOut:
    return ArtifactOut(
        id=a.id,
        job_id=a.job_id,
        kind=a.kind,
        position=a.position,
        size=a.size,
        created_at=a.created_at,
        url=f"/api/artifacts/{a.id}",
    )


def download_name(title: str, artifact: Artifact) -> str:
    """The project title as a file name: no path or reserved characters."""
    base = " ".join(_UNSAFE_NAME.sub(" ", title).split()).strip(" .")[:120] or "result"
    if artifact.kind == "preview":
        base = f"{base} — слайд {artifact.position}"
    return base + EXTENSIONS[artifact.kind]


@router.get("/projects/{project_id}/artifacts")
def list_artifacts(
    project: OwnedProject, db: DB, job_id: Annotated[uuid.UUID | None, Query()] = None
) -> list[ArtifactOut]:
    query = select(Artifact).where(Artifact.project_id == project.id)
    if job_id is not None:
        query = query.where(Artifact.job_id == job_id)
    rows = db.scalars(query.order_by(Artifact.created_at.desc(), Artifact.position))
    return [_out(a) for a in rows]


@router.get("/artifacts/{artifact_id}")
def download(artifact_id: uuid.UUID, db: DB, user: CurrentUser) -> FileResponse:
    row = db.execute(
        select(Artifact, Project.title)
        .join(Project, Project.id == Artifact.project_id)
        .where(Artifact.id == artifact_id, Artifact.user_id == user.id)
    ).first()
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Файл не найден")
    artifact, title = row
    path = artifact_path(artifact.project_id, artifact.id, artifact.kind)
    if not path.is_file():
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Файл не найден")
    inline = artifact.kind == "preview"
    return FileResponse(
        path,
        media_type=MEDIA_TYPES[artifact.kind],
        filename=download_name(title, artifact),
        content_disposition_type="inline" if inline else "attachment",
        # Content never changes for an id; it is private to the user.
        headers={"Cache-Control": "private, max-age=86400, immutable"},
    )
