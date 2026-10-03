"""Crawler unit tests on saved fixtures (plan/01-crawler.md task: "no network in tests").

Fixtures were captured from the real stage-1 pilot business (DEC-31, abathhouse.com).
"""

import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy import delete, select

from app.db import tenant_session
from app.db.models import Business, Chunk, Page
from app.ingest import run
from app.ingest.chunk import ChunkDraft, chunk_markdown, chunk_text, split_by_headers
from app.ingest.clean import content_hash, extract_title, html_to_markdown
from app.ingest.discover import (
    RobotsChecker,
    discover_links,
    discover_pages,
    is_skippable,
    normalize_url,
    same_domain,
)
from app.ingest.store import delete_pages_not_in, replace_chunks, upsert_page

FIXTURES = Path(__file__).parent / "fixtures"
HOMEPAGE_URL = "https://www.abathhouse.com/williamsburg"


@pytest.fixture(scope="module")
def homepage_html() -> str:
    return (FIXTURES / "abathhouse_williamsburg.html").read_text()


@pytest.fixture(scope="module")
def sitemap_xml() -> str:
    return (FIXTURES / "abathhouse_sitemap.xml").read_text()


@pytest.fixture(scope="module")
def robots_txt() -> str:
    return (FIXTURES / "abathhouse_robots.txt").read_text()


@pytest.fixture(scope="module")
def homepage_markdown(homepage_html: str) -> str:
    return html_to_markdown(homepage_html)


# --- discover.py --------------------------------------------------------------------


def test_same_domain_handles_www_without_the_lstrip_bug():
    assert same_domain("https://wonderful.com/a", "https://wonderful.com/b")  # not "onderful.com"
    assert same_domain("https://www.abathhouse.com/x", "https://abathhouse.com/y")
    assert not same_domain("https://evil.com", "https://abathhouse.com")


def test_is_skippable():
    assert is_skippable("https://x.com/menu.pdf")
    assert is_skippable("https://x.com/account/settings")
    assert not is_skippable("https://x.com/journal/sauna-benefits")


def test_normalize_url():
    assert normalize_url("https://X.com/a/#frag") == "https://x.com/a"
    assert normalize_url("https://x.com/?q=1") == "https://x.com/"


def test_discover_links_on_real_homepage(homepage_html: str):
    links = discover_links(homepage_html, HOMEPAGE_URL)
    assert len(links) > 10
    assert all(link.startswith("https://www.abathhouse.com") for link in links)


def test_discover_pages_prioritizes_start_url(sitemap_xml: str, robots_txt: str):
    pages = discover_pages(HOMEPAGE_URL, sitemap_xml, robots_txt, max_pages=8)
    assert pages[0] == HOMEPAGE_URL  # must survive truncation regardless of sitemap order
    assert len(pages) == len(set(pages))
    assert len(pages) == 8


def test_discover_pages_falls_back_without_sitemap(robots_txt: str):
    assert discover_pages(HOMEPAGE_URL, None, robots_txt) == [HOMEPAGE_URL]


# --- clean.py ------------------------------------------------------------------------


def test_extract_title(homepage_html: str):
    assert extract_title(homepage_html) == "Bathhouse Williamsburg | Sauna, Steam & Cold Plunge Brooklyn"


def test_html_to_markdown_strips_scripts_and_nav(homepage_markdown: str):
    assert "function(" not in homepage_markdown
    assert "@font-face" not in homepage_markdown
    assert "Sauna" in homepage_markdown


def test_strip_boilerplate_does_not_eat_body_on_layout_utility_classes():
    """Regression: Squarespace's <body class="has-banner-image"> must not be decomposed
    just because "banner" is a substring — that previously wiped the whole page."""
    html = """
    <html><body class="has-banner-image">
      <main id="page"><article><p>Real content that must survive.</p></article></main>
    </body></html>
    """
    assert "Real content that must survive." in html_to_markdown(html)


def test_content_hash_stable_and_sensitive():
    h1, h2 = content_hash("a"), content_hash("a")
    assert h1 == h2
    assert h1 != content_hash("b")


# --- chunk.py --------------------------------------------------------------------------


def test_split_by_headers_attributes_body_to_the_correct_heading():
    """Regression: an earlier draft attributed each body to the *next* header, not its own."""
    markdown = "intro text\n\n# Heading1\nbody1 text\n\n# Heading2\nbody2 text"
    assert split_by_headers(markdown) == [
        (None, "intro text"),
        ("Heading1", "body1 text"),
        ("Heading2", "body2 text"),
    ]


def test_chunk_overlap_starts_on_a_word_and_is_separated():
    """Regression: the overlap used to start mid-word and was glued to the next chunk
    with no separator ("...sauna isThe pool")."""
    text = "\n\n".join(["alpha " * 249 + "alpha", "BETA " + "beta " * 300])
    second = chunk_text(text, target_tokens=400)[1]
    overlap, _, rest = second.partition("\n\n")
    assert overlap.split()[0] == "alpha"  # whole word, not "pha"
    assert rest.startswith("BETA")


def test_bfs_never_queues_robots_disallowed_links(monkeypatch):
    """Regression: disallowed links were skipped when fetching the frontier but still
    added to the crawl list, so crawl_business fetched them anyway (R10)."""
    html = '<a href="https://x.com/private">p</a><a href="https://x.com/ok">o</a>'
    monkeypatch.setattr(run, "fetch_text", lambda url, client, timeout=15.0: html)
    monkeypatch.setattr(run.time, "sleep", lambda s: None)
    client = SimpleNamespace(_crawl_delay=0)
    urls, _ = run._expand_by_links("https://x.com/", client, RobotsChecker("User-agent: *\nDisallow: /private\n"), 10, 3)
    assert "https://x.com/private" not in urls
    assert "https://x.com/ok" in urls


def test_chunk_markdown_on_real_homepage(homepage_markdown: str):
    drafts = chunk_markdown(homepage_markdown)
    assert len(drafts) > 0
    assert [d.chunk_index for d in drafts] == list(range(len(drafts)))  # continuous, 0-based
    assert all(d.text.strip() for d in drafts)


# --- store.py (needs Postgres) -------------------------------------------------------


@pytest.fixture
def business_id():
    bid = uuid.uuid4()
    with tenant_session(bid) as s:
        s.add(Business(id=bid, name="Crawler Test"))
    yield bid
    with tenant_session(bid) as s:
        s.execute(delete(Business).where(Business.id == bid))


def test_upsert_page_tracks_changes_by_content_hash(business_id):
    with tenant_session(business_id) as s:
        _, changed = upsert_page(s, business_id, "https://x/a", "T", "md", "h1")
    assert changed is True

    with tenant_session(business_id) as s:
        _, changed = upsert_page(s, business_id, "https://x/a", "T", "md", "h1")
    assert changed is False  # same hash -> no-op

    with tenant_session(business_id) as s:
        page, changed = upsert_page(s, business_id, "https://x/a", "T2", "md v2", "h2")
    assert changed is True
    assert page.title == "T2"


def test_replace_chunks_is_a_full_replace(business_id):
    with tenant_session(business_id) as s:
        page, _ = upsert_page(s, business_id, "https://x/b", "T", "md", "h")
        page_id = page.id
        replace_chunks(s, business_id, page_id, [
            ChunkDraft(section_heading="A", text="one", chunk_index=0),
            ChunkDraft(section_heading="A", text="two", chunk_index=1),
        ])

    with tenant_session(business_id) as s:
        replace_chunks(s, business_id, page_id, [ChunkDraft(section_heading="A", text="only", chunk_index=0)])
        rows = s.scalars(select(Chunk).where(Chunk.page_id == page_id)).all()
    assert [r.text for r in rows] == ["only"]


def test_delete_pages_not_in_cascades_to_chunks(business_id):
    with tenant_session(business_id) as s:
        keep, _ = upsert_page(s, business_id, "https://x/keep", "K", "md", "h")
        gone, _ = upsert_page(s, business_id, "https://x/gone", "G", "md", "h")
        replace_chunks(s, business_id, gone.id, [ChunkDraft(section_heading=None, text="bye", chunk_index=0)])

    with tenant_session(business_id) as s:
        deleted = delete_pages_not_in(s, business_id, keep_urls=["https://x/keep"])
        remaining_urls = {p.url for p in s.scalars(select(Page).where(Page.business_id == business_id)).all()}
        remaining_chunks = s.scalars(select(Chunk).where(Chunk.business_id == business_id)).all()

    assert deleted == 1
    assert remaining_urls == {"https://x/keep"}
    assert remaining_chunks == []
