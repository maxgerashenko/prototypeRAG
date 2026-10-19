"""A conversation is about one location (DEC-48, OPEN-23). Postgres only; the question
embedding is faked, so ranking is by keyword."""

import uuid

import pytest
from sqlalchemy import delete

from app.db import tenant_session
from app.db.models import Business, Chunk, Location, Page
from app.rag import retrieve as retrieve_mod
from app.rag.location import default_location_id, format_locations
from app.rag.prompt import build_prompt
from app.voice.tools import search_business_info


@pytest.fixture
def business_id():
    bid = uuid.uuid4()
    with tenant_session(bid) as s:
        s.add(Business(id=bid, name="Bathhouse"))
    yield bid
    with tenant_session(bid) as s:
        s.execute(delete(Business).where(Business.id == bid))


@pytest.fixture(autouse=True)
def fake_embed(monkeypatch):
    monkeypatch.setattr(retrieve_mod, "embed", lambda texts: [[0.0] * 768 for _ in texts])


def _locations(s, bid):
    wb = Location(business_id=bid, name="Williamsburg", address="103 North 10th Street", is_default=True)
    fl = Location(business_id=bid, name="Flatiron", address="14 West 22nd Street")
    s.add_all([wb, fl])
    s.flush()
    return wb, fl


def _chunk(s, bid, text, location_id):
    page = Page(business_id=bid, url=f"https://x.com/{uuid.uuid4()}", markdown="x", content_hash="h")
    s.add(page)
    s.flush()
    s.add(Chunk(business_id=bid, page_id=page.id, kind="scraped", text=text, content_hash=text, location_id=location_id))


def test_default_location_and_locations_block(business_id):
    with tenant_session(business_id) as s:
        wb, fl = _locations(s, business_id)
        assert default_location_id(s, business_id) == wb.id
        block = format_locations(s, business_id, wb.id)
    assert "This conversation is about the location: Williamsburg (103 North 10th Street)." in block
    assert "Other locations of this business: Flatiron (14 West 22nd Street)." in block
    assert "Answer only for this location" in block


def test_no_block_for_a_single_location_or_none(business_id):
    with tenant_session(business_id) as s:
        assert default_location_id(s, business_id) is None
        assert format_locations(s, business_id, None) == ""
        s.add(Location(business_id=business_id, name="Only", is_default=True))
        s.flush()
        assert format_locations(s, business_id, None) == ""


def test_unknown_location_asks_which_one(business_id):
    with tenant_session(business_id) as s:
        _locations(s, business_id)
        block = format_locations(s, business_id, None)
    assert "Other locations" in block and "ask which one is meant" in block


def test_prompt_carries_the_locations_block(business_id):
    messages = build_prompt(None, [], [], "hours?", locations="LOCATIONS BLOCK")
    assert "LOCATIONS BLOCK" in messages[0]["content"]


def test_search_tool_never_returns_another_locations_text(business_id):
    with tenant_session(business_id) as s:
        wb, fl = _locations(s, business_id)
        _chunk(s, business_id, "Williamsburg sauna opens early.", wb.id)
        _chunk(s, business_id, "Flatiron sauna opens late.", fl.id)
        _chunk(s, business_id, "Every sauna requires a towel.", None)
    result = search_business_info(business_id, "sauna")
    assert "Williamsburg sauna" in result and "towel" in result
    assert "Flatiron" not in result
