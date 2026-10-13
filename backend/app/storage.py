"""Where files live on the shared /data volume.

Paths are built only from database ids (UUIDs) and a fixed extension: user-supplied file names
are stored in the database for display and never touch the file system, so there is nothing
to traverse with "../" or to collide on.
"""

import shutil
import uuid
from pathlib import Path

from app.config import get_settings

EXTENSIONS = {"docx": ".docx", "xlsx": ".xlsx", "pptx": ".pptx", "text": ".txt"}
ARTIFACT_EXTENSIONS = {"pptx": ".pptx", "mp4": ".mp4", "preview": ".png"}


def project_dir(project_id: uuid.UUID) -> Path:
    return get_settings().data_dir / "projects" / str(project_id)


def upload_path(project_id: uuid.UUID, file_id: uuid.UUID, kind: str) -> Path:
    return project_dir(project_id) / "files" / f"{file_id}{EXTENSIONS[kind]}"


def artifact_path(project_id: uuid.UUID, artifact_id: uuid.UUID, kind: str) -> Path:
    return project_dir(project_id) / "artifacts" / f"{artifact_id}{ARTIFACT_EXTENSIONS[kind]}"


def write_file(path: Path, data: bytes) -> None:
    """Write via a temporary name and rename: a crash never leaves a half-written file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    partial = path.with_suffix(path.suffix + ".part")
    try:
        partial.write_bytes(data)
        partial.replace(path)
    except BaseException:
        partial.unlink(missing_ok=True)  # e.g. disk full: leave no half-written file behind
        raise


def remove_project(project_id: uuid.UUID) -> None:
    shutil.rmtree(project_dir(project_id), ignore_errors=True)
