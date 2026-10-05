"""Foundation checks against the local Postgres (`docker compose up -d`, `alembic upgrade head`)."""

import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, select, text
from sqlalchemy.exc import IntegrityError

from app.db import engine, tenant_session
from app.db.models import EMBED_DIM, Business, Chunk, Page
from app.main import app


@pytest.fixture
def business_id():
    bid = uuid.uuid4()
    with tenant_session(bid) as s:
        s.add(Business(id=bid, name="Test business"))
    yield bid
    with tenant_session(bid) as s:
        s.execute(delete(Business).where(Business.id == bid))  # cascades to all tenant rows


def _add_page(s, bid, url="https://example.com/menu"):
    page = Page(business_id=bid, url=url, markdown="# Menu", content_hash="h")
    s.add(page)
    s.flush()
    return page


def test_health():
    assert TestClient(app).get("/health").json() == {"status": "ok"}


def test_tenant_session_sets_business_id_only_inside_transaction(business_id):
    with tenant_session(business_id) as s:
        assert s.scalar(text("SELECT current_setting('app.business_id')")) == str(business_id)
    # SET LOCAL semantics: a pooled connection must not carry the previous tenant
    with engine.connect() as conn:
        assert conn.scalar(text("SELECT coalesce(current_setting('app.business_id', true), '')")) == ""


def test_tenant_session_rejects_non_uuid():
    with pytest.raises(ValueError):
        with tenant_session("1; DROP TABLE businesses"):
            pass


def test_tenant_session_rolls_back_on_error(business_id):
    with pytest.raises(RuntimeError):
        with tenant_session(business_id) as s:
            _add_page(s, business_id)
            raise RuntimeError
    with tenant_session(business_id) as s:
        assert s.scalar(select(Page).where(Page.business_id == business_id)) is None


def test_chunk_cannot_reference_page_of_another_business(business_id):
    other = uuid.uuid4()
    with tenant_session(other) as s:
        s.add(Business(id=other, name="Other business"))
        foreign_page_id = _add_page(s, other).id
    try:
        with pytest.raises(IntegrityError):
            with tenant_session(business_id) as s:
                s.add(Chunk(business_id=business_id, page_id=foreign_page_id, kind="scraped",
                            text="x", content_hash="h"))
    finally:
        with tenant_session(other) as s:
            s.execute(delete(Business).where(Business.id == other))


def test_vector_and_fulltext_search(business_id):
    with tenant_session(business_id) as s:
        page = _add_page(s, business_id)
        near, far = [1.0] + [0.0] * (EMBED_DIM - 1), [0.0] * (EMBED_DIM - 1) + [1.0]
        s.add_all([
            Chunk(business_id=business_id, page_id=page.id, kind="scraped", chunk_index=0,
                  text="Open Monday to Friday", embedding=near, content_hash="a"),
            Chunk(business_id=business_id, page_id=page.id, kind="scraped", chunk_index=1,
                  text="Vegan pizza with truffle", embedding=far, content_hash="b"),
        ])
        s.flush()

        top = s.scalar(select(Chunk.text).where(Chunk.business_id == business_id)
                       .order_by(Chunk.embedding.cosine_distance(near)).limit(1))
        assert top == "Open Monday to Friday"

        hit = s.scalar(select(Chunk.text).where(
            Chunk.business_id == business_id,
            Chunk.tsv.op("@@")(text("plainto_tsquery('simple', 'truffle')")),
        ))
        assert hit == "Vegan pizza with truffle"


def test_root_and_old_voice_page_redirect_to_the_voice_app():
    client = TestClient(app, follow_redirects=False)
    for path in ("/", "/web/mic-test.html"):
        r = client.get(path)
        assert r.status_code == 307 and r.headers["location"] == "/web/", path
