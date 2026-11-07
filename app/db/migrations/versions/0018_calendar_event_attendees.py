"""calendar_events.attendees: the mirror keeps each event's attendee list

Revision ID: 0018
Revises: 0017
Create Date: 2026-11-02 14:00:00.000000

The sync reads the attendees of every event so it can tell when a guest was removed or declined
in Google (the booking is cancelled) and when an attendee write failed (it is pushed again).
Existing rows get NULL until their event is next synced.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = '0018'
down_revision: str | None = '0017'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column('calendar_events', sa.Column('attendees', postgresql.JSONB(astext_type=sa.Text()), nullable=True))


def downgrade() -> None:
    op.drop_column('calendar_events', 'attendees')
