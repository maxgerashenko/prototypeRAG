"""conversations: review status, note and time

Revision ID: 0020
Revises: 0019
Create Date: 2026-11-04 10:00:00.000000

A reviewer marks a conversation as verified / in_testing / fixed, with a note. NULL status
means not checked yet.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = '0020'
down_revision: str | None = '0019'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column('conversations', sa.Column('review_status', sa.Text(), nullable=True))
    op.add_column('conversations', sa.Column('review_note', sa.Text(), nullable=True))
    op.add_column('conversations', sa.Column('reviewed_at', sa.DateTime(timezone=True), nullable=True))
    op.create_check_constraint(
        op.f('ck_conversations_review_status'), 'conversations', "review_status IN ('verified', 'in_testing', 'fixed')"
    )


def downgrade() -> None:
    op.drop_constraint(op.f('ck_conversations_review_status'), 'conversations', type_='check')
    op.drop_column('conversations', 'reviewed_at')
    op.drop_column('conversations', 'review_note')
    op.drop_column('conversations', 'review_status')
