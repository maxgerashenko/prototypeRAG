"""Engine and the `tenant_session` helper — every tenant query runs through it.

Stage 1–2: one DB role, isolation by `WHERE business_id = ...` in queries.
Stage 3: RLS policies read `app.business_id`, so the same code becomes DB-enforced.
"""

import uuid
from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session, sessionmaker

from app.config import get_settings

engine = create_engine(get_settings().database_url, pool_pre_ping=True)
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
