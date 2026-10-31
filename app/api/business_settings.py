"""Business settings API for the Settings screen (stage 1, no login yet, DEC-52/DEC-53):

- GET/PUT /businesses/{id}/greeting   the owner's part of the call opening; the spoken text
  is the fixed AI disclosure + this text (just the disclosure when empty), cached as audio on save.
- GET /businesses/{id}/top-questions  the most asked questions, computed on request.
- PUT /businesses/{id}/number         assign one of the Twilio account's numbers.

The number endpoint reads and writes across tenants like app/api/businesses.py and needs
the owner login / an RLS-exempt path in stage 3."""

import logging
import re
import uuid
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import any_, func, select, update

from app.db import tenant_session
from app.db.models import Business, BusinessProfile, Message, message_order
from app.db.session import SessionLocal
from app.insights import questions as insights
from app.llm import embed
from app.voice import greeting_cache, twilio_numbers

log = logging.getLogger(__name__)
router = APIRouter()

EMBED_BATCH = 64


class GreetingIn(BaseModel):
    text: str = ""


class NumberIn(BaseModel):
    number: str


def _greeting_state(business_id: uuid.UUID) -> dict:
    with tenant_session(business_id) as session:
        business = session.get(Business, business_id)
        if business is None:
            raise HTTPException(status_code=404, detail="business not found")
        owner = (business.settings or {}).get("greeting")
        owner = owner.strip() if isinstance(owner, str) else ""
        full = greeting_cache.business_greeting(business, session.get(BusinessProfile, business_id))
    cached, cached_at = greeting_cache.cache_status(business_id, full)
    return {
        "text": owner, "is_default": not owner, "cached": cached, "cached_at": cached_at,
        "max_length": greeting_cache.MAX_GREETING_LEN, "full_text": full,
    }


E164 = re.compile(r"\+[1-9]\d{6,14}")


@router.get("/businesses/{business_id}/greeting")
def get_greeting(business_id: uuid.UUID) -> dict:
    return _greeting_state(business_id)


@router.put("/businesses/{business_id}/greeting")
def put_greeting(business_id: uuid.UUID, body: GreetingIn) -> dict:
    text = body.text.strip()
    if len(text) > greeting_cache.MAX_GREETING_LEN:
        raise HTTPException(
            status_code=422, detail=f"Greeting is too long ({len(text)} of {greeting_cache.MAX_GREETING_LEN} characters)"
        )
    with tenant_session(business_id) as session:
        business = session.get(Business, business_id)
        if business is None:
            raise HTTPException(status_code=404, detail="business not found")
        # a new dict: in-place changes to a JSONB column are not tracked; other keys survive
        settings = {k: v for k, v in (business.settings or {}).items() if k != "greeting"}
        if text:
            settings["greeting"] = text
        business.settings = settings
    cache_error = None
    try:
        greeting_cache.pregenerate(business_id)
    except Exception as exc:  # the text is saved; the first call fills the cache
        log.warning("greeting audio failed for %s", business_id, exc_info=True)
        cache_error = str(exc) or type(exc).__name__
    state = _greeting_state(business_id)
    greeting_cache.prune(business_id, state["full_text"])
    return {**state, "cache_error": cache_error}


@router.get("/businesses/{business_id}/top-questions")
def get_top_questions(
    business_id: uuid.UUID, limit: int = Query(5, ge=1, le=20), days: int = Query(30, ge=1, le=365)
) -> dict:
    since = datetime.now(UTC) - timedelta(days=days)
    with tenant_session(business_id) as session:
        if session.get(Business, business_id) is None:
            raise HTTPException(status_code=404, detail="business not found")
        rows = session.execute(
            select(Message.conversation_id, Message.role, Message.content, Message.created_at)
            .where(
                Message.business_id == business_id,
                Message.role.in_(("user", "assistant")),
                Message.created_at >= since,
            )
            .order_by(Message.conversation_id, *message_order())
        ).all()
    turns = [t for t in insights.pair_turns(rows) if insights.is_question(t.question)]
    result = {
        "total_questions": len(turns), "conversations": len({t.conversation_id for t in turns}),
        "days": days, "questions": [],
    }
    if not turns:
        return result
    try:
        vectors: list[list[float]] = []
        for i in range(0, len(turns), EMBED_BATCH):
            vectors.extend(embed([t.question for t in turns[i : i + EMBED_BATCH]]))
    except Exception as exc:
        log.warning("top questions: embedding failed: %s", exc)
        raise HTTPException(status_code=503, detail="embedding server unavailable") from exc
    result["questions"] = insights.top_questions(turns, vectors, limit=limit)
    return result


@router.put("/businesses/{business_id}/number")
def put_number(business_id: uuid.UUID, body: NumberIn) -> dict:
    """Give the business one of the account's numbers (and only it). A dialled number must
    find exactly one business (app/voice/twilio_routes.py), so it is taken off every other
    business in the same transaction."""
    number = body.number.strip()
    if not E164.fullmatch(number):
        raise HTTPException(status_code=422, detail="number must look like +15551234567")
    with SessionLocal() as session:
        if session.get(Business, business_id) is None:
            raise HTTPException(status_code=404, detail="business not found")
    try:
        on_account = {n["number"] for n in twilio_numbers.account_numbers()}
    except Exception as exc:
        log.warning("could not read the Twilio numbers: %s", exc)
        raise HTTPException(status_code=503, detail=f"could not read the Twilio numbers ({twilio_numbers.safe_error(exc)})") from exc
    if number not in on_account:
        raise HTTPException(status_code=422, detail="number is not on the Twilio account")
    with SessionLocal() as session:
        moved_from = list(session.scalars(
            select(Business.id).where(Business.id != business_id, number == any_(Business.phone_numbers))
        ).all())
        if moved_from:
            session.execute(
                update(Business).where(Business.id.in_(moved_from))
                .values(phone_numbers=func.array_remove(Business.phone_numbers, number))
            )
        session.execute(update(Business).where(Business.id == business_id).values(phone_numbers=[number]))
        session.commit()
    return {"business_id": business_id, "phone_numbers": [number], "moved_from": moved_from}
