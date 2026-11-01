"""sessions: private is its own flag, max_attendees is always set

Revision ID: 0014
Revises: 0013
Create Date: 2026-11-01 10:00:00.000000

Private (closed to the public: the assistant never books into it) no longer replaces the seat
limit. Sessions that were private (max_attendees NULL) become private=true with 30 seats, and
the limit is NOT NULL again. Downgrade turns private sessions back into max_attendees NULL.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = '0014'
down_revision: str | None = '0013'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column('sessions', sa.Column('private', sa.Boolean(), nullable=False, server_default=sa.false()))
    op.execute("UPDATE sessions SET private = true, max_attendees = 30 WHERE max_attendees IS NULL")
    op.drop_constraint(op.f('ck_sessions_max_attendees'), 'sessions', type_='check')
    op.alter_column('sessions', 'max_attendees', existing_type=sa.Integer(), nullable=False)
    op.create_check_constraint(op.f('ck_sessions_max_attendees'), 'sessions', 'max_attendees BETWEEN 1 AND 30')


def downgrade() -> None:
    op.drop_constraint(op.f('ck_sessions_max_attendees'), 'sessions', type_='check')
    op.alter_column('sessions', 'max_attendees', existing_type=sa.Integer(), nullable=True)
    op.execute("UPDATE sessions SET max_attendees = NULL WHERE private")
    op.create_check_constraint(op.f('ck_sessions_max_attendees'), 'sessions',
                               'max_attendees IS NULL OR max_attendees BETWEEN 1 AND 30')
    op.drop_column('sessions', 'private')
