"""calendar_events, calendar_sync_state

Revision ID: 0015
Revises: 0014
Create Date: 2026-11-01 12:00:00.000000

#165: the app's mirror of the Google Calendar (Google owns time and existence). `business_id`
is nullable (events typed by hand have no owner) and SET NULL on business delete.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = '0015'
down_revision: str | None = '0014'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        'calendar_events',
        sa.Column('id', sa.UUID(), server_default=sa.text('gen_random_uuid()'), nullable=False),
        sa.Column('calendar_id', sa.Text(), nullable=False),
        sa.Column('google_event_id', sa.Text(), nullable=False),
        sa.Column('etag', sa.Text(), nullable=True),
        sa.Column('business_id', sa.UUID(), nullable=True),
        sa.Column('summary', sa.Text(), nullable=True),
        sa.Column('starts_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('ends_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('status', sa.Text(), server_default='confirmed', nullable=False),
        sa.Column('transparency', sa.Text(), server_default='opaque', nullable=False),
        sa.Column('google_updated', sa.DateTime(timezone=True), nullable=True),
        sa.Column('synced_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.CheckConstraint("status IN ('confirmed', 'tentative', 'cancelled')", name=op.f('ck_calendar_events_status')),
        sa.CheckConstraint("transparency IN ('opaque', 'transparent')", name=op.f('ck_calendar_events_transparency')),
        sa.CheckConstraint('ends_at >= starts_at', name=op.f('ck_calendar_events_times')),
        sa.ForeignKeyConstraint(['business_id'], ['businesses.id'],
                                name=op.f('fk_calendar_events_business_id_businesses'), ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_calendar_events')),
        sa.UniqueConstraint('calendar_id', 'google_event_id', name=op.f('uq_calendar_events_calendar_id_google_event_id')),
    )
    op.create_index('ix_calendar_events_calendar_id_starts_at', 'calendar_events', ['calendar_id', 'starts_at'])
    op.create_table(
        'calendar_sync_state',
        sa.Column('calendar_id', sa.Text(), nullable=False),
        sa.Column('sync_token', sa.Text(), nullable=True),
        sa.Column('last_full_sync_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('last_sync_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('last_error', sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint('calendar_id', name=op.f('pk_calendar_sync_state')),
    )


def downgrade() -> None:
    op.drop_table('calendar_sync_state')
    op.drop_index('ix_calendar_events_calendar_id_starts_at', table_name='calendar_events')
    op.drop_table('calendar_events')
