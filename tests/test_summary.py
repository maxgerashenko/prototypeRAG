"""Business summary tests (plan/07-knowledge-quality.md §6, DEC-39). Postgres only:
the LLM call is faked, so these run without LM Studio. Testimonials come from the saved
pilot page (tests/fixtures/abathhouse_williamsburg.html)."""

import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import delete

from app.db import tenant_session
from app.db.models import Business, BusinessSummary, Page
from app.ingest import summary
from app.ingest.clean import clean_page
from app.ingest.organize import cut_testimonials
from app.rag.prompt import SUMMARY_MAX_CHARS, build_prompt, format_summary

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture(scope="module")
def pilot_markdown() -> str:
    return clean_page((FIXTURES / "abathhouse_williamsburg.html").read_text()).main_markdown


@pytest.fixture
def business_id():
    bid = uuid.uuid4()
    with tenant_session(bid) as s:
        s.add(Business(id=bid, name="Bathhouse"))
    yield bid
    with tenant_session(bid) as s:
        s.execute(delete(Business).where(Business.id == bid))


def _page(url, page_type, markdown="Text.", testimonials=None, **kw):
    return Page(
        id=uuid.uuid4(), url=url, page_type=page_type, markdown=markdown, content_hash="h",
        testimonials=testimonials, scraped_at=datetime.now(UTC), **kw,
    )


GOOD_RAW = {
    "one_liner": "A Brooklyn bathhouse with thermal pools, saunas and a rooftop pool.",
    "description": "A modern bathhouse for recovery and relaxation. Day passes from $39.",
    "highlights": ["Eight thermal pools", "Guided sauna sessions", "8 pools", "Rooftop pool"],
    "guest_themes": ["Friendly staff", "Clean facility", "David loved it"],
    "tone": "Warm, relaxed; words: recovery, ritual",
}


# --- pure logic ----------------------------------------------------------------------


def test_split_testimonials_keeps_quotes_and_holds_back_names(pilot_markdown):
    _, blocks = cut_testimonials(pilot_markdown)
    guest = summary.split_testimonials(blocks)
    assert len(guest.quotes) == 5
    assert guest.quotes[0].startswith("Thoroughly enjoyed my time here")
    assert {"David", "Richa", "Dan"} <= guest.names
    assert not any("David R." in q for q in guest.quotes)


def test_build_summary_input_never_contains_customer_names(pilot_markdown):
    """R16: the attribution lines never reach the model; the quotes do."""
    _, blocks = cut_testimonials(pilot_markdown)
    root = _page("https://www.abathhouse.com/", "landing", markdown="Welcome to Bathhouse.")
    location = _page("https://www.abathhouse.com/williamsburg", "location", pilot_markdown, blocks)
    text, names = summary.build_summary_input("Bathhouse", [root, location])
    assert "Thoroughly enjoyed my time here" in text
    assert "Welcome to Bathhouse." in text and "8 Thermal Pools" in text
    assert "David" in names
    for name in ("David R.", "Richa S.", "Dan P."):
        assert name not in text


def test_select_source_pages_root_about_location_and_testimonial_pages():
    pages = [
        _page("https://x.com/treatments/massage", "service"),  # no testimonials -> out
        _page("https://x.com/rooftop-pool-nyc", "service", testimonials=["block"]),
        _page("https://x.com/williamsburg", "location"),
        _page("https://x.com/", "landing"),
        _page("https://x.com/creators", "about"),
        _page("https://x.com/privacy", "legal", testimonials=["block"]),
        _page("https://x.com/__site_chrome__", "site_chrome"),
        _page("https://x.com/williamsburg-2", "location", duplicate_of=uuid.uuid4()),
    ]
    picked = [p.url for p in summary.select_source_pages(pages)]
    assert picked == [
        "https://x.com/",
        "https://x.com/creators",
        "https://x.com/williamsburg",
        "https://x.com/rooftop-pool-nyc",
    ]


def test_validate_summary_drops_numbers_names_and_cuts_lengths():
    raw = {
        **GOOD_RAW,
        "one_liner": " ".join(["word"] * 30),
        "highlights": ["a", "b", "c", "d", "e", "f"],
        "tone": "null",
    }
    out = summary.validate_summary(raw, names={"David"})
    assert len(out["one_liner"].split()) == 20 and out["one_liner"].endswith("…")
    assert out["description"] == "A modern bathhouse for recovery and relaxation."  # "$39" sentence dropped
    assert out["highlights"] == ["a", "b", "c", "d", "e"]
    assert out["guest_themes"] == ["Friendly staff", "Clean facility"]  # name dropped
    assert out["tone"] is None

    out = summary.validate_summary(GOOD_RAW, names=set())
    assert out["highlights"] == ["Eight thermal pools", "Guided sauna sessions", "Rooftop pool"]


def test_needs_regeneration():
    sources = [_page("https://x.com/", "landing")]
    sources[0].scraped_at = datetime.now(UTC) - timedelta(days=1)
    existing = BusinessSummary(source_page_ids=[sources[0].id], generated_at=datetime.now(UTC))
    assert summary.needs_regeneration(None, sources)
    assert not summary.needs_regeneration(existing, sources)
    assert summary.needs_regeneration(existing, [*sources, _page("https://x.com/about", "about")])
    sources[0].scraped_at = datetime.now(UTC) + timedelta(seconds=1)  # re-scraped with new content
    assert summary.needs_regeneration(existing, sources)


def test_format_summary_attributes_guest_themes_and_fits_the_budget():
    assert format_summary(None) == ""
    s = BusinessSummary(
        one_liner="A Brooklyn bathhouse.", highlights=["Thermal pools"], guest_themes=["Friendly staff"],
        tone="Warm",
    )
    text = format_summary(s)
    assert "About the business: A Brooklyn bathhouse." in text
    assert "Guests often mention: Friendly staff" in text
    assert "guests often say" in text and "never state it as fact" in text

    long = BusinessSummary(
        one_liner=" ".join(["word"] * 20), highlights=["x" * 60] * 5, guest_themes=["y" * 60] * 4, tone="Warm",
    )
    lines = format_summary(long).split("\n")[1:]  # the rule line isn't summary content
    assert len("\n".join(lines)) <= SUMMARY_MAX_CHARS
    assert any(line.startswith("Highlights:") for line in lines)
    assert any(line.startswith("Guests often mention:") for line in lines)


def test_build_prompt_includes_summary_only_when_present():
    s = BusinessSummary(one_liner="A Brooklyn bathhouse.")
    with_summary = build_prompt(None, [], [], "q?", s)[0]["content"]
    without = build_prompt(None, [], [], "q?")[0]["content"]
    assert "About the business: A Brooklyn bathhouse." in with_summary
    assert "Business summary" not in without and "\n\n\n" not in without


# --- summarize_business (Postgres, LLM faked) ----------------------------------------


def test_summarize_business_stores_regenerates_only_on_change_and_respects_owner(
    business_id, pilot_markdown, monkeypatch
):
    calls: list[list[dict]] = []
    monkeypatch.setattr(summary, "chat_json", lambda messages, *a: calls.append(messages) or dict(GOOD_RAW))

    _, blocks = cut_testimonials(pilot_markdown)
    with tenant_session(business_id) as s:
        page = Page(
            business_id=business_id, url="https://www.abathhouse.com/williamsburg", page_type="location",
            markdown=pilot_markdown, content_hash="h1", testimonials=blocks,
        )
        s.add(page)
        s.flush()
        page_id = page.id

    assert summary.summarize_business(business_id) == "generated"
    assert "David R." not in calls[0][1]["content"]
    with tenant_session(business_id) as s:
        row = s.get(BusinessSummary, business_id)
        assert row.source_page_ids == [page_id]
        assert row.guest_themes == ["Friendly staff", "Clean facility"]  # "David" from the testimonials
        assert row.highlights == ["Eight thermal pools", "Guided sauna sessions", "Rooftop pool"]

    assert summary.summarize_business(business_id) == "unchanged"
    assert len(calls) == 1

    with tenant_session(business_id) as s:  # re-crawl changed the page's content
        s.get(Page, page_id).scraped_at = datetime.now(UTC) + timedelta(seconds=1)
    assert summary.summarize_business(business_id) == "generated"
    assert len(calls) == 2

    with tenant_session(business_id) as s:
        row = s.get(BusinessSummary, business_id)
        row.one_liner = "Owner's words."
        row.owner_confirmed_at = datetime.now(UTC)
    assert summary.summarize_business(business_id, force=True) == "owner_confirmed"
    assert len(calls) == 2
    with tenant_session(business_id) as s:
        assert s.get(BusinessSummary, business_id).one_liner == "Owner's words."


def test_summarize_business_without_source_pages(business_id, monkeypatch):
    monkeypatch.setattr(summary, "chat_json", lambda *a: pytest.fail("no LLM call without sources"))
    with tenant_session(business_id) as s:
        s.add(Page(business_id=business_id, url="https://x.com/privacy", page_type="legal", markdown="x", content_hash="h"))
    assert summary.summarize_business(business_id) == "no_sources"
