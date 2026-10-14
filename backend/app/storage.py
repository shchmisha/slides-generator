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


def logo_path(project_id: uuid.UUID) -> Path:
    return project_dir(project_id) / "brand" / "logo.png"


def scratch_dir(job_id: uuid.UUID, attempt: int) -> Path:
    """Scratch space for one attempt of one render, in the spool shared with the renderer (not
    on the data volume: the renderer must not see other users' files). Each attempt has its
    own, so an attempt presumed dead cannot clean up a newer one's files."""
    return get_settings().spool_dir / f"{job_id}-{attempt}"


def remove_scratch(job_id: uuid.UUID) -> None:
    """Every attempt's scratch space of a job."""
    for path in get_settings().spool_dir.glob(f"{job_id}-*"):
        shutil.rmtree(path, ignore_errors=True)


def scratch_dirs() -> list[tuple[uuid.UUID, Path]]:
    """(job id, path) of every scratch dir in the spool, for the startup sweep."""
    found = []
    for path in get_settings().spool_dir.glob("*-*"):
        try:
            found.append((uuid.UUID(path.name.rsplit("-", 1)[0]), path))
        except ValueError:
            continue
    return found


def write_file(path: Path, data: bytes) -> None:
    """Write via a temporary name and rename: a crash never leaves a half-written file, and two
    writers of the same path (two tabs uploading a logo) never share a temporary file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    partial = path.with_name(f".{path.name}.{uuid.uuid4().hex}.part")
    try:
        partial.write_bytes(data)
        partial.replace(path)
    except BaseException:
        partial.unlink(missing_ok=True)  # e.g. disk full: leave no half-written file behind
        raise


def remove_project(project_id: uuid.UUID) -> None:
    shutil.rmtree(project_dir(project_id), ignore_errors=True)
