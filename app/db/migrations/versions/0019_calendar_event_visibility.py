"""calendar_events.visibility: the mirror keeps each event's visibility

Revision ID: 0019
Revises: 0018
Create Date: 2026-11-02 15:00:00.000000

The session's private flag is the event's visibility ("private" when true). The sync reads it so
a change made in Google flips the flag. Existing rows get NULL (= not read) until their event is
next synced; NULL never changes a session.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = '0019'
down_revision: str | None = '0018'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column('calendar_events', sa.Column('visibility', sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column('calendar_events', 'visibility')
