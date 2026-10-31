"""booking_rules, bookings

Revision ID: 0011
Revises: 0010
Create Date: 2026-10-28 21:30:00.000000

Stage 1 step 11 (plan/04-actions.md, #52): internal booking backend.
The composite FK keeps a booking's conversation inside the same business; `SET NULL (col)`
nulls only that column on delete, never `business_id`.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = '0011'
down_revision: str | None = '0010'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        'booking_rules',
        sa.Column('business_id', sa.UUID(), nullable=False),
        sa.Column('slot_minutes', sa.Integer(), server_default='60', nullable=False),
        sa.Column('capacity', sa.Integer(), server_default='1', nullable=False),
        sa.Column('lead_time_minutes', sa.Integer(), server_default='0', nullable=False),
        sa.Column('max_party_size', sa.Integer(), nullable=True),
        sa.Column('hours', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column('service_durations', postgresql.JSONB(astext_type=sa.Text()), server_default='{}', nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.CheckConstraint('slot_minutes > 0 AND capacity > 0 AND lead_time_minutes >= 0',
                           name=op.f('ck_booking_rules_positive')),
        sa.ForeignKeyConstraint(['business_id'], ['businesses.id'],
                                name=op.f('fk_booking_rules_business_id_businesses'), ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('business_id', name=op.f('pk_booking_rules')),
    )
    op.create_table(
        'bookings',
        sa.Column('id', sa.UUID(), server_default=sa.text('gen_random_uuid()'), nullable=False),
        sa.Column('business_id', sa.UUID(), nullable=False),
        sa.Column('conversation_id', sa.UUID(), nullable=True),
        sa.Column('type', sa.Text(), nullable=False),
        sa.Column('starts_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('ends_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('party_size', sa.Integer(), nullable=True),
        sa.Column('service', sa.Text(), nullable=True),
        sa.Column('name', sa.Text(), nullable=False),
        sa.Column('phone', sa.Text(), nullable=True),
        sa.Column('notes', sa.Text(), nullable=True),
        sa.Column('status', sa.Text(), server_default='confirmed', nullable=False),
        sa.Column('external_ref', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('cancelled_at', sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("type IN ('table', 'appointment')", name=op.f('ck_bookings_type')),
        sa.CheckConstraint("status IN ('confirmed', 'cancelled')", name=op.f('ck_bookings_status')),
        sa.CheckConstraint('ends_at > starts_at', name=op.f('ck_bookings_times')),
        sa.ForeignKeyConstraint(['business_id', 'conversation_id'], ['conversations.business_id', 'conversations.id'],
                                name=op.f('fk_bookings_business_id_conversations'), ondelete='SET NULL (conversation_id)'),
        sa.ForeignKeyConstraint(['business_id'], ['businesses.id'], name=op.f('fk_bookings_business_id_businesses'),
                                ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_bookings')),
        sa.UniqueConstraint('business_id', 'id', name=op.f('uq_bookings_business_id_id')),
    )
    op.create_index(op.f('ix_bookings_business_id'), 'bookings', ['business_id'], unique=False)
    op.create_index('ix_bookings_business_id_starts_at', 'bookings', ['business_id', 'starts_at'], unique=False)


def downgrade() -> None:
    op.drop_index('ix_bookings_business_id_starts_at', table_name='bookings')
    op.drop_index(op.f('ix_bookings_business_id'), table_name='bookings')
    op.drop_table('bookings')
    op.drop_table('booking_rules')
