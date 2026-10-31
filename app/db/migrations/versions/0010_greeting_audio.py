"""greeting_audio

Revision ID: 0010
Revises: 0009
Create Date: 2026-10-28 21:30:00.000000

V6 (plan/03-voice-channel.md): the standard call greeting's audio is stored per business
(app/voice/greeting_cache.py) instead of being synthesized at every call start. The key is
everything that changes the audio, so a stale row is never played.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = '0010'
down_revision: str | None = '0009'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        'greeting_audio',
        sa.Column('business_id', sa.UUID(), nullable=False),
        sa.Column('text', sa.Text(), nullable=False),
        sa.Column('sample_rate', sa.Integer(), autoincrement=False, nullable=False),
        sa.Column('voice', sa.Text(), nullable=False),
        sa.Column('language', sa.Text(), nullable=False),
        sa.Column('audio', sa.LargeBinary(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['business_id'], ['businesses.id'],
                                name=op.f('fk_greeting_audio_business_id_businesses'), ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('business_id', 'text', 'sample_rate', 'voice', 'language',
                                name=op.f('pk_greeting_audio')),
    )


def downgrade() -> None:
    op.drop_table('greeting_audio')
