"""An empty model reply is asked again once, then replaced by something sayable (chat path)."""

from types import SimpleNamespace

import pytest

from app.rag import answer as answer_mod


def _msg(content=None, tool_calls=None):
    return SimpleNamespace(content=content, tool_calls=tool_calls)


def _call():
    return SimpleNamespace(id="c1", function=SimpleNamespace(name="join_session", arguments="{}"))


class FakeClient:
    def __init__(self, script):
        self.script, self.n = script, 0
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    def _create(self, **kwargs):
        item = self.script[min(self.n, len(self.script) - 1)]
        self.n += 1
        if kwargs.get("stream"):
            delta = SimpleNamespace(content=item.content, tool_calls=[
                SimpleNamespace(index=0, id=tc.id, function=SimpleNamespace(name=tc.function.name, arguments="{}"))
                for tc in item.tool_calls or []
            ])
            return iter([SimpleNamespace(choices=[SimpleNamespace(delta=delta)])])
        return SimpleNamespace(choices=[SimpleNamespace(message=item)])


BOOKED = "Booked Ann Lee into Sauna Polish on Thu 05 Nov, 10:00. Reference ABCD1234. Read the reference to the caller."


@pytest.fixture
def patch(monkeypatch):
    def install(script, tool_result="No sessions."):
        client = FakeClient(script)
        monkeypatch.setattr(answer_mod, "prepare_prompt", lambda *a, **k: (None, [], [{"role": "user", "content": "hi"}]))
        monkeypatch.setattr(answer_mod, "get_chat_client", lambda: client)
        monkeypatch.setattr(answer_mod, "tools_for", lambda b: [])
        monkeypatch.setattr(
            answer_mod, "_run_calls",
            lambda b, c, calls: [{"role": "tool", "tool_call_id": "c1", "content": tool_result}],
        )
        return client
    return install


def test_empty_reply_is_asked_again(patch):
    client = patch([_msg(""), _msg("We open at nine.")])
    assert answer_mod.answer_question(None, "hours?").answer == "We open at nine."
    assert client.n == 2


def test_still_empty_is_not_blank(patch):
    patch([_msg("")])
    assert answer_mod.answer_question(None, "hours?").answer == answer_mod.EMPTY_ANSWER


def test_empty_after_a_booking_says_what_was_booked(patch):
    patch([_msg(None, [_call()]), _msg("")], tool_result=BOOKED)
    assert answer_mod.answer_question(None, "yes").answer == (
        "Booked Ann Lee into Sauna Polish on Thu 05 Nov, 10:00. Reference ABCD1234."
    )


def test_stream_empty_then_answer(patch):
    patch([_msg(""), _msg("We open at nine.")])
    assert "".join(answer_mod.stream_answer(None, "hours?")) == "We open at nine."


def test_stream_still_empty_after_booking(patch):
    patch([_msg(None, [_call()]), _msg("")], tool_result=BOOKED)
    assert "".join(answer_mod.stream_answer(None, "yes")).endswith("Reference ABCD1234.")


def test_shorten_keeps_short_replies_and_the_closing_question():
    from app.rag.answer import shorten

    assert shorten("We close at 5.") == "We close at 5."
    long = ("I believe there may be some confusion about that. " * 6 + "We have Sauna Polish on Thursday at 10:00. "
            "Shall I book it?")
    out = shorten(long)
    assert len(out.split()) <= 40 and out.endswith("Shall I book it?")


def test_a_booked_claim_without_the_tool_is_replaced_by_a_question():
    from app.rag.answer import NOT_BOOKED_ANSWER, guard_booking_claim

    said = "A table for four is booked. Your reference is 3F9A1C2B."
    assert guard_booking_claim(said, [], 0, []) == NOT_BOOKED_ANSWER
    tool = [{"role": "tool", "content": "Booked table for 4 on Thu. Reference 3F9A1C2B."}]
    assert guard_booking_claim(said, tool, 0, []) == said  # the tool booked it this turn
    recap = [{"role": "assistant", "content": "Your table is booked."}]
    assert guard_booking_claim("Yes, it is confirmed.", [], 0, recap) == "Yes, it is confirmed."  # earlier claim
    assert guard_booking_claim("Shall I book it?", [], 0, []) == "Shall I book it?"


def test_shorten_never_returns_more_than_the_cap():
    from app.rag.answer import shorten

    long_question = "Before I save your message " + "could you tell me " * 8 + "your name?"
    out = shorten("I understand you would like to call back later. " + long_question, 40)
    assert len(out.split()) <= 40 and out.endswith("?")
    one_sentence = "word " * 60
    assert len(shorten(one_sentence.strip() + ".", 40).split()) <= 40


def test_asking_for_the_name_twice_in_a_row_is_detected():
    from app.rag.answer import repeats_name_question

    asked = [{"role": "assistant", "content": "Could I have your first and last name?"}]
    assert repeats_name_question("Please confirm your first and last name.", asked, "Abraham Lincoln")
    assert not repeats_name_question("Please confirm your first and last name.", asked, "Tom")
    assert not repeats_name_question("Shall I book it for Abe Lincoln?", asked)
    assert not repeats_name_question("Could I have your first and last name?", [])


LISTED = [{"role": "user", "content": "what sessions?"},
          {"role": "assistant", "content": "Sauna Polish on Friday at 10:00 and Sauna German on Friday at 15:00."}]


def test_relisting_sessions_is_detected():
    again = "We have Sauna Polish on Friday at 10:00 and Sauna German at 15:00. Which one?"
    assert answer_mod.relists_sessions(again, "the Polish one", LISTED)
    assert not answer_mod.relists_sessions(again, "what else do you have?", LISTED)  # asked for the list
    assert not answer_mod.relists_sessions(again, "what sessions?", [])  # first listing
    assert not answer_mod.relists_sessions("Sauna Polish on Friday, 10:00 to 11:00. Shall I book it?", "the Polish one", LISTED)


def test_a_relisted_reply_is_asked_again(patch):
    client = patch([_msg("Sauna Polish at 10:00 and Sauna German at 15:00."), _msg("Sauna Polish on Friday at 10:00?")])
    out = answer_mod.answer_question(None, "the Polish one", history=LISTED).answer
    assert out == "Sauna Polish on Friday at 10:00?" and client.n == 2


def test_asking_for_the_name_after_the_caller_gave_it_is_detected():
    from app.rag.answer import repeats_name_question

    readback = [{"role": "assistant", "content": "Do you mean Sauna Polish on Friday at 10:00?"}]
    again = "Do you mean the Sauna Polish session on Friday at 10:00? Please confirm your first and last name"
    assert repeats_name_question(again, readback, "Abraham Lincoln")  # this line was the name
    assert repeats_name_question("May I have your name?", readback, "my name is abe lincoln")
    earlier = [{"role": "user", "content": "It's Abraham Lincoln."}] + readback
    assert repeats_name_question(again, earlier, "Yes")  # the name was given in an earlier line
    assert not repeats_name_question(again, readback, "sign me up to police station")
    assert not repeats_name_question(again, readback, "Yes please")
    assert not repeats_name_question(again, readback, "Sauna Polish", {"Sauna", "Polish"})  # a session, not a name
    assert not repeats_name_question("Shall I book it for Abraham Lincoln?", readback, "Abraham Lincoln")


def test_given_name():
    from app.rag.answer import given_name

    assert given_name("Abraham Lincoln") == "Abraham Lincoln"
    assert given_name("Yes, my name is abe lincoln.") == "abe lincoln"
    assert given_name("sign me up") is None and given_name("Hello") is None
    assert given_name("The Polish one") is None


def test_name_given_this_turn_asked_again_is_retried(patch):
    readback = [{"role": "user", "content": "the Polish session"},
                {"role": "assistant", "content": "Do you mean Sauna Polish on Friday at 10:00?"}]
    client = patch([_msg("Please confirm your first and last name."),
                    _msg("Sauna Polish on Friday at 10:00 for Abraham Lincoln. Shall I book it?")])
    out = answer_mod.answer_question(None, "Abraham Lincoln", history=readback).answer
    assert out.endswith("Shall I book it?") and client.n == 2


def test_open_sessions_mark_the_soonest_of_a_shared_name():
    rows = [{"ref": "AAAA1111", "name": "Sauna Polish", "when": "Fri 06 Nov, 10:00 to 11:00", "full": False},
            {"ref": "BBBB2222", "name": "Sauna German", "when": "Fri 06 Nov, 15:00 to 16:00", "full": False},
            {"ref": "CCCC3333", "name": "Sauna Polish", "when": "Sat 07 Nov, 10:00 to 11:00", "full": True}]
    lines = answer_mod.session_lines(rows)
    assert lines[0].endswith("SOONEST of this name") and "SOONEST" not in lines[1] + lines[2]
    assert lines[2].endswith("FULL")
    system = [{"role": "system", "content": "Open sessions:\n" + "\n".join(lines)}]
    assert answer_mod.session_name_words(system) == {"Sauna", "Polish", "German"}


def test_asking_only_for_the_last_name_first_is_detected():
    from app.rag.answer import asks_only_last_name

    picked = [{"role": "assistant", "content": "Do you mean Sauna Polish on Friday at 10:00?"}]
    ask_last = "I need your last name to complete the booking. Could you please provide it?"
    assert asks_only_last_name(ask_last, "Yes", picked)  # no first name was ever given
    assert not asks_only_last_name(ask_last, "Yes", [{"role": "user", "content": "Hi, my name is Abe"}] + picked)
    asked = [{"role": "assistant", "content": "Could I have your name?"}]
    assert not asks_only_last_name(ask_last, "Abe", asked)  # the caller answered a name question
    assert not asks_only_last_name("Could I have your first and last name?", "Yes", picked)


def test_asking_only_for_the_last_name_is_retried(patch):
    picked = [{"role": "assistant", "content": "Do you mean Sauna Polish on Friday at 10:00?"}]
    client = patch([_msg("I need your last name to complete the booking."),
                    _msg("Could I have your first and last name?")])
    out = answer_mod.answer_question(None, "Yes", history=picked).answer
    assert out == "Could I have your first and last name?" and client.n == 2


def test_review_edge_cases_are_not_blocked():
    from app.rag.answer import claims_booking, given_name, relists_sessions, repeats_name_question, shorten

    assert not claims_booking("Sorry, Thursday's 10:00 session is booked out.")
    assert given_name("I'm at work") is None and given_name("it's from London") is None
    assert given_name("it's Abe Lincoln") == "Abe Lincoln"
    asked = [{"role": "assistant", "content": "What's your name?"}]
    assert not repeats_name_question("Thanks Tom, and your last name?", asked, "Tom")  # a first name alone
    two = [{"role": "assistant", "content": "Sauna at 10:00 and Banya at 18:00."}]
    assert not relists_sessions("Friday: Sauna at 10:00 and Banya at 18:00.", "what's on Friday?", two)
    long_first = "The Sauna Polish session on Friday " + "really " * 40 + "starts at ten. Shall I book it?"
    out = shorten(long_first)
    assert out.endswith("Shall I book it?") and out.startswith("The Sauna Polish") and len(out.split()) <= 40


def test_a_false_booking_claim_after_a_say_exactly_refusal_says_the_refusal():
    from app.rag.answer import NOT_BOOKED_ANSWER, guard_booking_claim

    refusal = 'Error: not booked. Say exactly: "There is no session called \'Steam Bath\' on the schedule. We have Sauna Polish tomorrow at 10:00 AM." Never mention codes or a technical problem.'
    said = "Your Steam Bath session is booked, reference ABCD1234."
    msgs = [{"role": "user", "content": "hi"}, {"role": "tool", "tool_call_id": "c1", "content": refusal}]
    assert guard_booking_claim(said, msgs, 1, []) == (
        "There is no session called 'Steam Bath' on the schedule. We have Sauna Polish tomorrow at 10:00 AM.")
    other = [{"role": "user", "content": "hi"}, {"role": "tool", "tool_call_id": "c1", "content": "Error: needs a name."}]
    assert guard_booking_claim(said, other, 1, []) == NOT_BOOKED_ANSWER
    assert guard_booking_claim(said, msgs, 2, []) == NOT_BOOKED_ANSWER  # a refusal from an earlier turn does not count
