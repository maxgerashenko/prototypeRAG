"""sessions, bookings.session_id

Revision ID: 0012
Revises: 0011
Create Date: 2026-10-31 12:00:00.000000

plan/feature-bookings-sessions.md: group sessions (one fixed hour, name, capacity).
Attendees are bookings rows with type 'session' and session_id set. The composite FK keeps
an attendee inside the session's business; NO ACTION because sessions are cancelled softly.
Downgrade fails if bookings with type='session' exist (the old type check rejects them).
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = '0012'
down_revision: str | None = '0011'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        'sessions',
        sa.Column('id', sa.UUID(), server_default=sa.text('gen_random_uuid()'), nullable=False),
        sa.Column('business_id', sa.UUID(), nullable=False),
        sa.Column('starts_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('ends_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('name', sa.Text(), nullable=False),
        sa.Column('max_attendees', sa.Integer(), nullable=False),
        sa.Column('status', sa.Text(), server_default='active', nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('cancelled_at', sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("ends_at = starts_at + interval '1 hour'", name=op.f('ck_sessions_one_hour')),
        sa.CheckConstraint('char_length(btrim(name)) BETWEEN 1 AND 60', name=op.f('ck_sessions_name_length')),
        sa.CheckConstraint('max_attendees BETWEEN 1 AND 50', name=op.f('ck_sessions_max_attendees')),
        sa.CheckConstraint("status IN ('active', 'cancelled')", name=op.f('ck_sessions_status')),
        sa.ForeignKeyConstraint(['business_id'], ['businesses.id'], name=op.f('fk_sessions_business_id_businesses'),
                                ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_sessions')),
        sa.UniqueConstraint('business_id', 'id', name=op.f('uq_sessions_business_id_id')),
    )
    op.create_index(op.f('ix_sessions_business_id'), 'sessions', ['business_id'], unique=False)
    op.create_index('ix_sessions_business_id_starts_at', 'sessions', ['business_id', 'starts_at'], unique=False)
    op.create_index('uq_sessions_business_id_starts_at_active', 'sessions', ['business_id', 'starts_at'], unique=True,
                    postgresql_where=sa.text("status = 'active'"))

    op.add_column('bookings', sa.Column('session_id', sa.UUID(), nullable=True))
    op.create_foreign_key(op.f('fk_bookings_business_id_session_id_sessions'), 'bookings', 'sessions',
                          ['business_id', 'session_id'], ['business_id', 'id'])
    op.create_index('ix_bookings_business_id_session_id', 'bookings', ['business_id', 'session_id'], unique=False)
    op.drop_constraint(op.f('ck_bookings_type'), 'bookings', type_='check')
    op.create_check_constraint(op.f('ck_bookings_type'), 'bookings',
                               "type IN ('table', 'appointment', 'session')")


def downgrade() -> None:
    op.drop_constraint(op.f('ck_bookings_type'), 'bookings', type_='check')
    op.create_check_constraint(op.f('ck_bookings_type'), 'bookings', "type IN ('table', 'appointment')")
    op.drop_index('ix_bookings_business_id_session_id', table_name='bookings')
    op.drop_constraint(op.f('fk_bookings_business_id_session_id_sessions'), 'bookings', type_='foreignkey')
    op.drop_column('bookings', 'session_id')
    op.drop_index('uq_sessions_business_id_starts_at_active', table_name='sessions',
                  postgresql_where=sa.text("status = 'active'"))
    op.drop_index('ix_sessions_business_id_starts_at', table_name='sessions')
    op.drop_index(op.f('ix_sessions_business_id'), table_name='sessions')
    op.drop_table('sessions')
