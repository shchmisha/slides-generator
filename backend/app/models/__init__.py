"""ORM models. Every model module must be imported here so Alembic autogenerate sees it."""

from app.db import Base

__all__ = ["Base"]
