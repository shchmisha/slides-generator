"""Projects and their source materials (uploaded files and typed text).

Every query is scoped to the signed-in user, and a project of someone else answers 404 (not
403), so ids of other users' projects cannot even be confirmed to exist. Files are checked by
content before anything is stored; parsing happens later, in the AI worker.

Quotas (files per project, bytes and projects per user) are checked with the user's row locked,
so parallel requests cannot all pass the check and then all insert.
"""

import hashlib
import threading
import unicodedata
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime
from pathlib import PurePosixPath
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException, UploadFile, status
from pydantic import AfterValidator, BaseModel, Field, StringConstraints, ValidationError
from redis import Redis
from sqlalchemy import func, select

from app.agent.settings import DeckSettings, VideoSettings
from app.api.errors import describe_errors
from app.api.validators import encodable, no_control_chars
from app.auth.deps import DB, CurrentUser
from app.config import get_settings
from app.ingest.errors import IngestError
from app.ingest.sniff import sniff
from app.models import Artifact, Project, StoredFile, User
from app.redis_client import get_redis
from app.security import rate_limit
from app.storage import remove_project, upload_path, write_file

router = APIRouter(prefix="/projects", tags=["projects"])

MAX_TEXT_CHARS = 200_000
MAX_NAME_CHARS = 200
# An upload in flight holds up to twice its size in memory (the bytes and a macro-free copy),
# so only a few are read and checked at once; the rest wait briefly, then get 503.
MAX_PARALLEL_UPLOADS = 8
UPLOAD_WAIT_S = 30
_upload_slots = threading.BoundedSemaphore(MAX_PARALLEL_UPLOADS)
Kind = Literal["deck", "video"]
Title = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=200),
    AfterValidator(no_control_chars),
]
RedisDep = Annotated[Redis, Depends(get_redis)]


class ProjectIn(BaseModel):
    title: Title
    kind: Kind


class ProjectPatch(BaseModel):
    title: Title | None = None
    settings: dict[str, Any] | None = None


class FileOut(BaseModel):
    id: uuid.UUID
    filename: str
    kind: str
    size: int
    created_at: datetime
    notes: list[str] = Field(default_factory=list)  # e.g. "macros were removed"


class ProjectOut(BaseModel):
    id: uuid.UUID
    title: str
    kind: Kind
    settings: dict[str, Any]
    brand: dict[str, Any] | None  # the normalised theme; None: the default one
    has_logo: bool
    files: list[FileOut]
    created_at: datetime
    updated_at: datetime


class TextIn(BaseModel):
    title: Annotated[
        str,
        StringConstraints(strip_whitespace=True, min_length=1, max_length=120),
        AfterValidator(no_control_chars),
    ] = "Текст"
    text: Annotated[str, Field(min_length=1, max_length=MAX_TEXT_CHARS), AfterValidator(encodable)]


def owned_project(db: DB, user: CurrentUser, project_id: uuid.UUID) -> Project:
    project = db.scalar(select(Project).where(Project.id == project_id, Project.user_id == user.id))
    if project is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Проект не найден")
    return project


OwnedProject = Annotated[Project, Depends(owned_project)]


def _file_out(f: StoredFile) -> FileOut:
    return FileOut(id=f.id, filename=f.filename, kind=f.kind, size=f.size, created_at=f.created_at)


def _out(p: Project) -> ProjectOut:
    return ProjectOut(
        id=p.id,
        title=p.title,
        kind=p.kind,
        settings=p.settings,
        brand=p.brand,
        has_logo=p.logo_sha256 is not None,
        files=[_file_out(f) for f in p.files],
        created_at=p.created_at,
        updated_at=p.updated_at,
    )


def _validated_settings(kind: str, settings: dict[str, Any]) -> dict[str, Any]:
    model = DeckSettings if kind == "deck" else VideoSettings
    try:
        return model.model_validate(settings).model_dump()
    except ValidationError as exc:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=f"Неверные настройки: {describe_errors(exc.errors())}",
        ) from None


def _display_name(name: str | None) -> str:
    """An uploaded file's original name, for display only: no directories."""
    return _clean_name((name or "").replace("\\", "/").rsplit("/", 1)[-1])


def _clean_name(name: str) -> str:
    """No control or format characters (no NUL, no right-to-left overrides).

    A long name is cut before the extension, so ".docx" stays visible.
    """
    clean = "".join(ch for ch in name if unicodedata.category(ch)[0] != "C").strip()
    if len(clean) > MAX_NAME_CHARS:
        suffix = PurePosixPath(clean).suffix
        suffix = suffix if len(suffix) <= 10 else ""
        clean = clean[: MAX_NAME_CHARS - len(suffix) - 1].rstrip() + "…" + suffix
    return clean or "файл"


def lock_user(db: DB, user: User) -> None:
    """Serialise this user's quota checks until the transaction ends (no-op on SQLite).
    Used for files, projects and jobs alike. FOR NO KEY UPDATE: it excludes other quota
    checks but not the foreign-key checks of rows being inserted for this user elsewhere,
    which would otherwise deadlock with a job saving its results."""
    db.execute(select(User.id).where(User.id == user.id).with_for_update(key_share=True))


@router.post("", status_code=status.HTTP_201_CREATED)
def create_project(body: ProjectIn, db: DB, user: CurrentUser, redis: RedisDep) -> ProjectOut:
    rate_limit.check(redis, rate_limit.PROJECT_PER_USER, str(user.id))
    lock_user(db, user)
    count = db.scalar(select(func.count()).where(Project.user_id == user.id)) or 0
    if count >= get_settings().max_projects_per_user:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            detail=f"У вас уже {count} проектов — удалите ненужные, чтобы создать новый",
        )
    project = Project(
        user_id=user.id,
        title=body.title,
        kind=body.kind,
        settings=_validated_settings(body.kind, {}),
    )
    db.add(project)
    db.commit()
    db.refresh(project)
    return _out(project)


@router.get("")
def list_projects(db: DB, user: CurrentUser) -> list[ProjectOut]:
    projects = db.scalars(
        select(Project).where(Project.user_id == user.id).order_by(Project.updated_at.desc())
    )
    return [_out(p) for p in projects]


@router.get("/{project_id}")
def get_project(project: OwnedProject) -> ProjectOut:
    return _out(project)


@router.patch("/{project_id}")
def update_project(body: ProjectPatch, project: OwnedProject, db: DB) -> ProjectOut:
    if body.title is not None:
        project.title = body.title
    if body.settings is not None:
        project.settings = _validated_settings(project.kind, body.settings)
    db.commit()
    db.refresh(project)
    return _out(project)


@router.delete("/{project_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_project(project: OwnedProject, db: DB, user: CurrentUser) -> None:
    lock_user(db, user)  # waits for this user's uploads in progress to finish
    project_id = project.id
    db.delete(project)
    db.commit()
    remove_project(project_id)


def storage_used(db: DB, user: User) -> int:
    """Uploaded materials and produced files: both stay on the disk until deleted."""
    files = db.scalar(select(func.sum(StoredFile.size)).where(StoredFile.user_id == user.id))
    outputs = db.scalar(select(func.sum(Artifact.size)).where(Artifact.user_id == user.id))
    return (files or 0) + (outputs or 0)


def _check_quota(db: DB, user: User, project: Project, size: int = 0) -> None:
    settings = get_settings()
    count = db.scalar(select(func.count()).where(StoredFile.project_id == project.id)) or 0
    if count >= settings.max_files_per_project:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            detail=f"В проекте уже {count} файлов — это максимум",
        )
    if storage_used(db, user) + size > settings.max_bytes_per_user:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            detail="Место для ваших файлов закончилось — удалите ненужные проекты или файлы",
        )


def _store(db: DB, user: User, project: Project, data: bytes, name: str, kind: str) -> StoredFile:
    lock_user(db, user)
    # The project may have been deleted (another tab) while this upload was being read.
    if db.scalar(select(Project.id).where(Project.id == project.id)) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Проект не найден")
    _check_quota(db, user, project, len(data))
    stored = StoredFile(
        id=uuid.uuid4(),
        project_id=project.id,
        user_id=user.id,
        filename=name,
        kind=kind,
        size=len(data),
        sha256=hashlib.sha256(data).hexdigest(),
    )
    path = upload_path(project.id, stored.id, kind)
    write_file(path, data)
    try:
        db.add(stored)
        project.updated_at = func.now()
        db.commit()
    except BaseException:
        db.rollback()
        path.unlink(missing_ok=True)
        raise
    db.refresh(stored)
    return stored


@contextmanager
def _upload_slot() -> Iterator[None]:
    if not _upload_slots.acquire(timeout=UPLOAD_WAIT_S):
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Сервер занят другими загрузками, попробуйте через минуту",
            headers={"Retry-After": "30"},
        )
    try:
        yield
    finally:
        _upload_slots.release()


@router.post("/{project_id}/files", status_code=status.HTTP_201_CREATED)
def upload_file(
    file: UploadFile, project: OwnedProject, db: DB, user: CurrentUser, redis: RedisDep
) -> FileOut:
    rate_limit.check(redis, rate_limit.UPLOAD_PER_USER, str(user.id))
    _check_quota(db, user, project)  # early answer before reading the body; rechecked locked
    limit = get_settings().max_upload_bytes
    name = _display_name(file.filename)
    with _upload_slot():
        data = file.file.read(limit + 1)
        if len(data) > limit:
            raise HTTPException(
                status.HTTP_413_CONTENT_TOO_LARGE,
                detail=f"Файл больше {limit // (1024 * 1024)} МБ",
            )
        try:
            sniffed = sniff(data, name)
        except IngestError as exc:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)) from None
        # Store the sniffed bytes: for macro-enabled files these have the VBA parts removed.
        stored = _store(db, user, project, sniffed.data, name, sniffed.kind.value)
    out = _file_out(stored)
    out.notes = [note.message for note in sniffed.notes]
    return out


@router.post("/{project_id}/text", status_code=status.HTTP_201_CREATED)
def add_text(
    body: TextIn, project: OwnedProject, db: DB, user: CurrentUser, redis: RedisDep
) -> FileOut:
    rate_limit.check(redis, rate_limit.UPLOAD_PER_USER, str(user.id))
    name = _clean_name(body.title)
    return _file_out(_store(db, user, project, body.text.encode("utf-8"), name, "text"))


@router.delete("/{project_id}/files/{file_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_file(file_id: uuid.UUID, project: OwnedProject, db: DB) -> None:
    stored = db.scalar(
        select(StoredFile).where(StoredFile.id == file_id, StoredFile.project_id == project.id)
    )
    if stored is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Файл не найден")
    path = upload_path(project.id, stored.id, stored.kind)
    db.delete(stored)
    db.commit()
    path.unlink(missing_ok=True)
