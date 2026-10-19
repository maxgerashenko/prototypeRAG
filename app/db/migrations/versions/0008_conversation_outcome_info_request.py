"""conversations.outcome: 'answered' becomes 'info_request'

Revision ID: 0008
Revises: 0007
Create Date: 2026-10-19 06:00:00.000000

The post-call summary labels were standardized (booked, info_request, message,
transferred, unresolved), so the label "answered" is renamed. Existing rows are updated.
"""

from collections.abc import Sequence

from alembic import op

revision: str = '0008'
down_revision: str | None = '0007'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

NEW = "outcome IN ('info_request', 'booked', 'message', 'transferred', 'unresolved')"
OLD = "outcome IN ('answered', 'booked', 'message', 'transferred', 'unresolved')"


def upgrade() -> None:
    op.drop_constraint(op.f('ck_conversations_outcome'), 'conversations', type_='check')
    op.execute("UPDATE conversations SET outcome = 'info_request' WHERE outcome = 'answered'")
    op.create_check_constraint(op.f('ck_conversations_outcome'), 'conversations', NEW)


def downgrade() -> None:
    op.drop_constraint(op.f('ck_conversations_outcome'), 'conversations', type_='check')
    op.execute("UPDATE conversations SET outcome = 'answered' WHERE outcome = 'info_request'")
    op.create_check_constraint(op.f('ck_conversations_outcome'), 'conversations', OLD)
