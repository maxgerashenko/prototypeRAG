"""One crawl per business at a time (issue #196, crawler review L5).

Two crawls of one business running together race on its pages: the second run's
`delete_pages_not_in` deletes pages the first one just stored, and both rewrite the same
facts. `crawl_lock` refuses the second run instead of letting it wait: a crawl takes
minutes, and a caller that started a second one (CLI, a later web button or scheduler)
should hear "already running" right away.

The lock is a Postgres transaction-level advisory lock (`pg_try_advisory_xact_lock`)
held by a transaction that stays open on its own connection for the whole crawl. That
connection does nothing else -- the crawl's own reads and writes still run in their
short `tenant_session` transactions. A transaction lock rather than a session lock
because the cloud uses Neon's transaction-mode pooler (R20): a session lock would stay on
whichever server connection the pooler handed out and could leak to the next client;
an open transaction keeps its server connection, and the lock goes with the transaction
however the process ends (normal exit, exception, kill: the connection drops, Postgres
rolls back). The key is namespaced (`crawl:<business_id>`) so it never collides with the
booking locks, which hash the bare business id or a calendar id.
"""

import uuid
from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import text

from app.db.session import engine


class CrawlInProgress(RuntimeError):
    """Another crawl (or `--only` facts/summary run) of this business holds the lock."""


def crawl_lock_key(business_id: uuid.UUID | str) -> str:
    return f"crawl:{uuid.UUID(str(business_id))}"


@contextmanager
def crawl_lock(business_id: uuid.UUID | str) -> Iterator[None]:
    """Hold the business's crawl lock for the `with` block; raise `CrawlInProgress` at
    once if another run holds it. Released when the block ends, however it ends."""
    key = crawl_lock_key(business_id)
    with engine.connect() as conn, conn.begin():
        got = conn.execute(text("SELECT pg_try_advisory_xact_lock(hashtextextended(:k, 0))"), {"k": key}).scalar()
        if not got:
            raise CrawlInProgress(f"a crawl of business {business_id} is already running; try again when it ends")
        yield
