"""Voice settings: background bed and transition sounds on or off

Revision ID: 0004_voice_sounds
Revises: 0003_voice_settings
Create Date: 2026-10-14 18:20:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = '0004_voice_sounds'
down_revision: str | None = '0003_voice_settings'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        'voice_settings',
        sa.Column('sounds', sa.Boolean(), server_default=sa.text('true'), nullable=False),
    )


def downgrade() -> None:
    op.drop_column('voice_settings', 'sounds')
