"""session_guests: one row per person who books; bookings.guest_id, caller_conversation_id, attendee_seen_at

Revision ID: 0017
Revises: 0016
Create Date: 2026-11-02 12:00:00.000000

Guest identity for bookings. A guest is the name the caller gives (`name_key` for matching) plus a
fake `@guest.invalid` email that is written as the Google event attendee and never changes.
`bookings.caller_conversation_id` holds the conversation a booking was made in without a foreign
key, so a join from another business no longer needs the `notes = 'conversation <id>'` text match
(#288). `attendee_seen_at` is set when the sync first sees the attendee in Google. Existing rows
get NULLs; scripts/backfill_guests.py (later step) fills them.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = '0017'
down_revision: str | None = '0016'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        'session_guests',
        sa.Column('id', postgresql.UUID(as_uuid=True), server_default=sa.text('gen_random_uuid()'), nullable=False),
        sa.Column('business_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('display_name', sa.Text(), nullable=False),
        sa.Column('name_key', sa.Text(), nullable=False),
        sa.Column('email', sa.Text(), nullable=False),
        sa.Column('phone', sa.Text(), nullable=True),
        sa.Column('source', sa.Text(), server_default='app', nullable=False),
        sa.Column('merged_into', postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.CheckConstraint("source IN ('app', 'google')", name=op.f('ck_session_guests_source')),
        sa.ForeignKeyConstraint(['business_id'], ['businesses.id'], name=op.f('fk_session_guests_business_id_businesses'), ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_session_guests')),
        sa.UniqueConstraint('business_id', 'id', name=op.f('uq_session_guests_business_id_id')),
        sa.UniqueConstraint('business_id', 'email', name=op.f('uq_session_guests_business_id_email')),
    )
    op.create_foreign_key(
        op.f('fk_session_guests_business_id_session_guests'), 'session_guests', 'session_guests',
        ['business_id', 'merged_into'], ['business_id', 'id'],
    )
    op.create_index(op.f('ix_session_guests_business_id'), 'session_guests', ['business_id'])
    op.create_index('ix_session_guests_business_id_name_key', 'session_guests', ['business_id', 'name_key'])

    op.add_column('bookings', sa.Column('guest_id', postgresql.UUID(as_uuid=True), nullable=True))
    op.add_column('bookings', sa.Column('caller_conversation_id', postgresql.UUID(as_uuid=True), nullable=True))
    op.add_column('bookings', sa.Column('attendee_seen_at', sa.DateTime(timezone=True), nullable=True))
    op.create_foreign_key(
        op.f('fk_bookings_business_id_session_guests'), 'bookings', 'session_guests',
        ['business_id', 'guest_id'], ['business_id', 'id'],
    )
    op.create_index('ix_bookings_business_id_guest_id', 'bookings', ['business_id', 'guest_id'])


def downgrade() -> None:
    op.drop_index('ix_bookings_business_id_guest_id', table_name='bookings')
    op.drop_constraint(op.f('fk_bookings_business_id_session_guests'), 'bookings', type_='foreignkey')
    op.drop_column('bookings', 'attendee_seen_at')
    op.drop_column('bookings', 'caller_conversation_id')
    op.drop_column('bookings', 'guest_id')
    op.drop_index('ix_session_guests_business_id_name_key', table_name='session_guests')
    op.drop_index(op.f('ix_session_guests_business_id'), table_name='session_guests')
    op.drop_table('session_guests')
