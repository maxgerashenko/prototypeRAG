"""sessions as Google Calendar events: external_ref, source, any length

Revision ID: 0016
Revises: 0015
Create Date: 2026-11-01 12:00:00.000000

#149 part 2. Google owns which sessions exist and when; Postgres owns seats, the private flag
and attendees. `external_ref` is the Google event id, `source` says where the session was made.
An event typed in Google can have any length, so the 1-hour check goes (sessions made in the app
are still 1 hour, in code) and so does the one-active-session-per-start unique index (two Google
events may start together; the app still refuses it for sessions made in the app).
Downgrade fails if a session is not exactly 1 hour long.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = '0016'
down_revision: str | None = '0015'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column('sessions', sa.Column('external_ref', sa.Text(), nullable=True))
    op.add_column('sessions', sa.Column('source', sa.Text(), nullable=False, server_default='app'))
    op.create_check_constraint(op.f('ck_sessions_source'), 'sessions', "source IN ('app', 'google')")
    op.create_index('uq_sessions_business_id_external_ref', 'sessions', ['business_id', 'external_ref'],
                    unique=True, postgresql_where=sa.text('external_ref IS NOT NULL'))
    op.drop_constraint(op.f('ck_sessions_one_hour'), 'sessions', type_='check')
    op.create_check_constraint(op.f('ck_sessions_times'), 'sessions', 'ends_at > starts_at')
    op.drop_index('uq_sessions_business_id_starts_at_active', table_name='sessions',
                  postgresql_where=sa.text("status = 'active'"))


def downgrade() -> None:
    op.create_index('uq_sessions_business_id_starts_at_active', 'sessions', ['business_id', 'starts_at'],
                    unique=True, postgresql_where=sa.text("status = 'active'"))
    op.drop_constraint(op.f('ck_sessions_times'), 'sessions', type_='check')
    op.create_check_constraint(op.f('ck_sessions_one_hour'), 'sessions', "ends_at = starts_at + interval '1 hour'")
    op.drop_index('uq_sessions_business_id_external_ref', table_name='sessions',
                  postgresql_where=sa.text('external_ref IS NOT NULL'))
    op.drop_constraint(op.f('ck_sessions_source'), 'sessions', type_='check')
    op.drop_column('sessions', 'source')
    op.drop_column('sessions', 'external_ref')
