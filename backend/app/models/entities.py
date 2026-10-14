"""Database tables.

Every row that belongs to a user carries `user_id`, and every query in the API filters by it:
ownership is checked on each resource, not only at the project level. Files on disk are named
by row ids only (never by user-supplied names), so a stored path can never escape the data dir.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    JSON,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base

# JSONB on Postgres (indexable, compact), plain JSON elsewhere (SQLite in unit tests).
JsonDoc = JSON().with_variant(JSONB(), "postgresql")


def _now() -> Mapped[datetime]:
    return mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


def _id() -> Mapped[uuid.UUID]:
    return mapped_column(Uuid, primary_key=True, default=uuid.uuid4)


def _owner() -> Mapped[uuid.UUID]:
    return mapped_column(Uuid, ForeignKey("users.id", ondelete="CASCADE"), index=True)


class User(Base):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = _id()
    email: Mapped[str] = mapped_column(String(254), unique=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    created_at: Mapped[datetime] = _now()


class VoiceSettings(Base):
    """The user's ElevenLabs key (encrypted, see app/security/vault.py) and chosen voice. Its own
    table: the ciphertext is read only where a voice is needed, never with the user row."""

    __tablename__ = "voice_settings"

    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    api_key: Mapped[bytes] = mapped_column(LargeBinary)
    voice_id: Mapped[str] = mapped_column(String(64))
    voice_name: Mapped[str] = mapped_column(String(100))
    created_at: Mapped[datetime] = _now()
    updated_at: Mapped[datetime] = _now()


class AuthSession(Base):
    """A signed-in browser. Only the SHA-256 of the cookie token is stored: a leaked database
    does not give working sessions."""

    __tablename__ = "sessions"

    token_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[uuid.UUID] = _owner()
    created_at: Mapped[datetime] = _now()
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)

    user: Mapped[User] = relationship(lazy="joined")


class Project(Base):
    __tablename__ = "projects"
    __table_args__ = (CheckConstraint("kind in ('deck', 'video')", name="project_kind"),)

    id: Mapped[uuid.UUID] = _id()
    user_id: Mapped[uuid.UUID] = _owner()
    title: Mapped[str] = mapped_column(String(200))
    kind: Mapped[str] = mapped_column(String(10))
    settings: Mapped[dict[str, Any]] = mapped_column(JsonDoc, default=dict)
    # Theme for rendering (colors and fonts, normalised from the user's brand.json); None means
    # the default theme. The logo lives on disk; its hash says whether there is one.
    brand: Mapped[dict[str, Any] | None] = mapped_column(JsonDoc, nullable=True)
    logo_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = _now()
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    files: Mapped[list[StoredFile]] = relationship(
        back_populates="project", cascade="all, delete-orphan", order_by="StoredFile.created_at"
    )


class StoredFile(Base):
    """An uploaded file or a typed text, already checked by content (app.ingest.sniff)."""

    __tablename__ = "files"
    __table_args__ = (
        CheckConstraint("kind in ('docx', 'xlsx', 'pptx', 'text')", name="file_kind"),
    )

    id: Mapped[uuid.UUID] = _id()
    project_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    user_id: Mapped[uuid.UUID] = _owner()
    filename: Mapped[str] = mapped_column(String(255))  # for display only
    kind: Mapped[str] = mapped_column(String(10))
    size: Mapped[int] = mapped_column(Integer)
    sha256: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = _now()

    project: Mapped[Project] = relationship(back_populates="files")


ACTIVE_STATUSES = ("queued", "running")
ACTIVE_JOB = "status in ('queued', 'running')"


class Job(Base):
    """A queued unit of work: `plan` (files → scenario, AI queue) or `render` (scenario →
    PPTX/MP4, render queue). Postgres is the source of truth; Redis only carries the message.

    `attempts` is a fencing token: a worker claims the job by raising it and writes results only
    while it still matches, so a worker that was presumed dead cannot overwrite a newer run."""

    __tablename__ = "jobs"
    __table_args__ = (
        CheckConstraint("type in ('plan', 'render')", name="job_type"),
        CheckConstraint(
            "status in ('queued', 'running', 'succeeded', 'failed')", name="job_status"
        ),
        # One generation at a time per project, enforced by the database (no check-then-insert).
        Index(
            "one_active_job_per_project",
            "project_id",
            unique=True,
            postgresql_where=text(ACTIVE_JOB),
            sqlite_where=text(ACTIVE_JOB),
        ),
    )

    id: Mapped[uuid.UUID] = _id()
    project_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    user_id: Mapped[uuid.UUID] = _owner()
    type: Mapped[str] = mapped_column(String(10))
    status: Mapped[str] = mapped_column(String(10), default="queued", index=True)
    progress: Mapped[int] = mapped_column(Integer, default=0)
    stage: Mapped[str] = mapped_column(String(60), default="в очереди")
    error: Mapped[str | None] = mapped_column(Text, nullable=True)  # user-safe message only
    timings: Mapped[dict[str, Any]] = mapped_column(JsonDoc, default=dict)  # stage → ms
    report: Mapped[dict[str, Any]] = mapped_column(JsonDoc, default=dict)  # e.g. layout issues
    attempts: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Which Redis instance holds the message (see app.jobs.queue): a restart loses the queue.
    enqueued_epoch: Mapped[str | None] = mapped_column(String(36), nullable=True)
    enqueued_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    scenario_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("scenarios.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = _now()
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class Scenario(Base):
    """One version of a project's scenario: written by the model, then edited by the user.
    Versions are kept, so a render always names exactly what it was built from."""

    __tablename__ = "scenarios"
    __table_args__ = (
        UniqueConstraint("project_id", "version", name="scenario_version"),
        CheckConstraint("source in ('model', 'user')", name="scenario_source"),
    )

    id: Mapped[uuid.UUID] = _id()
    project_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    user_id: Mapped[uuid.UUID] = _owner()
    version: Mapped[int] = mapped_column(Integer)
    source: Mapped[str] = mapped_column(String(10))
    content: Mapped[dict[str, Any]] = mapped_column(JsonDoc)
    # Shown on the review step: warnings, data notes, checked claims, slides with the numbers.
    report: Mapped[dict[str, Any]] = mapped_column(JsonDoc, default=dict)
    # What the content is checked against (app.agent.review.Basis); loaded only when needed.
    basis: Mapped[dict[str, Any]] = mapped_column(JsonDoc, default=dict, deferred=True)
    created_at: Mapped[datetime] = _now()


class Artifact(Base):
    """A produced file: the deck, the video, or a slide preview."""

    __tablename__ = "artifacts"
    __table_args__ = (CheckConstraint("kind in ('pptx', 'mp4', 'preview')", name="artifact_kind"),)

    id: Mapped[uuid.UUID] = _id()
    project_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    user_id: Mapped[uuid.UUID] = _owner()
    job_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("jobs.id", ondelete="CASCADE"))
    kind: Mapped[str] = mapped_column(String(10))
    position: Mapped[int] = mapped_column(Integer, default=0)  # slide number for previews
    size: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = _now()
