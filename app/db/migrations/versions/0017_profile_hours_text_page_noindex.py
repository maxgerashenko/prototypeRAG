"""business_profile.opening_hours as text; pages.noindex

Revision ID: 0017
Revises: 0016
Create Date: 2026-11-07 12:00:00.000000

#295: the crawler's LLM extraction yields opening hours as a string and the prompt prints it
as is, so the JSONB column was text in practice (a JSON string). It is now plain text. Existing
values are unwrapped (`#>> '{}'`: a JSON string becomes its text, any other JSON its JSON text).
#289: whether a page said noindex on its last fetch, so organize can keep it non-retrievable on
runs that don't re-fetch it. Existing pages default to false (they were organized without the flag).
Downgrade turns each hours text back into a JSON string.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = '0017'
down_revision: str | None = '0016'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.alter_column('business_profile', 'opening_hours', existing_type=sa.dialects.postgresql.JSONB(),
                    type_=sa.Text(), existing_nullable=True, postgresql_using="opening_hours #>> '{}'")
    op.add_column('pages', sa.Column('noindex', sa.Boolean(), nullable=False, server_default=sa.text('false')))


def downgrade() -> None:
    op.drop_column('pages', 'noindex')
    op.alter_column('business_profile', 'opening_hours', existing_type=sa.Text(),
                    type_=sa.dialects.postgresql.JSONB(), existing_nullable=True,
                    postgresql_using="to_jsonb(opening_hours)")
