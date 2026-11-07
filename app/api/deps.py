"""Shared API helpers: the business existence check and upstream-outage mapping (review M3)."""

import logging
import uuid
from collections.abc import Iterator
from contextlib import contextmanager

import httpx
import openai
from fastapi import HTTPException
from sqlalchemy import select

from app.db.models import Business
from app.db.session import SessionLocal

log = logging.getLogger(__name__)

# Embedding / LLM server down, timing out or refusing: the caller should retry later (503),
# not see a 500. Matches openai's client errors and the raw httpx transport errors.
UPSTREAM_ERRORS = (openai.APIError, httpx.TransportError)


def require_business(business_id: uuid.UUID) -> uuid.UUID:
    """404 for an id that names no business. Use as a route dependency (or call it): without it
    the foreign key fails on insert and the client gets a 500. Reads across tenants (like
    `/businesses`), so it needs the RLS-exempt path in stage 2."""
    with SessionLocal() as session:
        found = session.scalar(select(Business.id).where(Business.id == business_id))
    if found is None:
        raise HTTPException(status_code=404, detail="business not found")
    return business_id


@contextmanager
def upstream_unavailable(what: str) -> Iterator[None]:
    """Turn an embedder/LLM outage inside the block into HTTP 503 ("<what> unavailable")."""
    try:
        yield
    except UPSTREAM_ERRORS as exc:
        log.warning("%s unavailable: %s", what, exc)
        raise HTTPException(status_code=503, detail=f"{what} unavailable") from exc
