"""Access control for the one deployment (DEC-43, resolves OPEN-19).

With `ACCESS_KEY` set, every route needs the key except the public ones below: the
`X-Access-Key` header (scripts, curl) or the cookie that `POST /login` sets (the web pages,
their fetches and WebSocket handshakes, which can't carry custom headers). Empty
`ACCESS_KEY` (local default) leaves everything open, as before.

Public by design:
- `/chat`, `/chat/stream` — the chat widget for a business's site visitors.
- `/health`, `/login`.
- `/twilio/voice` and `/voice/ws` — Twilio can't send our key; they are protected by the
  Twilio signature and a signed stream token instead (`twilio_routes.py`, `ws.py`).

Pure ASGI middleware, not `BaseHTTPMiddleware`: it must also cover WebSockets.
"""

import hashlib
import hmac
import logging

from fastapi import APIRouter, Form
from fastapi.responses import HTMLResponse, PlainTextResponse, RedirectResponse
from starlette.requests import HTTPConnection, Request

from app.config import get_settings

log = logging.getLogger(__name__)
router = APIRouter()

COOKIE = "access"
HEADER = "x-access-key"
PUBLIC_PATHS = {"/chat", "/chat/stream", "/health", "/login", "/twilio/voice", "/voice/ws"}
COOKIE_MAX_AGE_S = 30 * 24 * 3600


def _cookie_value(key: str) -> str:
    """The cookie holds a digest of the key, not the key itself."""
    return hmac.new(key.encode(), b"web-session", hashlib.sha256).hexdigest()


def is_allowed(conn: HTTPConnection, key: str) -> bool:
    if hmac.compare_digest(conn.headers.get(HEADER, "").encode(), key.encode()):
        return True
    return hmac.compare_digest(conn.cookies.get(COOKIE, "").encode(), _cookie_value(key).encode())


class AccessKeyMiddleware:
    def __init__(self, app) -> None:
        self.app = app

    async def __call__(self, scope, receive, send) -> None:
        key = get_settings().access_key
        if scope["type"] not in ("http", "websocket") or not key or scope["path"] in PUBLIC_PATHS:
            return await self.app(scope, receive, send)
        conn = HTTPConnection(scope)
        if is_allowed(conn, key):
            return await self.app(scope, receive, send)

        if scope["type"] == "websocket":
            # closing before accept rejects the handshake (the client sees 403)
            await send({"type": "websocket.close", "code": 4401, "reason": "access key required"})
            return
        path = scope["path"]
        if scope["method"] == "GET" and (path == "/" or path == "/web" or path.startswith("/web/")):
            response = RedirectResponse(f"/login?next={path}", status_code=303)
        else:
            response = PlainTextResponse("access key required", status_code=401)
        await response(scope, receive, send)


_LOGIN_FORM = """<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>Sign in</title></head>
<body style="font-family: system-ui; max-width: 360px; margin: 80px auto; padding: 0 16px">
<form method="post" action="/login">
<input type="hidden" name="next" value="{next}">
<label>Access key<br><input type="password" name="key" autofocus style="width: 100%; padding: 8px"></label>
<p><button type="submit">Sign in</button></p>{error}
</form></body></html>"""


def _safe_next(next_: str) -> str:
    """Only local paths, so the form can't be used as an open redirect."""
    return next_ if next_.startswith("/") and not next_.startswith("//") else "/web/"


@router.get("/login", include_in_schema=False)
def login_form(next: str = "/web/") -> HTMLResponse:
    return HTMLResponse(_LOGIN_FORM.format(next=_html_attr(_safe_next(next)), error=""))


@router.post("/login", include_in_schema=False)
def login(request: Request, key: str = Form(""), next: str = Form("/web/")):
    expected = get_settings().access_key
    target = _safe_next(next)
    if expected and not hmac.compare_digest(key.encode(), expected.encode()):
        log.warning("failed login")
        return HTMLResponse(_LOGIN_FORM.format(next=_html_attr(target), error="<p>Wrong key.</p>"), status_code=401)
    response = RedirectResponse(target, status_code=303)
    if expected:
        response.set_cookie(COOKIE, _cookie_value(expected), max_age=COOKIE_MAX_AGE_S,
                            httponly=True, samesite="lax", secure=_is_https(request))
    return response


def _is_https(request: Request) -> bool:
    proto = request.headers.get("x-forwarded-proto", request.url.scheme).split(",")[0].strip()
    return proto == "https"


def _html_attr(value: str) -> str:
    return value.replace("&", "&amp;").replace('"', "&quot;").replace("<", "&lt;").replace(">", "&gt;")
