"""sessions: max 1-30, NULL = private

Revision ID: 0013
Revises: 0012
Create Date: 2026-10-31 18:00:00.000000

A private session has no seat limit (max_attendees NULL) and is closed to the public. Seat
limits are 1-30 now (were 1-50): existing larger limits are lowered to 30. Downgrade turns
private sessions into 50-seat ones (the old column is NOT NULL).
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = '0013'
down_revision: str | None = '0012'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_constraint(op.f('ck_sessions_max_attendees'), 'sessions', type_='check')
    op.execute("UPDATE sessions SET max_attendees = 30 WHERE max_attendees > 30")
    op.alter_column('sessions', 'max_attendees', existing_type=sa.Integer(), nullable=True)
    op.create_check_constraint(op.f('ck_sessions_max_attendees'), 'sessions',
                               'max_attendees IS NULL OR max_attendees BETWEEN 1 AND 30')


def downgrade() -> None:
    op.drop_constraint(op.f('ck_sessions_max_attendees'), 'sessions', type_='check')
    op.execute("UPDATE sessions SET max_attendees = 50 WHERE max_attendees IS NULL")
    op.alter_column('sessions', 'max_attendees', existing_type=sa.Integer(), nullable=False)
    op.create_check_constraint(op.f('ck_sessions_max_attendees'), 'sessions', 'max_attendees BETWEEN 1 AND 50')
