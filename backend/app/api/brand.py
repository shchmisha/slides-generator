"""A project's look: colors and fonts (the client's brand.json, sent as JSON) and the logo.

Neither ever reaches the model: they only style the rendered file.
"""

from __future__ import annotations

import hashlib
from typing import Any

from fastapi import APIRouter, HTTPException, UploadFile, status
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sqlalchemy import select

from app.api.projects import OwnedProject, RedisDep
from app.auth.deps import DB, CurrentUser
from app.brand import MAX_LOGO_BYTES, BrandError, BrandIn, normalise_logo, theme_from_brand
from app.models import Project
from app.security import rate_limit
from app.storage import logo_path, write_file

router = APIRouter(prefix="/projects/{project_id}", tags=["brand"])


class BrandOut(BaseModel):
    theme: dict[str, Any]
    warnings: list[str]


@router.put("/brand")
def set_brand(body: BrandIn, project: OwnedProject, db: DB) -> BrandOut:
    theme, warnings = theme_from_brand(body)
    project.brand = theme
    db.commit()
    return BrandOut(theme=theme, warnings=warnings)


@router.delete("/brand", status_code=status.HTTP_204_NO_CONTENT)
def reset_brand(project: OwnedProject, db: DB) -> None:
    project.brand = None
    db.commit()


@router.put("/logo", status_code=status.HTTP_204_NO_CONTENT)
def set_logo(
    file: UploadFile, project: OwnedProject, db: DB, user: CurrentUser, redis: RedisDep
) -> None:
    rate_limit.check(redis, rate_limit.UPLOAD_PER_USER, str(user.id))
    try:
        data = normalise_logo(file.file.read(MAX_LOGO_BYTES + 1))
    except BrandError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)) from None
    _lock(db, project)  # file and hash change together, also with two uploads at once
    write_file(logo_path(project.id), data)
    project.logo_sha256 = hashlib.sha256(data).hexdigest()
    db.commit()


@router.get("/logo")
def get_logo(project: OwnedProject) -> FileResponse:
    path = logo_path(project.id)
    if project.logo_sha256 is None or not path.is_file():
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Логотипа нет")
    return FileResponse(path, media_type="image/png", headers={"Cache-Control": "no-store"})


@router.delete("/logo", status_code=status.HTTP_204_NO_CONTENT)
def delete_logo(project: OwnedProject, db: DB) -> None:
    _lock(db, project)
    project.logo_sha256 = None
    logo_path(project.id).unlink(missing_ok=True)
    db.commit()


def _lock(db: DB, project: Project) -> None:
    db.execute(select(Project.id).where(Project.id == project.id).with_for_update(key_share=True))
