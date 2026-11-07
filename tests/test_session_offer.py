"""Reply-text guards for sessions: `offer_open_sessions` (a refusal names the real sessions, built in code),
`prompt_sessions` (the rows come from the system prompt) and `fix_end_times` (AM/PM on an end time)."""

from datetime import date
from types import SimpleNamespace

import pytest

from app.actions.booking import tools
from app.rag import answer as A
from app.rag.answer import fix_end_times, needs_session_offer, offer_open_sessions, prompt_sessions

REFUSAL = "We don't have a Steam Bath session tomorrow at 2:00 PM. Would you like to book one of those instead?"
ASK = "I'd like to book the Steam Bath session at 2 PM"


def _row(name, when, full=False):
    return {"name": name, "when": when, "full": full}


ROWS = [
    _row("Sauna Polish", "Fri 06 Nov, 10:00 AM to 11:00 AM"),
    _row("Sauna German", "Fri 06 Nov, 3:00 PM to 4:00 PM"),
    _row("Banya Evening", "Fri 06 Nov, 6:00 PM to 7:00 PM"),
]


@pytest.fixture(autouse=True)
def today(monkeypatch):
    monkeypatch.setattr(tools, "_business_today", lambda business_id: date(2026, 11, 5))


def test_the_logged_refusal_gets_the_real_sessions():
    out = offer_open_sessions(REFUSAL, ASK, ROWS, None)
    assert out == (
        "We don't have a Steam Bath session tomorrow at 2:00 PM. We have Sauna Polish tomorrow at 10:00 AM, "
        "Sauna German tomorrow at 3:00 PM, Banya Evening tomorrow at 6:00 PM. Would you like to book one of those instead?")
    assert len(out.split()) <= 40


def test_curly_apostrophe_and_other_refusals():
    assert offer_open_sessions(REFUSAL.replace("'", "’"), ASK, ROWS, None).count("We have Sauna Polish") == 1
    for text in ("There is no Steam Bath session tomorrow.", "The Steam Bath session is not on the schedule.",
                 "I can’t find a Steam Bath session.", "Steam Bath isn't available tomorrow."):
        assert "We have Sauna Polish" in offer_open_sessions(text, ASK, ROWS, None), text


def test_a_long_refusal_gets_fewer_sessions_to_stay_under_the_cap():
    long = "We are very sorry but we really do not have any such Steam Bath session on any day this whole week, sorry."
    out = offer_open_sessions(long, ASK, ROWS, None)
    assert len(out.split()) <= 40 and "We have Sauna Polish" in out and "Banya" not in out


@pytest.mark.parametrize("text, question, rows", [
    ("Sauna Polish is tomorrow at 10 AM. We don't have a Steam Bath session.", ASK, ROWS),  # reply names one
    ("We don't have a Sauna German session at 2 PM.", "book the Sauna German session", ROWS),  # caller named one
    ("Of course. If you change your mind, just ask.", "No thanks, I don't want those.", ROWS),  # no intent word
    (REFUSAL, ASK, []),  # no sessions
    (REFUSAL, ASK, [_row("Sauna Polish", "Fri 06 Nov, 10:00 AM to 11:00 AM", full=True)]),  # only FULL ones
    ("Sure, I can book that. Shall I?", ASK, ROWS),  # no refusal
    ("There are no sessions on Sunday.", "any sessions on Sunday?", ROWS),  # nothing on a day, not a missing session
    ("I haven't booked anything yet. Shall I go ahead and book it?", ASK, ROWS),
    ("I can't confirm it without your last name. What is your last name?", "Yes, book it", ROWS),  # not a session refusal
    ("I couldn't find a booking with that reference.", "I want to cancel my session booking", ROWS),  # no shared word
    ("We don't have a parking lot, but there is street parking.", "Is there parking at your place?", ROWS),
])
def test_unchanged(text, question, rows):
    assert not needs_session_offer(text, question, rows)
    assert offer_open_sessions(text, question, rows, None) == text


def test_prompt_sessions_reads_only_the_system_prompt_lines():
    system = (
        "Open sessions (for your reference):\n"
        "3C785323 · Sauna Polish · Fri 06 Nov, 10:00 AM to 11:00 AM · SOONEST of this name\n"
        "AAAA1111 · Sauna German · Fri 06 Nov, 3:00 PM to 4:00 PM · FULL\n"
        "BBBB2222 · Sauna Polish · Sat 07 Nov, 10:00 AM to 11:00 AM\n"
    )
    rows = prompt_sessions([{"role": "system", "content": system}])
    assert rows == [
        {"name": "Sauna Polish", "when": "Fri 06 Nov, 10:00 AM to 11:00 AM", "full": False},
        {"name": "Sauna German", "when": "Fri 06 Nov, 3:00 PM to 4:00 PM", "full": True},
        {"name": "Sauna Polish", "when": "Sat 07 Nov, 10:00 AM to 11:00 AM", "full": False},
    ]
    assert prompt_sessions([{"role": "user", "content": system}]) == []
    assert prompt_sessions([{"role": "system", "content": "no lines"}]) == []
    assert prompt_sessions([]) == []


@pytest.mark.parametrize("text, expected", [
    ("6:00 PM to 7:00", "6:00 PM to 7:00 PM"),
    ("from 6:00 p.m. to 7:00 today", "from 6:00 p.m. to 7:00 PM today"),
    ("11:00 AM to 12:00", "11:00 AM to 12:00 PM"),
    ("11:30 PM until 12:30", "11:30 PM until 12:30 AM"),
    ("6:00 PM to 19:00", "6:00 PM to 7:00 PM"),
    ("10 AM to 11.", "10 AM to 11:00 AM."),
    ("Polish is from 10:00 AM to 11:00, then German.", "Polish is from 10:00 AM to 11:00 AM, then German."),
    ("6:00 PM to 7:00 PM", "6:00 PM to 7:00 PM"),
    ("6 PM to 8 people", "6 PM to 8 people"),
    ("10:00 to 11:00", "10:00 to 11:00"),
    ("10 AM to 2026.", "10 AM to 2026."),
    ("We open at nine.", "We open at nine."),
])
def test_fix_end_times(text, expected):
    assert fix_end_times(text) == expected


# --- answer_question: the guards run on the final reply -------------------------------------------------

SYSTEM = (
    "You are a bot.\nOpen sessions (codes are for join_session only):\n"
    "AAAA1111 · Sauna Polish · Fri 06 Nov, 10:00 AM to 11:00 AM\n"
    "BBBB2222 · Sauna German · Fri 06 Nov, 3:00 PM to 4:00 PM\n"
)


@pytest.fixture
def chat(monkeypatch):
    def install(reply, question=ASK):
        client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(
            create=lambda **kw: SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=reply, tool_calls=None))]))))
        monkeypatch.setattr(A, "prepare_prompt", lambda *a, **k: (None, [], [
            {"role": "system", "content": SYSTEM}, {"role": "user", "content": question}]))
        monkeypatch.setattr(A, "get_chat_client", lambda: client)
        monkeypatch.setattr(A, "tools_for", lambda b: [])
        return lambda: A.answer_question(None, question).answer
    return install


def test_answer_question_names_the_open_sessions_after_a_refusal(chat):
    out = chat(REFUSAL)()
    assert out == ("We don't have a Steam Bath session tomorrow at 2:00 PM. We have Sauna Polish tomorrow at "
                   "10:00 AM, Sauna German tomorrow at 3:00 PM. Would you like to book one of those instead?")


def test_answer_question_fixes_the_end_time(chat):
    assert chat("Polish is tomorrow, 6:00 PM to 7:00.", "what is on?")() == "Polish is tomorrow, 6:00 PM to 7:00 PM."


def test_answer_question_leaves_a_declined_offer_alone(chat):
    reply = "No problem, just ask if you change your mind."
    assert chat(reply, "no thanks, I don't want those")() == reply


def test_a_question_after_the_refusal_is_kept():
    out = offer_open_sessions("We don't have a Steam Bath session. Would you like another time?", ASK, ROWS, None)
    assert out.endswith("Would you like another time?") and "We have Sauna Polish tomorrow at 10:00 AM" in out
    assert out.count("Would you like") == 1


def test_a_refusal_that_shares_no_word_with_the_caller_is_not_a_session_refusal():
    # "Steam Bath" is asked for, the refusal is about something else
    out = offer_open_sessions("We don't have parking here.", ASK, ROWS, None)
    assert out == "We don't have parking here."
