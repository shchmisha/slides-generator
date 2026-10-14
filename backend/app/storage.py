"""Where files live on the shared /data volume.

Paths are built only from database ids (UUIDs) and a fixed extension: user-supplied file names
are stored in the database for display and never touch the file system, so there is nothing
to traverse with "../" or to collide on.
"""

import os
import shutil
import stat
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


def _open_in_spool(path: Path) -> int:
    """A descriptor for a regular file the renderer wrote under SPOOL_DIR, opened without
    following a link at any step. The renderer is not trusted: a link it left in the spool
    could point at another project's files on the data volume, which this worker can read."""
    spool = get_settings().spool_dir
    parts = path.relative_to(spool).parts
    if not parts or any(part in ("", ".", "..") for part in parts):
        raise OSError(f"not a spool file: {path}")
    folder = os.open(spool, os.O_RDONLY | os.O_DIRECTORY)
    try:
        for name in parts[:-1]:
            inner = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=folder)
            os.close(folder)
            folder = inner
        # O_NONBLOCK: a FIFO in place of the file must not hang the worker on open.
        fd = os.open(parts[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=folder)
    finally:
        os.close(folder)
    if not stat.S_ISREG(os.fstat(fd).st_mode):
        os.close(fd)
        raise OSError(f"not a regular file: {path}")
    return fd


def read_spool_file(path: Path, max_bytes: int) -> bytes:
    with os.fdopen(_open_in_spool(path), "rb") as source:
        return source.read(max_bytes + 1)[:max_bytes]


def take_from_spool(source: Path, target: Path, max_bytes: int) -> int:
    """Copy a renderer output onto the data volume (a new file at `target`); returns its size.
    The scratch folder, source included, is removed by the caller afterwards."""
    with os.fdopen(_open_in_spool(source), "rb") as reader:
        target.parent.mkdir(parents=True, exist_ok=True)
        size = 0
        writer = target.open("xb")  # a fresh id: never someone else's file
        try:
            with writer:
                while chunk := reader.read(1024 * 1024):
                    size += len(chunk)
                    if size > max_bytes:
                        raise OSError(f"{source.name} is over {max_bytes} bytes")
                    writer.write(chunk)
        except BaseException:
            target.unlink(missing_ok=True)
            raise
    return size


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
