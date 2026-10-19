"""conversations: post-call summary columns

Revision ID: 0006
Revises: 0005
Create Date: 2026-10-19 05:40:00.000000

Stage 1 step 12 (plan/04-actions.md, DEC-24): the status callback stores the call
summary on the conversation. `summarized_at` marks "done", so a repeated callback doesn't
call the model again. `outcome` is limited to the five values in the plan.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = '0006'
down_revision: str | None = '0005'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column('conversations', sa.Column('summary', sa.Text(), nullable=True))
    op.add_column('conversations', sa.Column('intent', sa.Text(), nullable=True))
    op.add_column('conversations', sa.Column('outcome', sa.Text(), nullable=True))
    op.add_column('conversations', sa.Column('follow_up', sa.Boolean(), nullable=True))
    op.add_column('conversations', sa.Column('summarized_at', sa.DateTime(timezone=True), nullable=True))
    op.create_check_constraint(
        op.f('ck_conversations_outcome'), 'conversations',
        "outcome IN ('answered', 'booked', 'message', 'transferred', 'unresolved')",
    )


def downgrade() -> None:
    op.drop_constraint(op.f('ck_conversations_outcome'), 'conversations', type_='check')
    for column in ('summarized_at', 'follow_up', 'outcome', 'intent', 'summary'):
        op.drop_column('conversations', column)
