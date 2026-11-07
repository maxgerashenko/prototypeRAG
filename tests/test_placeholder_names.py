"""A booking is never made under a name the model invented (live calls booked "John Doe")."""

import uuid

import pytest

from app.actions.booking import tools
from app.actions.executor import ActionContext


@pytest.fixture(autouse=True)
def session_exists(monkeypatch):
    monkeypatch.setattr(
        tools.sessions_, "find_open", lambda business_id, ref, now=None: {"ref": "3C785323", "name": "Sauna Polish", "when": "x"}
    )


def ctx():
    return ActionContext(business_id=uuid.uuid4(), conversation_id=None)


@pytest.mark.parametrize("first, last", [
    ("John", "Doe"), ("Caller", ""), ("Caller", "Unknown"), ("unknown", ""), ("Guest", ""), ("N/A", ""),
    ("[Caller's First Name]", "[Caller's Last Name]"), ("<first_name>", ""),
])
def test_join_session_refuses_a_placeholder_name(first, last):
    out = tools.join_session(ctx(), {"session": "3C785323", "first_name": first, "last_name": last})
    assert out == tools.NAME_NEEDED


def test_create_booking_refuses_a_placeholder_name():
    out = tools.create_booking(
        ctx(), {"date": "2030-01-01", "time": "19:00", "first_name": "John", "last_name": "Doe", "phone": "+15550100"}
    )
    assert out == tools.NAME_NEEDED


@pytest.mark.parametrize("first, last", [("John", "Doerr"), ("Johnny", "Doe-Smith"), ("Anna", "")])
def test_real_names_pass_the_check(first, last):
    assert not tools._is_placeholder(first, last)


def test_heard_text_drops_the_unheard_marker():
    from app.db.models import heard_text

    assert heard_text("We close at 5. [not heard: Anything else?]") == "We close at 5."
    assert heard_text("[not heard: Anything else?]") == ""
    assert heard_text("We close at 5.") == "We close at 5."
