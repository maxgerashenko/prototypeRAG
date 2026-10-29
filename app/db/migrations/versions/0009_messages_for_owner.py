"""messages_for_owner

Revision ID: 0009
Revises: 0008
Create Date: 2026-10-28 21:30:00.000000

Stage 1 step 9 (plan/04-actions.md, #50): messages callers leave for the business owner --
by the `take_message` tool during a call, or as a voicemail recording when a call transfer
isn't answered (`recording_url`). `conversation_id` is nullable like in `action_log`.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = '0009'
down_revision: str | None = '0008'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        'messages_for_owner',
        sa.Column('id', sa.UUID(), server_default=sa.text('gen_random_uuid()'), nullable=False),
        sa.Column('business_id', sa.UUID(), nullable=False),
        sa.Column('conversation_id', sa.UUID(), nullable=True),
        sa.Column('caller_name', sa.Text(), nullable=True),
        sa.Column('caller_phone', sa.Text(), nullable=True),
        sa.Column('text', sa.Text(), nullable=False),
        sa.Column('urgency', sa.Text(), server_default='normal', nullable=False),
        sa.Column('recording_url', sa.Text(), nullable=True),
        sa.Column('status', sa.Text(), server_default='new', nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.CheckConstraint("urgency IN ('normal', 'urgent')", name=op.f('ck_messages_for_owner_urgency')),
        sa.CheckConstraint("status IN ('new', 'seen')", name=op.f('ck_messages_for_owner_status')),
        sa.ForeignKeyConstraint(['business_id', 'conversation_id'], ['conversations.business_id', 'conversations.id'],
                                name=op.f('fk_messages_for_owner_business_id_conversations'), ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['business_id'], ['businesses.id'],
                                name=op.f('fk_messages_for_owner_business_id_businesses'), ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_messages_for_owner')),
    )
    op.create_index(op.f('ix_messages_for_owner_business_id'), 'messages_for_owner', ['business_id'], unique=False)
    op.create_index(op.f('ix_messages_for_owner_conversation_id'), 'messages_for_owner', ['conversation_id'],
                    unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_messages_for_owner_conversation_id'), table_name='messages_for_owner')
    op.drop_index(op.f('ix_messages_for_owner_business_id'), table_name='messages_for_owner')
    op.drop_table('messages_for_owner')
