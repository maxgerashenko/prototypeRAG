"""Twilio HTTP side of a call (plan/03-voice-channel.md, demo modes A and B).

POST /twilio/voice — Twilio's webhook when a call starts, for both a dialled number
(mode A) and a Voice SDK browser call through our TwiML App (mode B, DEC-34). It picks the
business and answers with TwiML that connects the call's audio to the WebSocket
`/voice/ws` (`ws.py`). The caller still hears ringing while this runs, which also hides
the DB wake-up (DEC-17, R3).

GET /twilio/token — Access Token for the Voice SDK page (`web/call.html`), allowed to call
only our TwiML App.

The business comes from the `business_id` parameter the browser page passes to
`device.connect()` (mode B), else from the dialled number `To` in `businesses.phone_numbers`
(mode A). It goes to the WebSocket as a `<Stream>` `<Parameter>`, so `/voice/ws` never
trusts anything the caller sent directly.

With `TWILIO_AUTH_TOKEN` set (DEC-43): the webhook must carry a valid X-Twilio-Signature,
and the TwiML adds a `token` parameter, an HMAC of business_id + CallSid under the auth
token. `/voice/ws` accepts a stream only with a matching token, so knowing the WebSocket
URL isn't enough to open a call against any business. The TwiML only ever travels from
us to Twilio, so the token isn't visible to the caller.
"""

import hashlib
import hmac
import logging
import uuid

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import Response
from sqlalchemy import any_, select
from twilio.jwt.access_token import AccessToken
from twilio.jwt.access_token.grants import VoiceGrant
from twilio.request_validator import RequestValidator
from twilio.twiml.voice_response import Connect, VoiceResponse

from app.config import get_settings
from app.db.session import SessionLocal
from app.db.models import Business

log = logging.getLogger(__name__)
router = APIRouter()

TOKEN_TTL_S = 3600
BROWSER_IDENTITY = "browser-test"  # stage 1: one tester, no login (OPEN-19)


def public_base_url(request: Request) -> str:
    """The https URL Twilio used to reach us. Behind ngrok/Cloud Run the request arrives as
    plain http, so the scheme comes from X-Forwarded-Proto unless PUBLIC_BASE_URL is set."""
    configured = get_settings().public_base_url.rstrip("/")
    if configured:
        return configured
    proto = request.headers.get("x-forwarded-proto", request.url.scheme).split(",")[0].strip()
    host = request.headers.get("x-forwarded-host") or request.headers.get("host") or request.url.netloc
    return f"{proto}://{host}"


def stream_token(business_id: str, call_sid: str) -> str:
    """Signs a stream's business and call; "" when TWILIO_AUTH_TOKEN isn't set (local)."""
    secret = get_settings().twilio_auth_token
    if not secret:
        return ""
    return hmac.new(secret.encode(), f"{business_id}:{call_sid}".encode(), hashlib.sha256).hexdigest()


def stream_token_ok(business_id: str, call_sid: str, token: str) -> bool:
    expected = stream_token(business_id, call_sid)
    return not expected or hmac.compare_digest(expected.encode(), token.encode())


def _twiml(response: VoiceResponse) -> Response:
    return Response(content=str(response), media_type="application/xml")


def _find_business(business_id: str | None, to: str | None) -> uuid.UUID | None:
    """Reads across tenants by nature (the call isn't tied to a business yet); in stage 3
    this lookup needs a role or function that bypasses RLS."""
    with SessionLocal() as session:
        if business_id:
            try:
                bid = uuid.UUID(business_id)
            except ValueError:
                return None
            return session.scalar(select(Business.id).where(Business.id == bid))
        if to:
            return session.scalar(select(Business.id).where(to == any_(Business.phone_numbers)).limit(1))
    return None


@router.post("/twilio/voice", include_in_schema=False)
async def twilio_voice(request: Request) -> Response:
    form = {k: str(v) for k, v in (await request.form()).items()}
    base = public_base_url(request)

    auth_token = get_settings().twilio_auth_token
    if auth_token:
        url = base + request.url.path + (f"?{request.url.query}" if request.url.query else "")
        signature = request.headers.get("x-twilio-signature", "")
        if not RequestValidator(auth_token).validate(url, form, signature):
            # the usual cause is a URL mismatch: Twilio signs the exact URL set in the console
            log.warning("Twilio signature check failed for %s (set PUBLIC_BASE_URL?)", url)
            raise HTTPException(status_code=403, detail="invalid Twilio signature")

    business_id = _find_business(form.get("business_id"), form.get("To"))
    log.info("Twilio call %s from %s to %s -> business %s",
             form.get("CallSid"), form.get("From"), form.get("To"), business_id)
    response = VoiceResponse()
    if business_id is None:
        response.say("Sorry, this number is not in service.")
        response.hangup()
        return _twiml(response)

    ws_base = "ws" + base.removeprefix("http")  # https://… -> wss://…, http://… -> ws://…
    connect = Connect()
    stream = connect.stream(url=f"{ws_base}/voice/ws")
    stream.parameter(name="business_id", value=str(business_id))
    stream.parameter(name="caller", value=form.get("From", ""))
    token = stream_token(str(business_id), form.get("CallSid", ""))
    if token:
        stream.parameter(name="token", value=token)
    response.append(connect)
    return _twiml(response)


@router.get("/twilio/token", include_in_schema=False)
def twilio_token() -> dict:
    s = get_settings()
    if not (s.twilio_account_sid and s.twilio_api_key_sid and s.twilio_api_key_secret and s.twilio_twiml_app_sid):
        raise HTTPException(
            status_code=503,
            detail="Twilio not configured: set TWILIO_ACCOUNT_SID, TWILIO_API_KEY_SID, "
                   "TWILIO_API_KEY_SECRET and TWILIO_TWIML_APP_SID",
        )
    token = AccessToken(s.twilio_account_sid, s.twilio_api_key_sid, s.twilio_api_key_secret,
                        identity=BROWSER_IDENTITY, ttl=TOKEN_TTL_S)
    token.add_grant(VoiceGrant(outgoing_application_sid=s.twilio_twiml_app_sid))
    return {"token": token.to_jwt(), "identity": BROWSER_IDENTITY}
