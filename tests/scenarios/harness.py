"""Runner for the LLM-driven booking scenarios (tests/scenarios/README.md).

A caller model (gemma, from a persona prompt) talks to the real bot over `POST /chat`
(FastAPI TestClient, the real LLM, prompt, tools and Postgres). The verdict comes from
database assertions only. Debug one scenario:

    uv run python -m tests.scenarios.harness <scenario-id>
"""

import contextlib
import os
import re
import sys
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx
import yaml
from fastapi.testclient import TestClient
from openai import OpenAI
from sqlalchemy import delete, func, select

from app.actions.booking import rules as R
from app.actions.booking import session_events as G
from app.actions.booking import sessions
from app.actions.booking import tools as booking_tools
from app.actions.booking.internal import BookingRequest, InternalBackend
from app.config import get_settings
from app.db import tenant_session
from app.rag import answer as answer_
from app.rag.answer import claims_booking
from app.db.models import ActionLog, Booking, BookingRule, Business, BusinessProfile, GroupSession, SessionGuest

CALLER_MODEL = os.environ.get("SCENARIO_CALLER_MODEL", "google/gemma-4-12b")
TZ = "America/New_York"
TOOLS = ["list_sessions", "join_session", "create_booking", "check_availability", "cancel_booking"]
YAML = Path(__file__).with_name("scenarios.yaml")
END = "<END>"


def load() -> tuple[str, list[dict]]:
    data = yaml.safe_load(YAML.read_text())
    return data["common"].strip(), data["scenarios"]


def retries() -> int:
    return max(0, int(os.environ.get("SCENARIO_RETRIES", "1")))


# --- environment ---------------------------------------------------------------------------------

def lm_missing() -> str | None:
    """None when LM Studio is up and lists the chat and caller models, else why not."""
    s = get_settings()
    try:
        ids = {m["id"] for m in httpx.get(s.llm_base_url.rstrip("/") + "/models", timeout=3).json()["data"]}
    except Exception as exc:
        return f"LM Studio not reachable at {s.llm_base_url} ({type(exc).__name__})"
    missing = [m for m in (s.llm_model, CALLER_MODEL) if m not in ids]
    return f"models not listed by LM Studio: {missing}" if missing else None


@contextlib.contextmanager
def patched_app(business_ids: set):
    """Internal booking backend, no Google, and `list_open` (tool and prompt) limited to the scenario's own sessions
    (the demo rule shows every business's sessions - the dev DB holds the real calendar's)."""
    settings = get_settings()
    saved = (settings.booking_backend, booking_tools.backend, G._client, sessions.list_open, answer_.list_open)
    orig = sessions.list_open

    def scoped(business_id, day=None, now=None, limit=10):
        rows = orig(business_id, day, now, 500)
        with tenant_session(business_id) as s:
            mine = {sessions.session_ref(i) for i in s.scalars(
                select(GroupSession.id).where(GroupSession.business_id == business_id))}
        return [r for r in rows if r["ref"] in mine][:limit]

    settings.booking_backend = "internal"
    answer_.list_open = scoped  # the prompt's open-session list (now_note) imports it by name
    booking_tools.backend = InternalBackend()
    G._client = None
    sessions.list_open = scoped
    try:
        yield
    finally:
        settings.booking_backend, booking_tools.backend, G._client, sessions.list_open, answer_.list_open = saved


# --- seeding -------------------------------------------------------------------------------------

class World:
    """One fresh business plus what the setup created (keys -> ids)."""

    def __init__(self, setup: dict):
        self.bid = uuid.uuid4()
        self.tz = ZoneInfo(TZ)
        self.today = datetime.now(self.tz).date()
        self.sessions: dict[str, uuid.UUID] = {}
        self.session_names: dict[str, str] = {}
        self.bookings: dict[str, uuid.UUID] = {}
        self.refs: dict[str, str] = {}
        self._create(setup or {})

    def day(self, offset: int) -> str:
        return (self.today + timedelta(days=offset)).isoformat()

    def _create(self, setup: dict) -> None:
        hours = {d: [["09:00", "21:00"]] for d in R.DAYS}
        rules = {"slot_minutes": 60, "capacity": 2, "max_party_size": 8, "hours": hours, **(setup.get("rules") or {})}
        with tenant_session(self.bid) as s:
            s.add(Business(id=self.bid, name="Banya Test", timezone=TZ, settings={"enabled_tools": TOOLS}))
            s.flush()
            s.add(BusinessProfile(business_id=self.bid, name="Banya Test"))
            s.add(BookingRule(business_id=self.bid, **rules))
        for spec in setup.get("sessions") or []:
            item = sessions.create_session(self.bid, self.day(spec["day"]), spec["hour"], spec["name"], spec["max_attendees"])
            sid = uuid.UUID(item["id"])
            self.sessions[spec["key"]] = sid
            self.session_names[spec["key"]] = spec["name"]
            for i, holder in enumerate(spec.get("booked") or []):
                first, _, last = holder.partition(" ")
                sessions.join_session(self.bid, sessions.session_ref(sid), None, first, f"+1555019{i:04d}", last_name=last)
        for spec in setup.get("bookings") or []:
            first, _, last = spec["name"].partition(" ")
            r = InternalBackend().create(self.bid, BookingRequest(
                day=self.day(spec["day"]), at=f"{spec['hour']:02d}:00", party_size=spec.get("party_size"),
                name=first, last_name=last, phone=spec["phone"]))
            assert r.ok, r.message
            self.bookings[spec["key"]] = r.booking_id
            self.refs[spec["key"]] = r.reference

    def persona(self, text: str) -> str:
        text = re.sub(r"\{ref:(\w+)\}", lambda m: self.refs[m.group(1)], text)
        return re.sub(r"\{day:(-?\d+)\}", lambda m: (self.today + timedelta(days=int(m.group(1)))).strftime("%A"), text)

    def delete(self) -> None:
        with tenant_session(self.bid) as s:
            s.execute(delete(Business).where(Business.id == self.bid))


# --- the caller ----------------------------------------------------------------------------------

_THINK = re.compile(r"<think>.*?</think>|<think>.*\Z|</think>", re.S | re.I)


def clean(text: str | None) -> str:
    text = _THINK.sub("", text or "").strip()
    line = next((ln.strip() for ln in text.splitlines() if ln.strip()), "")
    line = re.sub(r"^(caller|customer|me|you)\s*:\s*", "", line, flags=re.I)
    return line.strip().strip('"').strip()


def caller_line(client: OpenAI, system: str, transcript: list[tuple[str, str]]) -> str:
    """The caller model sees only its persona and the transcript (its own lines as `assistant`)."""
    messages = [{"role": "system", "content": system}, {"role": "user", "content": "(The call connects. Say your first line.)"}]
    for who, text in transcript:
        messages.append({"role": "assistant" if who == "caller" else "user", "content": text})
    for _ in range(2):  # one retry on an empty answer
        resp = client.chat.completions.create(
            model=CALLER_MODEL, messages=messages, temperature=0.7, max_tokens=120, reasoning_effort="none")
        line = clean(resp.choices[0].message.content)
        if line:
            return line
    return END


# --- run + assertions ----------------------------------------------------------------------------

@dataclass
class Result:
    scenario: str
    attempt: int
    transcript: list[tuple[str, str]] = field(default_factory=list)
    failures: list[str] = field(default_factory=list)
    state: str = ""

    @property
    def passed(self) -> bool:
        return not self.failures

    def text(self) -> str:
        lines = [f"--- {self.scenario} attempt {self.attempt}: {'PASS' if self.passed else 'FAIL'}"]
        lines += [f"{'CALLER' if w == 'caller' else 'BOT   '}: {t}" for w, t in self.transcript]
        lines += [f"!! {f}" for f in self.failures]
        return "\n".join(lines + ["DB state:", self.state])


# asking for a detail ("... your first and last name to book it?") is not a confirmation question
INFO_Q = re.compile(r"(last name|your name|phone number|time in mind|what time|which (day|time|session))", re.I)
CONFIRM_Q = re.compile(r"\b(shall i|book it|is that (correct|right)|correct\?|right\?|confirm\b|would you like me to|do you want me to|go ahead)", re.I)


def _local(dt, tz):
    return dt.astimezone(tz)


def snapshot(world: World, conv: uuid.UUID | None) -> tuple[str, list[Booking], dict]:
    """-> (printable DB state, all bookings of the business, session key -> confirmed seats)."""
    with tenant_session(world.bid) as s:
        rows = s.scalars(select(Booking).where(Booking.business_id == world.bid).order_by(Booking.created_at)).all()
        names = {g.id: g.display_name for g in s.scalars(select(SessionGuest).where(SessionGuest.business_id == world.bid))}
        logs = s.scalars(select(ActionLog).where(ActionLog.business_id == world.bid).order_by(ActionLog.created_at)).all()
        seats = {k: s.scalar(select(func.count()).select_from(Booking).where(
            Booking.business_id == world.bid, Booking.session_id == sid, Booking.status == "confirmed")) for k, sid in world.sessions.items()}
        sess_key = {sid: k for k, sid in world.sessions.items()}
        lines = [f"  booking {str(b.id)[:8].upper()} {b.type} {b.status} {_local(b.starts_at, world.tz):%a %d %b %H:%M} "
                 f"party={b.party_size} name={b.name!r} guest={names.get(b.guest_id)!r} phone={b.phone} "
                 f"session={sess_key.get(b.session_id)} mine={b.caller_conversation_id == conv}" for b in rows]
        lines += [f"  seats {k}: {n}/{s.get(GroupSession, world.sessions[k]).max_attendees}" for k, n in seats.items()]
        lines += [f"  action_log [{a.status}] {a.tool} {a.arguments} -> {a.result}" for a in logs]
        s.expunge_all()
    return "\n".join(lines) or "  (empty)", rows, seats


def check(world: World, conv: uuid.UUID | None, transcript: list[tuple[str, str]], expect: dict) -> tuple[list[str], str]:
    state, rows, seats = snapshot(world, conv)
    fails: list[str] = []
    new = [b for b in rows if b.caller_conversation_id == conv and conv is not None]
    new_ok = [b for b in new if b.status == "confirmed"]
    with tenant_session(world.bid) as s:
        guests = {g.id: g.display_name for g in s.scalars(select(SessionGuest).where(SessionGuest.business_id == world.bid))}
        caps = {g.id: g.max_attendees for g in s.scalars(select(GroupSession).where(GroupSession.business_id == world.bid))}

    # universal invariants
    bot = [t for w, t in transcript if w == "bot"]
    if not expect.get("skip_claim_check") and any(claims_booking(t) for t in bot) and not new_ok:
        fails.append("INVARIANT a: the bot said it is booked/confirmed/reserved but no confirmed booking exists for this conversation")
    for sid, cap in caps.items():
        n = sum(1 for b in rows if b.session_id == sid and b.status == "confirmed")
        if n > cap:
            fails.append(f"INVARIANT b: {n} confirmed seats in a session with max_attendees={cap}")
    if any(b.guest_id is None for b in rows):
        fails.append("INVARIANT c: a booking has no guest_id")

    # universal invariant (d): short replies, no repeated session list (unless the caller asks for a list/details)
    wants_list = re.compile(r"\b(list|all|options?|which|what (sessions|do you have|is on|else)|details?|tell me (more|about)|"
                            r"available|schedule|other|else|again|repeat)\b", re.I)
    prev_caller = ""
    bot_i = 0
    for who, text in transcript:
        if who == "caller":
            prev_caller = text
            continue
        asked = bool(wants_list.search(prev_caller))
        if len(text.split()) > 40 and not asked:
            fails.append(f"INVARIANT d: bot reply #{bot_i + 1} has {len(text.split())} words (max 40): {text[:80]!r}...")
        clock = r"\d{1,2}(?::\d{2})?\s?(?:am|pm)?"
        starts = re.sub(rf"({clock})\s?(?:to|until|-|–)\s?{clock}", r"\1", text, flags=re.I)  # "18:00 to 19:00" = one session
        times = set(re.findall(r"\b\d{1,2}:\d{2}\b|\b\d{1,2}\s?(?:am|pm)\b", starts, re.I))
        if bot_i >= 1 and len(times) >= 2 and not asked:
            fails.append(f"INVARIANT d: bot reply #{bot_i + 1} lists several sessions again ({sorted(times)}) without being asked")
        bot_i += 1

    # universal invariant (e): a year the bot names is the real one (chat read-backs once said 2024/2025)
    year = world.today.year
    for t in bot:
        bad = [y for y in re.findall(r"\b(20\d\d)\b", t) if int(y) not in (year, year + 1)]
        if bad:
            fails.append(f"INVARIANT e: the bot named the year {bad[0]} (today is {world.today}): {t[:80]!r}")
            break

    # scenario expectations
    if "new_confirmed" in expect and len(new_ok) != expect["new_confirmed"]:
        fails.append(f"expected {expect['new_confirmed']} new confirmed booking(s), found {len(new_ok)}")
    if len(new_ok) == 1:
        b = new_ok[0]
        if "guest" in expect and guests.get(b.guest_id) != expect["guest"]:
            fails.append(f"guest is {guests.get(b.guest_id)!r}, expected {expect['guest']!r}")
        if "phone" in expect and b.phone != expect["phone"]:
            fails.append(f"phone is {b.phone!r}, expected {expect['phone']!r}")
        if "booking_session" in expect and b.session_id != world.sessions[expect["booking_session"]]:
            fails.append(f"the seat is in another session than {expect['booking_session']!r}")
        if "booking_type" in expect and b.type != expect["booking_type"]:
            fails.append(f"type is {b.type!r}, expected {expect['booking_type']!r}")
        if "party_size" in expect and b.party_size != expect["party_size"]:
            fails.append(f"party_size is {b.party_size}, expected {expect['party_size']}")
        start = _local(b.starts_at, world.tz)
        if "day" in expect and start.date().isoformat() != world.day(expect["day"]):
            fails.append(f"booking day is {start.date()}, expected {world.day(expect['day'])}")
        if "hour" in expect and (start.hour, start.minute) != (expect["hour"], 0):
            fails.append(f"booking time is {start:%H:%M}, expected {expect['hour']:02d}:00")
    for key, n in (expect.get("seats") or {}).items():
        if seats.get(key) != n:
            fails.append(f"session {key!r} has {seats.get(key)} confirmed seats, expected {n}")
    by_id = {b.id: b for b in rows}
    for key in expect.get("cancelled") or []:
        if by_id[world.bookings[key]].status != "cancelled":
            fails.append(f"seeded booking {key!r} should be cancelled")
    for key in expect.get("untouched") or []:
        if by_id[world.bookings[key]].status != "confirmed":
            fails.append(f"seeded booking {key!r} should still be confirmed")
    # conversation-flow checks. forbid_after: from the reply to the first caller line matching `caller`
    # on, no bot message may match `bot` (e.g. asking again for what the caller already said)
    for rule in expect.get("forbid_after") or []:
        idx = next((i for i, (w, t) in enumerate(transcript) if w == "caller" and re.search(rule["caller"], t, re.I)), None)
        if idx is None:
            fails.append(f"the caller never said /{rule['caller']}/ (scenario did not reach this point)")
            continue
        for w, t in transcript[idx + 1:]:
            if w == "bot" and re.search(rule["bot"], t, re.I):
                fails.append(f"after the caller said /{rule['caller']}/ the bot still said /{rule['bot']}/: {t[:100]!r}")
                break
    if "max_confirmations" in expect:  # confirmation questions asked before the last bot message
        asks = [
            t for w, t in transcript[:-1]
            if w == "bot" and t.rstrip().endswith("?") and CONFIRM_Q.search(t) and not INFO_Q.search(t)
        ]
        if len(asks) > expect["max_confirmations"]:
            fails.append(f"{len(asks)} confirmation questions asked, max {expect['max_confirmations']}: {[a[-80:] for a in asks]}")
    if "bot_mentions_day" in expect:  # the read-back names that day: weekday name or "Month D" / "D Month"
        d = world.today + timedelta(days=expect["bot_mentions_day"])
        pat = rf"{d:%A}|{d:%B} {d.day}\b|{d:%b} {d.day}\b|\b{d.day}(st|nd|rd|th)? (of )?{d:%B}"
        if not any(re.search(pat, t, re.I) for t in bot):
            fails.append(f"no bot message names the right day ({d:%A %d %B}); P8")
    for pattern in expect.get("bot_says") or []:
        if not any(re.search(pattern, t, re.I) for t in bot):
            fails.append(f"no bot message matches /{pattern}/")
    return fails, state


def run_once(scenario: dict, common: str, attempt: int = 1, log=None) -> Result:
    """One fresh business, one conversation. `log(line)` is called per line while it runs."""
    from app.main import app

    result = Result(scenario["id"], attempt)
    world = None
    try:
        # World() creates sessions: inside the patch, or the .env Google token writes real events to the shared calendar
        with patched_app(set()):
            world = World(scenario.get("setup"))
            llm = OpenAI(base_url=get_settings().llm_base_url, api_key=get_settings().llm_api_key)
            http = TestClient(app)
            system = common + "\n\n" + world.persona(scenario["persona"].strip())
            conv: str | None = None
            line = scenario["opening"]
            for _ in range(scenario.get("max_turns", 8)):
                result.transcript.append(("caller", line))
                if log:
                    log(f"CALLER: {line}")
                resp = http.post("/chat", json={"business_id": str(world.bid), "question": line, **({"conversation_id": conv} if conv else {})})
                resp.raise_for_status()
                body = resp.json()
                conv = body["conversation_id"]
                result.transcript.append(("bot", body["answer"]))
                if log:
                    log(f"BOT   : {body['answer']}")
                line = caller_line(llm, system, result.transcript)
                if line.startswith(END):
                    break
                if END in line:  # words before <END>: say them, then stop
                    line = line.replace(END, "").strip()
                    result.transcript.append(("caller", line))
                    if log:
                        log(f"CALLER: {line}")
                    break
            result.failures, result.state = check(world, uuid.UUID(conv) if conv else None, result.transcript, scenario.get("expect") or {})
    except Exception as exc:  # a harness/app crash is a failure with the transcript attached
        result.failures.append(f"crashed: {type(exc).__name__}: {exc}")
    finally:
        if world is not None:
            world.delete()
    return result


def run_scenario(scenario: dict, common: str, log=None) -> list[Result]:
    """Attempts until one passes (1 + SCENARIO_RETRIES at most)."""
    results: list[Result] = []
    for attempt in range(1, retries() + 2):
        results.append(run_once(scenario, common, attempt, log))
        if results[-1].passed:
            break
    return results


def main(argv: list[str]) -> int:
    common, scenarios = load()
    wanted = argv or [s["id"] for s in scenarios]
    by_id = {s["id"]: s for s in scenarios}
    if any(w not in by_id for w in wanted):
        print("unknown scenario; known:", ", ".join(by_id))
        return 2
    if (why := lm_missing()):
        print("cannot run:", why)
        return 2
    code = 0
    for sid in wanted:
        print(f"=== {sid}")
        result = run_once(by_id[sid], common, log=print)
        print(result.text().split("\n", 1)[0], *[f"!! {f}" for f in result.failures], "DB state:", result.state, sep="\n")
        code |= 0 if result.passed else 1
    return code


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
