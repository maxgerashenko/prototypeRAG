"""Fact chunks + fact-aware retrieval (plan/07-knowledge-quality.md §5.2, §5.5; DEC-47).
Postgres only: the question is embedded by a fake, and chunks have no embeddings, so the
keyword path of the hybrid search is what ranks."""

import uuid

import pytest
from sqlalchemy import delete, select

from app.db import tenant_session
from app.db.models import Business, Chunk, Fact, FactSource, Location, Page
from app.ingest.fact_chunks import sync_fact_chunks
from app.rag import retrieve as retrieve_mod
from app.rag.retrieve import retrieve


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


def _fact(s, bid, statement, status="active", location_id=None, key=None):
    f = Fact(
        business_id=bid, type="price", subject="Day Pass", attribute="price", value="$39", statement=statement,
        fact_key=key or f"price|-|{uuid.uuid4()}", status=status, location_id=location_id,
    )
    s.add(f)
    s.flush()
    return f


def _page(s, bid, markdown="x"):
    p = Page(business_id=bid, url=f"https://x.com/{uuid.uuid4()}", markdown=markdown, content_hash="h")
    s.add(p)
    s.flush()
    return p


def _scraped(s, bid, text, location_id=None):
    p = _page(s, bid)
    c = Chunk(business_id=bid, page_id=p.id, kind="scraped", text=text, content_hash=text, location_id=location_id)
    s.add(c)
    s.flush()
    return p, c


def _fact_chunks(bid):
    with tenant_session(bid) as s:
        return {c.text: c for c in s.scalars(select(Chunk).where(Chunk.business_id == bid, Chunk.kind == "fact"))}


# --- sync ----------------------------------------------------------------------------


def test_only_active_facts_get_a_chunk_and_sync_is_idempotent(business_id):
    with tenant_session(business_id) as s:
        loc = Location(business_id=business_id, name="Flatiron", is_default=True)
        s.add(loc)
        s.flush()
        _fact(s, business_id, "Day Pass starts at $39.", location_id=loc.id)
        for status in ("stale", "conflict", "candidate"):
            _fact(s, business_id, f"{status} fact", status=status)
        stats = sync_fact_chunks(s, business_id)
    assert stats.created == 1
    chunks = _fact_chunks(business_id)
    assert list(chunks) == ["Day Pass starts at $39."] and chunks["Day Pass starts at $39."].location_id is not None

    with tenant_session(business_id) as s:
        again = sync_fact_chunks(s, business_id)
    assert (again.created, again.updated, again.deleted) == (0, 0, 0)


def test_changed_statement_resets_embedding_and_demoted_fact_loses_its_chunk(business_id):
    with tenant_session(business_id) as s:
        keep = _fact(s, business_id, "Day Pass starts at $39.")
        drop = _fact(s, business_id, "Banya is 190F.")
        sync_fact_chunks(s, business_id)
        for c in s.scalars(select(Chunk).where(Chunk.business_id == business_id)):
            c.embedding, c.embed_model = [0.1] * 768, "m"
        keep_id, drop_id = keep.id, drop.id

    with tenant_session(business_id) as s:
        s.get(Fact, keep_id).statement = "Day Pass starts at $42."
        s.get(Fact, drop_id).status = "stale"
        stats = sync_fact_chunks(s, business_id)
    assert (stats.created, stats.updated, stats.deleted) == (0, 1, 1)
    chunks = _fact_chunks(business_id)
    assert list(chunks) == ["Day Pass starts at $42."]
    assert chunks["Day Pass starts at $42."].embedding is None and chunks["Day Pass starts at $42."].embed_model is None


# --- retrieval -----------------------------------------------------------------------


def test_fact_outranks_an_equally_matching_page_chunk(business_id):
    with tenant_session(business_id) as s:
        _scraped(s, business_id, "Sauna hours are varied across the week.")
        _fact(s, business_id, "Sauna hours are varied across the week.")
        sync_fact_chunks(s, business_id)
    with tenant_session(business_id) as s:
        kinds = [c.kind for c in retrieve(s, business_id, "sauna hours", top_n=2)]
    assert kinds == ["fact", "scraped"]


def test_page_chunk_repeating_a_returned_fact_is_dropped_others_stay(business_id):
    """DEC-47: the chunk containing the fact's quote goes; unrelated text stays as fallback."""
    with tenant_session(business_id) as s:
        page, _ = _scraped(s, business_id, "## Day Pass\n\nDay Pass, **Starts at $39**. Towels included.")
        _scraped(s, business_id, "Day pass guests may also book a massage.")
        fact = _fact(s, business_id, "Bathhouse Day Pass starts at $39.")
        s.add(FactSource(business_id=business_id, fact_id=fact.id, page_id=page.id,
                         quote="Day Pass, Starts at $39", page_hash="h"))
        sync_fact_chunks(s, business_id)
    with tenant_session(business_id) as s:
        texts = [c.text for c in retrieve(s, business_id, "day pass", top_n=3)]
    assert texts[0] == "Bathhouse Day Pass starts at $39."
    assert "Day pass guests may also book a massage." in texts
    assert not any("Starts at $39" in t and t != texts[0] for t in texts)


def test_location_filter_keeps_business_wide_and_current_location_only(business_id):
    with tenant_session(business_id) as s:
        a = Location(business_id=business_id, name="Flatiron", is_default=True)
        b = Location(business_id=business_id, name="Williamsburg")
        s.add_all([a, b])
        s.flush()
        _fact(s, business_id, "Flatiron sauna is open late.", location_id=a.id)
        _fact(s, business_id, "Williamsburg sauna is open early.", location_id=b.id)
        _fact(s, business_id, "Every sauna needs a towel.", location_id=None)
        sync_fact_chunks(s, business_id)
        a_id = a.id
    with tenant_session(business_id) as s:
        everything = {c.text for c in retrieve(s, business_id, "sauna", top_n=5)}
        flatiron = {c.text for c in retrieve(s, business_id, "sauna", top_n=5, location_id=a_id)}
    assert len(everything) == 3
    assert flatiron == {"Flatiron sauna is open late.", "Every sauna needs a towel."}
