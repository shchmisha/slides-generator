"""Jobs: fencing, heartbeat, queue epoch, one active per project; scenario basis; project brand

Revision ID: 0002_jobs_render
Revises: 0001_initial
Create Date: 2026-10-13 19:21:31.799704
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = '0002_jobs_render'
down_revision: str | None = '0001_initial'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


JSON_DOC = sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql')


def upgrade() -> None:
    # Existing rows get an empty document; the default is dropped afterwards (the app sets it).
    op.add_column('jobs', sa.Column('report', JSON_DOC, server_default=sa.text("'{}'"), nullable=False))
    op.alter_column('jobs', 'report', server_default=None)
    op.add_column('jobs', sa.Column('attempts', sa.Integer(), server_default='0', nullable=False))
    op.add_column('jobs', sa.Column('heartbeat_at', sa.DateTime(timezone=True), nullable=True))
    op.add_column('jobs', sa.Column('enqueued_epoch', sa.String(length=36), nullable=True))
    op.add_column('jobs', sa.Column('enqueued_at', sa.DateTime(timezone=True), nullable=True))
    op.create_index('one_active_job_per_project', 'jobs', ['project_id'], unique=True, postgresql_where=sa.text("status in ('queued', 'running')"), sqlite_where=sa.text("status in ('queued', 'running')"))
    op.add_column('projects', sa.Column('brand', JSON_DOC, nullable=True))
    op.add_column('projects', sa.Column('logo_sha256', sa.String(length=64), nullable=True))
    op.add_column('scenarios', sa.Column('basis', JSON_DOC, server_default=sa.text("'{}'"), nullable=False))
    op.alter_column('scenarios', 'basis', server_default=None)


def downgrade() -> None:
    op.drop_column('scenarios', 'basis')
    op.drop_column('projects', 'logo_sha256')
    op.drop_column('projects', 'brand')
    op.drop_index('one_active_job_per_project', table_name='jobs', postgresql_where=sa.text("status in ('queued', 'running')"), sqlite_where=sa.text("status in ('queued', 'running')"))
    op.drop_column('jobs', 'enqueued_at')
    op.drop_column('jobs', 'enqueued_epoch')
    op.drop_column('jobs', 'heartbeat_at')
    op.drop_column('jobs', 'attempts')
    op.drop_column('jobs', 'report')
