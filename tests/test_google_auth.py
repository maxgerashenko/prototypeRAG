"""How `GoogleCalendarClient` authenticates (#163): OAuth token file (no service account key)
or service account key. Everything is mocked, no network and no Postgres. The live consent and
a real calendar call are on the "Test on Mac" list (plan/03-voice-channel.md)."""

import json
import stat
import sys
import types
from pathlib import Path

import pytest

from app.actions.booking.google_client import SCOPES, CalendarError, GoogleCalendarClient, client_from_settings
from app.config import get_settings

TOKEN = {
    "type": "authorized_user",
    "client_id": "id.apps.googleusercontent.com",
    "client_secret": "secret",
    "refresh_token": "1//refresh",
    "token_uri": "https://oauth2.googleapis.com/token",
}


def test_token_file_gives_oauth_user_credentials(tmp_path):
    f = tmp_path / "token.json"
    f.write_text(json.dumps(TOKEN))
    creds = GoogleCalendarClient(token_file=str(f))._credentials()
    assert creds.refresh_token == "1//refresh"
    assert set(creds.scopes) == set(SCOPES)  # the Calendar scopes, nothing broader


def test_token_file_wins_over_service_account_key(tmp_path, monkeypatch):
    f = tmp_path / "token.json"
    f.write_text(json.dumps(TOKEN))
    from google.oauth2 import service_account

    def boom(*a, **k):
        raise AssertionError("service account key must not be read")

    monkeypatch.setattr(service_account.Credentials, "from_service_account_file", boom)
    GoogleCalendarClient("key.json", str(f))._credentials()


def test_service_account_key_still_works(monkeypatch):
    from google.oauth2 import service_account

    seen = {}
    monkeypatch.setattr(service_account.Credentials, "from_service_account_file",
                        lambda path, scopes: seen.update(path=path, scopes=scopes) or "sa-creds")
    assert GoogleCalendarClient("key.json")._credentials() == "sa-creds"
    assert seen == {"path": "key.json", "scopes": list(SCOPES)}


def test_missing_or_broken_credentials_become_calendar_error(tmp_path):
    with pytest.raises(CalendarError, match="GOOGLE_CALENDAR_TOKEN_FILE"):
        GoogleCalendarClient()._credentials()
    with pytest.raises(CalendarError, match="google_calendar_login"):
        GoogleCalendarClient(token_file=str(tmp_path / "nope.json"))._credentials()
    bad = tmp_path / "bad.json"
    bad.write_text('{"type": "authorized_user"}')  # no refresh token / client
    with pytest.raises(CalendarError, match="google_calendar_login"):
        GoogleCalendarClient(token_file=str(bad))._credentials()


def test_client_from_settings_reads_both_env_vars(monkeypatch):
    monkeypatch.setenv("GOOGLE_CALENDAR_TOKEN_FILE", "/x/token.json")
    monkeypatch.setenv("GOOGLE_CALENDAR_CREDENTIALS_FILE", "/x/key.json")
    get_settings.cache_clear()
    try:
        c = client_from_settings()
        assert (c.token_file, c.credentials_file) == ("/x/token.json", "/x/key.json")
    finally:
        get_settings.cache_clear()


def _login_module():
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
    try:
        import google_calendar_login
        return google_calendar_login
    finally:
        sys.path.pop(0)


def test_login_script_refuses_to_write_inside_the_repo(capsys):
    m = _login_module()
    inside = m.REPO / "gcal-token.json"
    assert m.main(["--client-file", "/tmp/c.json", "--token-file", str(inside)]) == 2
    assert not inside.exists()
    assert "outside the repository" in capsys.readouterr().err


def test_login_script_saves_a_private_token_file(tmp_path, monkeypatch, capsys):
    m = _login_module()
    client = tmp_path / "client.json"
    client.write_text("{}")
    token = tmp_path / "sub" / "token.json"

    class Creds:
        refresh_token = "1//r"

        def to_json(self):
            return json.dumps(TOKEN)

    calls = {}

    class Flow:
        @classmethod
        def from_client_secrets_file(cls, path, scopes):
            calls["scopes"] = scopes
            return cls()

        def run_local_server(self, **kw):
            calls["kw"] = kw
            return Creds()

    fake = types.ModuleType("google_auth_oauthlib.flow")
    fake.InstalledAppFlow = Flow
    monkeypatch.setitem(sys.modules, "google_auth_oauthlib.flow", fake)
    assert m.main(["--client-file", str(client), "--token-file", str(token)]) == 0
    assert json.loads(token.read_text())["refresh_token"] == "1//refresh"
    assert stat.S_IMODE(token.stat().st_mode) == 0o600
    assert calls["scopes"] == list(SCOPES)
    assert calls["kw"]["access_type"] == "offline" and calls["kw"]["prompt"] == "consent"
    assert f"GOOGLE_CALENDAR_TOKEN_FILE={token}" in capsys.readouterr().out
