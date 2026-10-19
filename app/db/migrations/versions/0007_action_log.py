"""action_log

Revision ID: 0007
Revises: 0006
Create Date: 2026-10-19 05:40:00.000000

Stage 1 step 9 (plan/04-actions.md): audit trail written by app/actions/executor.py for
every side-effect tool call, including refused (`needs_confirmation`) and failed ones.
`conversation_id` is nullable; the composite FK ties it to the same business.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = '0007'
down_revision: str | None = '0006'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        'action_log',
        sa.Column('id', sa.UUID(), server_default=sa.text('gen_random_uuid()'), nullable=False),
        sa.Column('business_id', sa.UUID(), nullable=False),
        sa.Column('conversation_id', sa.UUID(), nullable=True),
        sa.Column('tool', sa.Text(), nullable=False),
        sa.Column('arguments', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column('result', sa.Text(), nullable=False),
        sa.Column('status', sa.Text(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.CheckConstraint("status IN ('ok', 'error', 'needs_confirmation')", name=op.f('ck_action_log_status')),
        sa.ForeignKeyConstraint(['business_id', 'conversation_id'], ['conversations.business_id', 'conversations.id'],
                                name=op.f('fk_action_log_business_id_conversations'), ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['business_id'], ['businesses.id'], name=op.f('fk_action_log_business_id_businesses'),
                                ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_action_log')),
    )
    op.create_index(op.f('ix_action_log_business_id'), 'action_log', ['business_id'], unique=False)
    op.create_index(op.f('ix_action_log_conversation_id'), 'action_log', ['conversation_id'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_action_log_conversation_id'), table_name='action_log')
    op.drop_index(op.f('ix_action_log_business_id'), table_name='action_log')
    op.drop_table('action_log')
