"""business domain identity + locations

Revision ID: 0002
Revises: 0001
Create Date: 2026-10-04 00:00:00.000000

Backfill notes: for every existing business with a website, set `domain` from it and
normalize `website` to the site root, then create one default location carrying the
old website URL, the business/profile name and the profile address. The normalization
mirrors app/ingest/identity.py's `domain_of`/`site_root`, inlined below rather than
imported -- a migration's behavior must stay fixed even if that module's implementation
changes later. If two existing businesses would collapse onto the same domain, the
migration refuses (raises) instead of silently merging them; none do in the data seen
while writing this migration (one business, "abathhouse.com").

Round-trip safety (plan/07-knowledge-quality.md A5, added after this migration had
already been applied once): the first version of `downgrade()` just dropped `domain`
and `locations` without restoring `businesses.website` to its pre-upgrade value (the
one WITH a path, e.g. ".../williamsburg") -- `upgrade()` had already overwritten it with
the path-less site root. A downgrade then a re-upgrade therefore rebuilt the default
location's `url` from the root, silently losing the original path. Fixed by having
`downgrade()` restore `website` from the default location's `url` *before* dropping the
table, and by having `upgrade()`'s backfill only touch rows it hasn't already touched
(`domain IS NULL`) and skip creating a second default location for a business that
already has one -- so running upgrade -> downgrade -> upgrade again reproduces the
original state instead of compounding a loss. Verified on a throwaway database copy
(`CREATE DATABASE app_roundtrip TEMPLATE app`), never on the dev DB `app` (downgrade is
destructive and this migration is already applied there).
"""

from collections.abc import Sequence
from urllib.parse import urlparse

import sqlalchemy as sa
from alembic import op

revision: str = '0002'
down_revision: str | None = '0001'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_PLACEHOLDER_NAMES = {"business_name", "(pending)", "pending", ""}


def _domain_of(url: str) -> str | None:
    try:
        parsed = urlparse(url)
    except ValueError:
        return None
    host = parsed.hostname
    if not host:
        return None
    host = host.lower().rstrip(".")
    if host.startswith("www."):
        host = host[len("www.") :]
    return host or None


def _site_root(url: str) -> str | None:
    try:
        parsed = urlparse(url)
    except ValueError:
        return None
    host = parsed.hostname
    if not host:
        return None
    netloc = host.lower()
    if parsed.port:
        netloc = f"{netloc}:{parsed.port}"
    scheme = parsed.scheme or "https"
    return f"{scheme}://{netloc}/"


def upgrade() -> None:
    op.add_column('businesses', sa.Column('domain', sa.Text(), nullable=True))

    op.create_table(
        'locations',
        sa.Column('id', sa.UUID(), server_default=sa.text('gen_random_uuid()'), nullable=False),
        sa.Column('business_id', sa.UUID(), nullable=False),
        sa.Column('name', sa.Text(), nullable=True),
        sa.Column('url', sa.Text(), nullable=True),
        sa.Column('address', sa.Text(), nullable=True),
        sa.Column('timezone', sa.Text(), nullable=True),
        sa.Column('is_default', sa.Boolean(), server_default=sa.text('false'), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['business_id'], ['businesses.id'], name=op.f('fk_locations_business_id_businesses'), ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_locations')),
        sa.UniqueConstraint('business_id', 'id', name=op.f('uq_locations_business_id_id')),
        sa.UniqueConstraint('business_id', 'url', name=op.f('uq_locations_business_id_url')),
    )
    op.create_index(op.f('ix_locations_business_id'), 'locations', ['business_id'], unique=False)
    op.create_index(
        'uq_locations_business_id_default', 'locations', ['business_id'],
        unique=True, postgresql_where=sa.text('is_default'),
    )

    # --- backfill existing businesses ------------------------------------------------
    # domain IS NULL: only rows this migration hasn't already touched -- on a plain
    # first-time upgrade that's every row (the column was just added NULL for all of
    # them), but it also makes a second upgrade (e.g. after the round-trip test's
    # downgrade) a no-op for businesses it already backfilled, instead of re-deriving
    # `domain`/`website` from data another run may have already changed (A5).
    bind = op.get_bind()
    rows = bind.execute(sa.text(
        "SELECT b.id AS id, b.name AS business_name, b.website AS website, "
        "       p.name AS profile_name, p.address AS address "
        "FROM businesses b LEFT JOIN business_profile p ON p.business_id = b.id "
        "WHERE b.website IS NOT NULL AND b.domain IS NULL"
    )).fetchall()

    by_domain: dict[str, list] = {}
    for row in rows:
        domain = _domain_of(row.website)
        if domain:
            by_domain.setdefault(domain, []).append(row)

    conflicts = {domain: rs for domain, rs in by_domain.items() if len(rs) > 1}
    if conflicts:
        detail = "; ".join(
            f"{domain} shared by {[str(r.id) for r in rs]}" for domain, rs in conflicts.items()
        )
        raise RuntimeError(
            "Migration 0002 backfill found businesses that would collapse onto the "
            f"same domain -- resolve manually before re-running this migration: {detail}"
        )

    for row in rows:
        domain = _domain_of(row.website)
        root = _site_root(row.website)
        if domain and root:
            bind.execute(
                sa.text("UPDATE businesses SET domain = :domain, website = :root WHERE id = :id"),
                {"domain": domain, "root": root, "id": row.id},
            )

        name = row.profile_name
        if not name or name.strip().lower() in _PLACEHOLDER_NAMES:
            name = row.business_name
        if not name or name.strip().lower() in _PLACEHOLDER_NAMES:
            name = "Main"
        # defensive idempotency (A5): never create a second default location for a
        # business that already has one -- shouldn't happen on a fresh locations table,
        # but keeps this backfill safe to reason about if it's ever re-run without an
        # intervening downgrade.
        has_default = bind.execute(
            sa.text("SELECT 1 FROM locations WHERE business_id = :bid AND is_default LIMIT 1"),
            {"bid": row.id},
        ).first()
        if has_default is None:
            bind.execute(
                sa.text(
                    "INSERT INTO locations (business_id, name, url, address, is_default) "
                    "VALUES (:bid, :name, :url, :address, true)"
                ),
                {"bid": row.id, "name": name, "url": row.website, "address": row.address},
            )

    op.create_unique_constraint(op.f('uq_businesses_domain'), 'businesses', ['domain'])


def downgrade() -> None:
    # Restore businesses.website to its pre-upgrade value (with path, e.g.
    # ".../williamsburg") from the default location's url *before* dropping `locations`
    # -- upgrade() overwrote website with the path-less site root, so the default
    # location's url is the only surviving copy of the original value (A5).
    bind = op.get_bind()
    bind.execute(sa.text(
        "UPDATE businesses b SET website = l.url "
        "FROM locations l "
        "WHERE l.business_id = b.id AND l.is_default AND l.url IS NOT NULL"
    ))

    op.drop_constraint(op.f('uq_businesses_domain'), 'businesses', type_='unique')
    op.drop_index('uq_locations_business_id_default', table_name='locations')
    op.drop_index(op.f('ix_locations_business_id'), table_name='locations')
    op.drop_table('locations')
    op.drop_column('businesses', 'domain')
