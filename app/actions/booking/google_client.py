"""Thin Google Calendar REST client (DEC-48) behind a small protocol, so the backend is
tested against a fake and only this file knows Google's wire format.

Pilot auth: a service account whose key file is `GOOGLE_CALENDAR_CREDENTIALS_FILE`; the
owner shares their calendar with its email ("make changes to events"). No OAuth. The
credentials load on first use, so importing this module needs no key.
"""

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

API = "https://www.googleapis.com/calendar/v3"
SCOPES = (
    "https://www.googleapis.com/auth/calendar.events",
    "https://www.googleapis.com/auth/calendar.freebusy",
)
TIMEOUT_S = 10  # a caller is waiting on the line


class CalendarError(Exception):
    """Google could not be reached or refused the call. The message is for logs, not callers."""


class SyncTokenExpired(CalendarError):
    """410 Gone: the sync token is too old; drop it and run a full sync."""


class PreconditionFailed(CalendarError):
    """412: the event changed since the etag we hold (events.patch with If-Match)."""


class EventExists(CalendarError):
    """409: an event with the caller-chosen id already exists (a retried insert)."""


@dataclass
class EventsPage:
    events: list[dict]
    next_page_token: str | None = None
    next_sync_token: str | None = None  # only on the last page


class CalendarClient(Protocol):
    def busy(self, calendar_id: str, start: datetime, end: datetime) -> list[tuple[datetime, datetime]]:
        """Busy intervals (tz-aware) of the calendar inside [start, end)."""
        ...

    def insert_event(self, calendar_id: str, body: dict) -> str:
        """Create an event; returns its id."""
        ...

    def delete_event(self, calendar_id: str, event_id: str) -> None:
        """Delete an event; an already deleted one is not an error."""
        ...


class CalendarEventsClient(Protocol):
    """What the sync (#165) and session patching (#149) need on top of `CalendarClient`."""

    def list_events(self, calendar_id: str, *, sync_token: str | None = None, time_min: datetime | None = None,
                    time_max: datetime | None = None, page_token: str | None = None) -> EventsPage:
        """events.list with singleEvents, showDeleted, eventTypes=default. With `sync_token`
        no time window is sent (Google refuses both). Raises SyncTokenExpired on 410."""
        ...

    def get_event(self, calendar_id: str, event_id: str) -> dict | None:
        """events.get; None if it does not exist."""
        ...

    def patch_event(self, calendar_id: str, event_id: str, body: dict, etag: str | None = None) -> str:
        """events.patch, with If-Match when `etag` is given; returns the new etag.
        Raises PreconditionFailed on 412."""
        ...


class GoogleCalendarClient:
    def __init__(self, credentials_file: str):
        self.credentials_file = credentials_file
        self._session = None

    def _http(self):
        if self._session is None:
            if not self.credentials_file:
                raise CalendarError("GOOGLE_CALENDAR_CREDENTIALS_FILE is not set")
            from google.auth.transport.requests import AuthorizedSession
            from google.oauth2 import service_account

            try:
                creds = service_account.Credentials.from_service_account_file(
                    self.credentials_file, scopes=list(SCOPES)
                )
            except (OSError, ValueError) as exc:
                raise CalendarError(f"cannot load service account key: {exc}") from exc
            self._session = AuthorizedSession(creds)
        return self._session

    def _call(self, method: str, url: str, **kw):
        try:
            resp = self._http().request(method, url, timeout=TIMEOUT_S, **kw)
        except CalendarError:
            raise
        except Exception as exc:  # network, token refresh
            raise CalendarError(f"{method} {url}: {exc}") from exc
        return resp

    def busy(self, calendar_id, start, end):
        resp = self._call(
            "POST", f"{API}/freeBusy",
            json={"timeMin": start.isoformat(), "timeMax": end.isoformat(), "items": [{"id": calendar_id}]},
        )
        if resp.status_code != 200:
            raise CalendarError(f"freeBusy {resp.status_code}: {resp.text[:200]}")
        cal = resp.json().get("calendars", {}).get(calendar_id, {})
        if cal.get("errors"):  # e.g. notFound: the calendar is not shared with the service account
            raise CalendarError(f"freeBusy for {calendar_id}: {cal['errors']}")
        return [
            (datetime.fromisoformat(b["start"].replace("Z", "+00:00")),
             datetime.fromisoformat(b["end"].replace("Z", "+00:00")))
            for b in cal.get("busy", [])
        ]

    def insert_event(self, calendar_id, body):
        resp = self._call("POST", f"{API}/calendars/{_quote(calendar_id)}/events", json=body)
        if resp.status_code == 409:
            raise EventExists(f"events.insert: event {body.get('id')} exists")
        if resp.status_code not in (200, 201):
            raise CalendarError(f"events.insert {resp.status_code}: {resp.text[:200]}")
        return resp.json()["id"]

    def list_events(self, calendar_id, *, sync_token=None, time_min=None, time_max=None, page_token=None):
        params: dict = {"singleEvents": "true", "showDeleted": "true", "eventTypes": "default", "maxResults": 250}
        if sync_token:
            params["syncToken"] = sync_token
        else:
            if time_min:
                params["timeMin"] = time_min.isoformat()
            if time_max:
                params["timeMax"] = time_max.isoformat()
        if page_token:
            params["pageToken"] = page_token
        resp = self._call("GET", f"{API}/calendars/{_quote(calendar_id)}/events", params=params)
        if resp.status_code == 410:
            raise SyncTokenExpired("events.list 410")
        if resp.status_code != 200:
            raise CalendarError(f"events.list {resp.status_code}: {resp.text[:200]}")
        data = resp.json()
        return EventsPage(data.get("items", []), data.get("nextPageToken"), data.get("nextSyncToken"))

    def get_event(self, calendar_id, event_id):
        resp = self._call("GET", f"{API}/calendars/{_quote(calendar_id)}/events/{_quote(event_id)}")
        if resp.status_code == 404:
            return None
        if resp.status_code != 200:
            raise CalendarError(f"events.get {resp.status_code}: {resp.text[:200]}")
        return resp.json()

    def patch_event(self, calendar_id, event_id, body, etag=None):
        resp = self._call(
            "PATCH", f"{API}/calendars/{_quote(calendar_id)}/events/{_quote(event_id)}", json=body,
            headers={"If-Match": etag} if etag else None,
        )
        if resp.status_code == 412:
            raise PreconditionFailed(f"events.patch {event_id}: etag changed")
        if resp.status_code != 200:
            raise CalendarError(f"events.patch {resp.status_code}: {resp.text[:200]}")
        return resp.json().get("etag", "")

    def delete_event(self, calendar_id, event_id):
        resp = self._call("DELETE", f"{API}/calendars/{_quote(calendar_id)}/events/{_quote(event_id)}")
        if resp.status_code not in (200, 204, 404, 410):
            raise CalendarError(f"events.delete {resp.status_code}: {resp.text[:200]}")


def _quote(value: str) -> str:
    from urllib.parse import quote

    return quote(value, safe="")
