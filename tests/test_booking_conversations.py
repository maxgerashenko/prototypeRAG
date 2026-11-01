"""Simulated booking conversations (#55, plan/04-actions.md → Tasks).

Each test is a scripted call: the caller's lines are fixed and the "model" plays back the
tool calls and answers a good model would produce (`FakeLLM`). Everything behind the model
is real: the tool loop in `answer_question`, the executor (confirmation guard, action log),
the booking tools, the rules and the Postgres backend. So these tests check what the code
guarantees whatever the model says -- not that a model behaves; that needs a live run
(see 03-voice-channel.md "Test on Mac").

Needs Postgres (no pgvector search, no LM Studio). Dates are far in the future because the
tools use the real clock for the lead-time rule.
"""

import json
import uuid

import pytest
from sqlalchemy import delete, select

from app.actions.booking import rules as R
from app.actions.booking import tools as booking_tools  # noqa: F401  (importing registers the tools)
from app.db import tenant_session
from app.db.models import ActionLog, Booking, BookingRule, Business, Conversation
from app.rag import answer
from tests.test_rag import FakeLLM

MON, THU, FRI = "2099-11-02", "2099-11-05", "2099-11-06"  # 2099-11-02 is a Monday
OPEN_9_21 = {d: [["09:00", "21:00"]] for d in R.DAYS if d != "mon"}  # closed Mondays
CALLER = "+15550100100"


@pytest.fixture
def business_id():
    bid = uuid.uuid4()
    with tenant_session(bid) as s:
        s.add(Business(id=bid, name="Spa", timezone="America/New_York", settings={"enabled_tools": ["check_availability", "create_booking", "cancel_booking"]}))
        s.flush()
        s.add(BookingRule(business_id=bid, slot_minutes=60, capacity=1, max_party_size=6, hours=OPEN_9_21))
    yield bid
    with tenant_session(bid) as s:
        s.execute(delete(Business).where(Business.id == bid))


def _call(bid, caller=CALLER) -> uuid.UUID | None:
    """A conversation row, like a phone call with caller ID (None caller = text chat)."""
    with tenant_session(bid) as s:
        c = Conversation(business_id=bid, channel="voice" if caller else "chat", caller=caller)
        s.add(c)
        s.flush()
        return c.id


def tool(tool_name: str, /, **args) -> list[tuple[str, str, str]]:
    """One scripted model step: a single tool call."""
    return [("c1", tool_name, json.dumps(args))]


class Call:
    """A scripted conversation. `turn(caller_says, *model_steps)` plays one caller turn: the
    model steps are tool calls (from `tool()`) ending with the text answer. Returns the
    answer; `results` is the list of tool results the model saw, in order."""

    def __init__(self, monkeypatch, bid, conversation_id):
        self.bid, self.conversation_id = bid, conversation_id
        self.history: list[dict] = []
        self.results: list[str] = []
        self.monkeypatch = monkeypatch
        monkeypatch.setattr(
            answer, "prepare_prompt",
            lambda business_id, question, history=None, top_n=5: (
                None, [], [{"role": "system", "content": "rules"}, *(history or []),
                           {"role": "user", "content": question}]),
        )

    def turn(self, says: str, *steps) -> str:
        llm = FakeLLM(*steps)
        self.monkeypatch.setattr(answer, "get_chat_client", lambda: llm)
        out = answer.answer_question(self.bid, says, self.history, conversation_id=self.conversation_id).answer
        assert llm.replies == [], "the script has model steps the loop never asked for"
        self.results += [m["content"] for m in llm.requests[-1]["messages"] if m["role"] == "tool"]
        self.history += [{"role": "user", "content": says}, {"role": "assistant", "content": out}]
        return out


@pytest.fixture
def call_for(monkeypatch, business_id):
    return lambda caller=CALLER: Call(monkeypatch, business_id, _call(business_id, caller))


def _bookings(bid, status="confirmed"):
    with tenant_session(bid) as s:
        return s.execute(
            select(Booking.name, Booking.phone, Booking.party_size, Booking.starts_at)
            .where(Booking.status == status).order_by(Booking.starts_at)
        ).all()


def _log(bid):
    with tenant_session(bid) as s:
        return s.execute(select(ActionLog.tool, ActionLog.status).order_by(ActionLog.created_at)).all()


def _ref(result: str) -> str:
    return result.split("Reference ")[1].split(".")[0]


def _book(call: Call, day=THU, at="14:00", name="Anna", party_size=2) -> str:
    """Helper for scenarios that start from an existing booking; returns its reference."""
    call.turn(
        "Table for two Thursday at two, Anna.",
        tool("check_availability", date=day, time=at, party_size=party_size),
        "Thursday 2 pm for two is free. Shall I book it under Anna?",
    )
    call.turn(
        "Yes.",
        tool("create_booking", date=day, time=at, party_size=party_size, name=name, confirmed=True),
        "Booked.",
    )
    return _ref(call.results[-1])


# ---- happy path ----------------------------------------------------------------------

def test_happy_path_check_confirm_book(call_for, business_id):
    call = call_for()
    assert "free" in call.turn(
        "Hi, a table for two on Thursday at two pm, please.",
        tool("check_availability", date=THU, time="14:00", party_size=2),
        "Thursday at 2 pm for two is free. Shall I book it under your name?",
    )
    assert call.results == ["Available."]
    assert _bookings(business_id) == []  # asking never books

    call.turn(
        "Anna. Yes, please.",
        tool("create_booking", date=THU, time="14:00", party_size=2, name="Anna", confirmed=True),
        "You're booked: Thursday 5 November at 2 pm, table for two. Your reference is on screen.",
    )
    assert call.results[-1].startswith("Booked table for 2 on Thu 05 Nov, 14:00. Reference ")
    rows = _bookings(business_id)
    assert [(r.name, r.phone, r.party_size) for r in rows] == [("Anna", CALLER, 2)]
    assert rows[0].starts_at.hour == 19  # 14:00 New York (EST) stored as UTC
    assert _log(business_id) == [("create_booking", "ok")]


def test_appointment_with_a_service(call_for, business_id):
    call = call_for()
    call.turn(
        "A sauna session Friday at 10, I'm Dana.",
        tool("create_booking", date=FRI, time="10:00", service="Sauna", name="Dana"),
        "To confirm: a sauna session Friday at 10 am for Dana. Is that right?",
    )
    assert call.results[-1].startswith("Not done")
    call.turn(
        "Yes.",
        tool("create_booking", date=FRI, time="10:00", service="Sauna", name="Dana", confirmed=True),
        "Done.",
    )
    assert call.results[-1].startswith("Booked Sauna on Fri 06 Nov, 10:00")
    assert [r.name for r in _bookings(business_id)] == ["Dana"]


# ---- edge cases ----------------------------------------------------------------------

def test_closed_day_then_caller_picks_another_day(call_for, business_id):
    call = call_for()
    call.turn(
        "Table for four on Monday at seven pm.",
        tool("check_availability", date=MON, time="19:00", party_size=4),
        "Sorry, we're closed on Mondays. Would another day work?",
    )
    assert call.results == ["Not available: We are closed that day."]
    call.turn(
        "Thursday then, same time. Name is Ben.",
        tool("check_availability", date=THU, time="19:00", party_size=4),
        "Thursday at 7 pm for four is free. Book it for Ben?",
    )
    call.turn(
        "Yes.",
        tool("create_booking", date=THU, time="19:00", party_size=4, name="Ben", confirmed=True),
        "Booked.",
    )
    assert [(r.name, r.party_size) for r in _bookings(business_id)] == [("Ben", 4)]


def test_model_that_skips_the_check_cannot_book_a_closed_day(call_for, business_id):
    """Rules run in code: even a confirmed create_booking is refused on a closed day."""
    call = call_for()
    call.turn(
        "Monday at noon, Cara, yes I confirm.",
        tool("create_booking", date=MON, time="12:00", party_size=2, name="Cara", confirmed=True),
        "Sorry, we're closed on Mondays. Thursday at noon?",
    )
    assert call.results[-1].startswith("Error: not booked. We are closed that day.")
    assert "Free times" not in call.results[-1]  # alternatives are for the same (closed) day only
    assert _bookings(business_id) == []
    assert _log(business_id) == [("create_booking", "error")]


def test_outside_hours_offers_alternatives_from_the_opening_grid(call_for, business_id):
    call = call_for()
    call.turn(
        "Friday at 10 pm for two.",
        tool("check_availability", date=FRI, time="22:00", party_size=2),
        "We close at 9. The latest I can offer is 8 pm.",
    )
    assert call.results[-1].startswith("Not available: That time is outside opening hours")
    assert "Free times that day: 21:00" not in call.results[-1]
    assert "20:00" in call.results[-1]


def test_full_slot_second_caller_gets_alternatives_and_takes_one(call_for, business_id):
    first = call_for()
    _book(first)
    second = call_for("+15550100200")
    second.turn(
        "Table for two Thursday at 2:30, Bob.",
        tool("check_availability", date=THU, time="14:30", party_size=2),
        "That time is fully booked. I can do 3 pm or 1 pm.",
    )
    assert "fully booked" in second.results[-1] and "15:00" in second.results[-1]
    second.turn(
        "3 pm. Yes, book it.",
        tool("create_booking", date=THU, time="15:00", party_size=2, name="Bob", confirmed=True),
        "Booked.",
    )
    assert [(r.name, r.phone) for r in _bookings(business_id)] == [("Anna", CALLER), ("Bob", "+15550100200")]


def test_full_slot_refuses_even_a_confirmed_create(call_for, business_id):
    _book(call_for())
    other = call_for("+15550100200")
    other.turn(
        "Thursday 2 pm, Bob, I confirm.",
        tool("create_booking", date=THU, time="14:00", party_size=2, name="Bob", confirmed=True),
        "Sorry, that's taken.",
    )
    assert other.results[-1].startswith("Error: not booked. That time is fully booked.")
    assert [r.name for r in _bookings(business_id)] == ["Anna"]


def test_party_too_large_is_refused(call_for, business_id):
    call = call_for()
    call.turn(
        "Table for ten Thursday at 7 pm, Eve, confirmed.",
        tool("create_booking", date=THU, time="19:00", party_size=10, name="Eve", confirmed=True),
        "We can seat at most six people per booking.",
    )
    assert "largest party we can book is 6" in call.results[-1]
    assert _bookings(business_id) == []


def test_change_of_mind_before_confirming_books_only_the_final_choice(call_for, business_id):
    call = call_for()
    call.turn(
        "Thursday at 2 pm, two people, Anna.",
        tool("create_booking", date=THU, time="14:00", party_size=2, name="Anna"),
        "To confirm: Thursday 2 pm, two people, Anna. Right?",
    )
    assert call.results[-1].startswith("Not done")
    assert _bookings(business_id) == []
    call.turn(
        "Actually, make it Friday at 6 pm.",
        tool("check_availability", date=FRI, time="18:00", party_size=2),
        "Friday 6 pm is free. Shall I book Friday at 6 pm for two, Anna?",
    )
    call.turn(
        "Yes.",
        tool("create_booking", date=FRI, time="18:00", party_size=2, name="Anna", confirmed=True),
        "Booked.",
    )
    rows = _bookings(business_id)
    assert len(rows) == 1 and rows[0].starts_at.day == 6  # Friday 23:00 UTC; Thursday was never booked
    assert _log(business_id) == [("create_booking", "needs_confirmation"), ("create_booking", "ok")]


def test_caller_declines_at_read_back_nothing_is_booked(call_for, business_id):
    call = call_for()
    call.turn(
        "Thursday at 2 pm for two, Anna.",
        tool("create_booking", date=THU, time="14:00", party_size=2, name="Anna"),
        "Shall I book Thursday 2 pm for two under Anna?",
    )
    assert call.turn("No, forget it.", "No problem. Anything else?") == "No problem. Anything else?"
    assert _bookings(business_id) == []
    assert _log(business_id) == [("create_booking", "needs_confirmation")]


def test_invalid_date_and_missing_name_are_returned_to_the_model(call_for, business_id):
    call = call_for()
    call.turn(
        "Table next Thursday.",
        tool("check_availability", date="next Thursday", time="14:00"),
        "Which date exactly?",
    )
    assert call.results[-1] == "Not available: date must look like 2026-11-05"
    call.turn(
        "5 November at 2, confirmed.",
        tool("create_booking", date=THU, time="14:00", party_size=2, name="  ", confirmed=True),
        "What name is it under?",
    )
    assert call.results[-1].startswith("Error: not booked. A name")
    assert _bookings(business_id) == []


# ---- cancellation --------------------------------------------------------------------

def test_cancel_by_reference_frees_the_slot_for_someone_else(call_for, business_id):
    ref = _book(call_for())
    again = call_for()  # same caller ID, a later call
    again.turn(
        f"I want to cancel booking {ref}.",
        tool("cancel_booking", reference=ref),
        "Cancel booking " + ref + ", Thursday 2 pm. Is that right?",
    )
    assert again.results[-1].startswith("Not done")
    assert len(_bookings(business_id)) == 1
    again.turn("Yes.", tool("cancel_booking", reference=ref, confirmed=True), "It's cancelled.")
    assert again.results[-1] == f"Cancelled booking {ref}."
    assert _bookings(business_id) == [] and len(_bookings(business_id, "cancelled")) == 1

    other = call_for("+15550100200")
    other.turn(
        "Thursday 2 pm, two, Bob, confirmed.",
        tool("create_booking", date=THU, time="14:00", party_size=2, name="Bob", confirmed=True),
        "Booked.",
    )
    assert other.results[-1].startswith("Booked")


def test_cancel_unknown_reference(call_for, business_id):
    call = call_for()
    call.turn(
        "Cancel booking ZZZZZZZZ, confirmed.",
        tool("cancel_booking", reference="ZZZZZZZZ", confirmed=True),
        "I can't find that booking.",
    )
    assert call.results[-1].startswith("No upcoming booking found")


# ---- text chat (no caller ID) --------------------------------------------------------

def test_chat_without_caller_id_asks_for_a_valid_number(call_for, business_id):
    chat = call_for(caller=None)
    chat.turn(
        "Thursday 2 pm, Anna, confirmed.",
        tool("create_booking", date=THU, time="14:00", name="Anna", confirmed=True),
        "What phone number should the booking be under?",
    )
    assert "phone number is needed" in chat.results[-1]
    chat.turn(
        "555 0100",
        tool("create_booking", date=THU, time="14:00", name="Anna", phone="5550100", confirmed=True),
        "Please give the full number with country code.",
    )
    assert "phone number is needed" in chat.results[-1]
    chat.turn(
        "+1 555 010 0300",
        tool("create_booking", date=THU, time="14:00", name="Anna", phone="+1 555 010 0300", confirmed=True),
        "Booked.",
    )
    assert [r.phone for r in _bookings(business_id)] == ["+15550100300"]
