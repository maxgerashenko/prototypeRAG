"""Twilio HTTP routes — demo modes A (phone number) and B (Voice SDK browser call, DEC-34).

- POST /twilio/voice   Twilio's voice webhook (a number's or the TwiML App's voice URL).
  Resolves the business and answers with TwiML `<Connect><Stream>` → our media stream
  WebSocket `/voice/ws` (`ws.py`), which runs the call. The caller hears
  ringing while this runs, which is also when the DB wakes up (DEC-17, DEC-35).
- POST /twilio/status  Call status callback: sets `conversations.ended_at` for a call whose
  WebSocket didn't close cleanly (V19). The summary (DEC-24) hooks in here in step 5.
- GET /twilio/token    Voice SDK access token for `web/call.html` (mode B).

Business resolution: a Voice SDK call sends `business_id` as a custom parameter (mode B);
a phone call is matched by the dialled number (`To`) against `businesses.phone_numbers`
(mode A). Both lookups read across tenants before a business is known — like
`/businesses`, they need an RLS-exempt path in stage 3.

Webhooks are checked against `X-Twilio-Signature` (the ngrok URL is public, OPEN-19). The
stream's custom parameters carry an HMAC of business + CallSid, so `/voice/ws` only runs
calls this webhook started; Twilio passes custom parameters only in the stream's `start`
message (query strings on the stream URL are not allowed).
"""

import hashlib
import hmac
import ipaddress
import logging
import uuid
from datetime import datetime

from fastapi import APIRouter, HTTPException, Request, Response
from sqlalchemy import any_, literal, select
from twilio.jwt.access_token import AccessToken
from twilio.jwt.access_token.grants import VoiceGrant
from twilio.request_validator import RequestValidator
from twilio.twiml.voice_response import Connect, VoiceResponse

from app.config import get_settings
from app.db import tenant_session
from app.db.models import Business, Conversation
from app.db.session import SessionLocal

log = logging.getLogger(__name__)
router = APIRouter()

# CallStatus values after which the call is over
FINAL_STATUSES = {"completed", "busy", "failed", "no-answer", "canceled"}
TOKEN_IDENTITY = "web-tester"
TOKEN_TTL_S = 3600


# --- helpers ------------------------------------------------------------------------

def public_url(request: Request) -> str:
    """The URL Twilio used for this request — what its signature covers."""
    base = get_settings().public_base_url.rstrip("/")
    if base:
        query = f"?{request.url.query}" if request.url.query else ""
        return f"{base}{request.url.path}{query}"
    proto = request.headers.get("x-forwarded-proto", request.url.scheme)
    return str(request.url.replace(scheme=proto))


def stream_url(request: Request) -> str:
    """wss:// URL of the media stream endpoint, on the same public host as the webhook."""
    url = public_url(request).split("?", 1)[0]
    scheme, host = url[: len(url) - len(request.url.path)].split("://", 1)
    return f"{'wss' if scheme == 'https' else 'ws'}://{host}/voice/ws"


def stream_token(business_id: uuid.UUID, call_sid: str) -> str:
    key = get_settings().twilio_auth_token.encode()
    return hmac.new(key, f"{business_id}|{call_sid}".encode(), hashlib.sha256).hexdigest()


def check_stream_token(business_id: uuid.UUID, call_sid: str, token: str) -> bool:
    if not get_settings().twilio_validate_signature:
        return True
    if not get_settings().twilio_auth_token:
        return False
    return hmac.compare_digest(stream_token(business_id, call_sid), token or "")


async def twilio_form(request: Request) -> dict[str, str]:
    """Form params of a Twilio webhook, after checking its signature (403 if wrong)."""
    form = {k: str(v) for k, v in (await request.form()).items()}
    settings = get_settings()
    if settings.twilio_validate_signature:
        if not settings.twilio_auth_token:
            log.error("TWILIO_AUTH_TOKEN not set; rejecting Twilio webhook")
            raise HTTPException(403, "Twilio signature check is on but TWILIO_AUTH_TOKEN is not set")
        validator = RequestValidator(settings.twilio_auth_token)
        if not validator.validate(public_url(request), form, request.headers.get("x-twilio-signature", "")):
            log.warning("bad Twilio signature for %s (PUBLIC_BASE_URL must match the URL Twilio calls)",
                        public_url(request))
            raise HTTPException(403, "bad Twilio signature")
    return form


def twiml(response: VoiceResponse) -> Response:
    return Response(content=str(response), media_type="application/xml")


def find_business(form: dict[str, str]) -> uuid.UUID | None:
    """Mode B: `business_id` custom parameter from the Voice SDK page. Mode A: the
    dialled number. None if neither names a known business."""
    with SessionLocal() as session:
        raw = form.get("business_id", "").strip()
        if raw:
            try:
                bid = uuid.UUID(raw)
            except ValueError:
                return None
            return bid if session.get(Business, bid) is not None else None
        to = form.get("To", "").strip()
        if not to:
            return None
        return session.scalars(
            select(Business.id).where(literal(to) == any_(Business.phone_numbers)).order_by(Business.created_at)
        ).first()


# --- routes -------------------------------------------------------------------------

@router.post("/twilio/voice")
async def twilio_voice(request: Request) -> Response:
    form = await twilio_form(request)
    call_sid = form.get("CallSid", "")
    business_id = find_business(form)
    response = VoiceResponse()
    if business_id is None or not call_sid:
        log.warning("twilio call %s: no business for To=%r business_id=%r",
                    call_sid, form.get("To"), form.get("business_id"))
        response.say("Sorry, this number is not in service.")
        response.hangup()
        return twiml(response)

    connect = Connect()
    stream = connect.stream(url=stream_url(request))
    stream.parameter(name="business_id", value=str(business_id))
    stream.parameter(name="caller", value=form.get("From", ""))
    stream.parameter(name="token", value=stream_token(business_id, call_sid))
    response.append(connect)
    log.info("twilio call %s → business %s (from %s)", call_sid, business_id, form.get("From"))
    return twiml(response)


@router.post("/twilio/status")
async def twilio_status(request: Request) -> Response:
    form = await twilio_form(request)
    call_sid, status = form.get("CallSid", ""), form.get("CallStatus", "")
    log.info("twilio call %s status %s (duration %s s)", call_sid, status, form.get("CallDuration"))
    if call_sid and status in FINAL_STATUSES:
        with SessionLocal() as session:
            row = session.execute(
                select(Conversation.id, Conversation.business_id).where(Conversation.call_sid == call_sid)
            ).first()
        if row is not None:
            with tenant_session(row.business_id) as session:
                conversation = session.get(Conversation, row.id)
                if conversation is not None and conversation.ended_at is None:
                    conversation.ended_at = datetime.now().astimezone()
            # step 5 (plan/04-actions.md): the call summary runs here, inside the request (DEC-24)
    return Response(status_code=204)


@router.get("/twilio/token")
def twilio_token(request: Request) -> dict:
    """Access token for the Voice SDK page (mode B). Each token can start billed calls,
    so it's only handed out to the developer's own browser: a loopback client with no
    proxy header — requests through ngrok also come from loopback but carry
    X-Forwarded-For. Real access control comes with OPEN-19 (stage 2)."""
    client = request.client.host if request.client else ""
    try:
        loopback = ipaddress.ip_address(client).is_loopback
    except ValueError:
        loopback = False
    if not loopback or "x-forwarded-for" in request.headers:
        raise HTTPException(403, "the call token is only served to localhost")
    s = get_settings()
    missing = [name for name, value in (
        ("TWILIO_ACCOUNT_SID", s.twilio_account_sid), ("TWILIO_API_KEY_SID", s.twilio_api_key_sid),
        ("TWILIO_API_KEY_SECRET", s.twilio_api_key_secret), ("TWILIO_TWIML_APP_SID", s.twilio_twiml_app_sid),
    ) if not value]
    if missing:
        raise HTTPException(503, f"Twilio not configured: set {', '.join(missing)} in .env")
    token = AccessToken(s.twilio_account_sid, s.twilio_api_key_sid, s.twilio_api_key_secret,
                        identity=TOKEN_IDENTITY, ttl=TOKEN_TTL_S)
    token.add_grant(VoiceGrant(outgoing_application_sid=s.twilio_twiml_app_sid))
    return {"token": token.to_jwt(), "identity": TOKEN_IDENTITY}
