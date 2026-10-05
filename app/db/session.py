"""Engine and the `tenant_session` helper — every tenant query runs through it.

Stage 1–2: one DB role, isolation by `WHERE business_id = ...` in queries.
Stage 3: RLS policies read `app.business_id`, so the same code becomes DB-enforced.
"""

import uuid
from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import create_engine, make_url, text
from sqlalchemy.orm import Session, sessionmaker

from app.config import get_settings

_LOCAL_HOSTS = {None, "localhost", "127.0.0.1", "::1"}


def connect_args(url: str) -> dict:
    """psycopg connect args for `url` (R20).

    - `sslmode=require` for any non-local database unless the URL sets sslmode itself:
      libpq's default (`prefer`) silently falls back to plaintext.
    - No server-side prepared statements through Neon's `-pooler` host. psycopg prepares
      a query after 5 runs; in PgBouncer transaction mode the next transaction may land on
      another server connection, so unless the pooler tracks them
      (`max_prepared_statements`) queries fail with "prepared statement _pg3_0 does not
      exist / already exists" (reproduced with a local PgBouncer 1.22).
    """
    u = make_url(url)
    args: dict = {}
    if "sslmode" not in u.query and u.host not in _LOCAL_HOSTS:
        args["sslmode"] = "require"
    if "-pooler." in (u.host or ""):
        args["prepare_threshold"] = None
    return args


_settings = get_settings()
engine = create_engine(
    _settings.database_url,
    pool_pre_ping=True,  # Neon suspends idle computes; drop dead connections before use
    pool_size=_settings.db_pool_size,
    max_overflow=_settings.db_max_overflow,
    connect_args=connect_args(_settings.database_url),
)
SessionLocal = sessionmaker(engine, expire_on_commit=False)


@contextmanager
def tenant_session(business_id: uuid.UUID | str) -> Iterator[Session]:
    """Open one transaction scoped to a business.

    Sets `app.business_id` with `set_config(..., is_local => true)` — the parameterized
    form of `SET LOCAL`: it is reset at transaction end, so it never leaks to the next
    user of a pooled connection (R20). Commits on success, rolls back on error.
    """
    bid = str(uuid.UUID(str(business_id)))  # reject anything that isn't a UUID
    with SessionLocal() as session, session.begin():
        session.execute(text("SELECT set_config('app.business_id', :bid, true)"), {"bid": bid})
        yield session
