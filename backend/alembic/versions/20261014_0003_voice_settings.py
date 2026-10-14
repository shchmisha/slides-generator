"""Voice settings: the user's encrypted ElevenLabs key and chosen voice

Revision ID: 0003_voice_settings
Revises: 0002_jobs_render
Create Date: 2026-10-14 17:40:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = '0003_voice_settings'
down_revision: str | None = '0002_jobs_render'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        'voice_settings',
        sa.Column('user_id', sa.Uuid(), nullable=False),
        sa.Column('api_key', sa.LargeBinary(), nullable=False),
        sa.Column('voice_id', sa.String(length=64), nullable=False),
        sa.Column('voice_name', sa.String(length=100), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('user_id'),
    )


def downgrade() -> None:
    op.drop_table('voice_settings')
