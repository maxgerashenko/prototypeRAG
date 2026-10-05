"""Access control (DEC-43, app/access.py): with ACCESS_KEY set, only the chat widget, health,
login and the Twilio entry points are public; the rest needs the header or the login cookie.
No Postgres needed: rejected requests never reach a route, allowed ones hit /login or a
404 path."""

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from app.config import get_settings
from app.main import app

KEY = "k" * 24


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(get_settings(), "access_key", KEY)
    return TestClient(app, follow_redirects=False)


@pytest.mark.parametrize("method,path", [
    ("get", "/businesses"),
    ("get", "/businesses/00000000-0000-0000-0000-000000000000/conversations"),
    ("post", "/businesses/00000000-0000-0000-0000-000000000000/custom-replies"),
    ("get", "/debug/retrieve"),
    ("get", "/twilio/token"),
    ("get", "/nothing-here"),
])
def test_protected_routes_need_the_key(client, method, path):
    assert getattr(client, method)(path).status_code == 401


def test_header_or_cookie_opens_them(client):
    # /nothing-here: past the middleware a 404 proves the request was let through
    assert client.get("/nothing-here", headers={"x-access-key": KEY}).status_code == 404
    assert client.get("/nothing-here", headers={"x-access-key": "wrong"}).status_code == 401

    r = client.post("/login", data={"key": KEY, "next": "/web/chat.html"})
    assert r.status_code == 303 and r.headers["location"] == "/web/chat.html"
    cookie = r.cookies.get("access")
    assert cookie and KEY not in cookie and "httponly" in r.headers["set-cookie"].lower()
    assert TestClient(app, cookies={"access": cookie}).get("/nothing-here").status_code == 404
    assert TestClient(app, cookies={"access": "forged"}).get("/nothing-here").status_code == 401


def test_pages_redirect_to_login(client):
    for path in ("/", "/web/", "/web/call.html"):
        r = client.get(path)
        assert r.status_code == 303 and r.headers["location"] == f"/login?next={path}"
    assert 'name="key"' in client.get("/login?next=/web/").text


def test_wrong_key_and_open_redirect(client):
    assert client.post("/login", data={"key": "nope"}).status_code == 401
    for target in ("https://evil.example", "//evil.example"):
        r = client.post("/login", data={"key": KEY, "next": target})
        assert r.headers["location"] == "/web/"


def test_public_routes_skip_the_key(client):
    # reaching the route at all (any status other than 401) is what matters here
    assert client.post("/chat", json={}).status_code == 422
    assert client.post("/twilio/voice", data={"business_id": "not-a-uuid"}).status_code == 200


def test_browser_websocket_needs_the_key(client):
    with pytest.raises(WebSocketDisconnect) as exc:
        with client.websocket_connect("/voice/browser?business_id=00000000-0000-0000-0000-000000000000"):
            pass
    assert exc.value.code == 4401


def test_no_key_configured_keeps_everything_open(monkeypatch):
    monkeypatch.setattr(get_settings(), "access_key", "")
    assert TestClient(app).get("/nothing-here").status_code == 404
