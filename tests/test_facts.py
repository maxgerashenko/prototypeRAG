"""Facts extraction + merge tests (plan/07-knowledge-quality.md §5, DEC-38). Postgres only:
the LLM call is faked, so these run without LM Studio. The site-chrome text (with the
footer's opening hours, V23) comes from the saved pilot page."""

import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import delete, select

from app.db import tenant_session
from app.db.models import Business, Fact, FactSource, Location, Page
from app.ingest import facts as facts_mod
from app.ingest import run
from app.ingest.clean import clean_page
from app.ingest.facts import (
    EXTRACTOR_VERSION, normalize_text, pages_to_extract, parse_hours, split_sections, validate_fact,
)
from app.ingest.merge import conflicts_report, make_fact_key, normalize_value

FIXTURES = Path(__file__).parent / "fixtures"
HOURS_QUOTE = "Hours 7 days a week 8am to 11:30pm"


@pytest.fixture(scope="module")
def chrome_text() -> str:
    return clean_page((FIXTURES / "abathhouse_williamsburg.html").read_text()).chrome_markdown


@pytest.fixture
def business_id():
    bid = uuid.uuid4()
    with tenant_session(bid) as s:
        s.add(Business(id=bid, name="Bathhouse"))
    yield bid
    with tenant_session(bid) as s:
        s.execute(delete(Business).where(Business.id == bid))


def raw_fact(**kw):
    return {
        "type": "price", "subject": "Day Pass", "attribute": "price", "value": "from $39", "location": "this",
        "statement": "A Bathhouse Day Pass starts at $39.", "quote": "Day Pass, Starts at $39", "confidence": 0.9,
        **kw,
    }


PAGE_TEXT = normalize_text("# Day Pass\n\nDay Pass, **Starts at $39**. The Banya runs between 185°F and 195°F.")
LOC = uuid.uuid4()


# --- validation ----------------------------------------------------------------------


def test_valid_fact_passes_and_quote_ignores_markdown_and_case():
    fact, reason = validate_fact(raw_fact(), PAGE_TEXT, "service", {}, LOC)
    assert reason is None and fact.location_id == LOC and fact.value == "from $39"


@pytest.mark.parametrize("change, reason", [
    ({"quote": "Day Pass, Starts at $49"}, "quote not found in page"),
    ({"value": "from $49"}, "number in value not in quote"),
    ({"type": "discount"}, "unknown type"),
    ({"value": "null"}, "empty or placeholder value"),
    ({"subject": "subject"}, "empty or placeholder subject"),
    ({"location": "Atlantis"}, "unknown location 'Atlantis'"),
])
def test_invalid_facts_are_rejected_with_a_reason(change, reason):
    assert validate_fact(raw_fact(**change), PAGE_TEXT, "service", {}, LOC) == (None, reason)


def test_blog_pages_cannot_state_time_sensitive_facts():
    assert validate_fact(raw_fact(), PAGE_TEXT, "blog", {}, None)[1] == "time-sensitive fact from a blog post"
    banya = raw_fact(type="amenity", subject="Banya", attribute="temperature", value="185°F to 195°F",
                     quote="The Banya runs between 185°F and 195°F")
    assert validate_fact(banya, PAGE_TEXT, "blog", {}, None)[1] is None


def test_location_all_this_and_named():
    other = uuid.uuid4()
    by_lower = {"flatiron": other}
    assert validate_fact(raw_fact(location="all"), PAGE_TEXT, "service", by_lower, LOC)[0].location_id is None
    assert validate_fact(raw_fact(location="Flatiron"), PAGE_TEXT, "service", by_lower, LOC)[0].location_id == other
    assert validate_fact(raw_fact(location="this"), PAGE_TEXT, "service", by_lower, None)[0].location_id is None


def test_parse_hours():
    week = parse_hours("7 days a week 8am to 11:30pm")
    assert week["mon"] == [["08:00", "23:30"]] and week["sun"] == week["mon"] and len(week) == 7
    assert parse_hours("daily 8:00-23:30")["sat"] == [["08:00", "23:30"]]
    assert parse_hours("Mon-Fri 8am to 10pm, Sat 9am to 11pm") is None  # per-day: text only
    assert parse_hours("8am to 11:30pm") is None  # no "every day": not guessed


def test_split_sections_cuts_at_headings_and_hard_when_needed():
    text = "# A\n" + "a" * 50 + "\n# B\n" + "b" * 50 + "\n# C\n" + "c" * 150
    parts = split_sections(text, limit=100)
    assert "".join(parts) == text and all(len(p) <= 100 for p in parts) and len(parts) >= 3
    assert split_sections("short", limit=100) == ["short"]


def test_normalize_value_and_fact_key():
    assert normalize_value("From  $39.") == normalize_value("from 39")
    assert make_fact_key("price", None, " Day  Pass", "Price") == "price|-|day pass|price"


def _page(url="https://x.com/p", page_type="service", stamp=None, **kw):
    return Page(id=uuid.uuid4(), url=url, markdown="m", content_hash="h1", page_type=page_type, extracted_hash=stamp, **kw)


def test_pages_to_extract_skips_unchanged_legal_duplicates_and_untyped():
    done = _page("https://x.com/a", stamp=f"h1:{EXTRACTOR_VERSION}")
    changed = _page("https://x.com/b", stamp=f"old:{EXTRACTOR_VERSION}")
    old_version = _page("https://x.com/c", stamp="h1:0")
    new = _page("https://x.com/d")
    chrome = _page("https://x.com/__site_chrome__", page_type="site_chrome")
    skipped = [_page("https://x.com/e", "legal"), _page("https://x.com/f", duplicate_of=uuid.uuid4()),
               _page("https://x.com/g", None)]
    pages = [done, changed, old_version, new, chrome, *skipped]
    assert [p.url for p in pages_to_extract(pages)] == [p.url for p in sorted([changed, old_version, new, chrome], key=lambda p: p.url)]
    assert done in pages_to_extract(pages, force=True)


# --- extraction + merge against Postgres ---------------------------------------------


class FakeModel:
    """Stands in for chat_json: answers by page text; counts calls."""

    def __init__(self):
        self.calls = 0
        self.answers: dict[str, list[dict]] = {}  # substring of the user message -> raw facts

    def __call__(self, messages, schema, name, model=None):
        self.calls += 1
        user = messages[-1]["content"]
        for needle, facts in self.answers.items():
            if needle in user:
                return {"facts": facts}
        return {"facts": []}


@pytest.fixture
def model(monkeypatch):
    fake = FakeModel()
    monkeypatch.setattr(facts_mod, "chat_json", fake)
    return fake


def _setup(business_id, chrome_text):
    """A business with two locations, the site-chrome page, a Day Pass service page and
    a /memberships page; returns their ids."""
    with tenant_session(business_id) as s:
        wb = Location(business_id=business_id, name="Williamsburg", url="https://x.com/williamsburg", is_default=True)
        fl = Location(business_id=business_id, name="Flatiron", url="https://x.com/flatiron")
        s.add_all([wb, fl])
        s.flush()
        now = datetime.now(UTC)
        pages = {
            "chrome": Page(business_id=business_id, url="https://x.com/__site_chrome__", markdown=chrome_text,
                           content_hash="c1", page_type="site_chrome", retrievable=False),
            "daypass": Page(business_id=business_id, url="https://x.com/day-pass", markdown="# Day Pass\n\nDay Pass, Starts at $39.",
                            content_hash="d1", page_type="service", scraped_at=now - timedelta(days=2)),
            "blogpost": Page(business_id=business_id, url="https://x.com/journal/post",
                             markdown="# Post\n\nThe Day Pass is $45 this week.", content_hash="b1", page_type="blog",
                             scraped_at=now),
        }
        s.add_all(pages.values())
        s.flush()
        return {"wb": wb.id, "fl": fl.id, **{k: p.id for k, p in pages.items()}}


def _facts(business_id):
    with tenant_session(business_id) as s:
        return {(f.type, f.value, f.status): f for f in s.scalars(select(Fact).where(Fact.business_id == business_id))}


def test_hours_come_from_the_site_chrome_for_all_locations(business_id, chrome_text, model):
    """V23: the footer's hours become one business-wide, sourced, structured fact."""
    _setup(business_id, chrome_text)
    model.answers["header/footer"] = [{
        "type": "hours", "subject": "Bathhouse", "attribute": "hours", "value": "7 days a week 8am to 11:30pm",
        "location": "all", "statement": "Bathhouse is open 7 days a week from 8am to 11:30pm.",
        "quote": HOURS_QUOTE, "confidence": 0.95,
    }]
    out = run.extract_facts(business_id)
    assert out["facts_created"] == 1 and out["rejected"] == 0

    with tenant_session(business_id) as s:
        (fact,) = s.scalars(select(Fact).where(Fact.business_id == business_id)).all()
        assert (fact.type, fact.status, fact.location_id) == ("hours", "active", None)
        assert fact.value_json["sun"] == [["08:00", "23:30"]]
        (src,) = s.scalars(select(FactSource).where(FactSource.fact_id == fact.id)).all()
        chrome = s.scalar(select(Page).where(Page.business_id == business_id, Page.page_type == "site_chrome"))
        assert src.page_id == chrome.id and src.page_hash == "c1"


def test_rerun_on_unchanged_pages_changes_nothing(business_id, chrome_text, model):
    _setup(business_id, chrome_text)
    model.answers["Day Pass"] = [raw_fact(quote="Day Pass, Starts at $39")]
    first = run.extract_facts(business_id)
    assert first["facts_created"] == 1
    calls = model.calls
    with tenant_session(business_id) as s:
        before = [(f.id, f.status, f.last_seen_at) for f in s.scalars(select(Fact))]

    again = run.extract_facts(business_id)
    assert model.calls == calls and again["pages_extracted"] == 0 and again["facts_created"] == 0
    with tenant_session(business_id) as s:
        assert [(f.id, f.status, f.last_seen_at) for f in s.scalars(select(Fact))] == before

    # --force redoes the extraction but lands on the same single active fact and source
    run.extract_facts(business_id, force=True)
    with tenant_session(business_id) as s:
        assert [f.status for f in s.scalars(select(Fact))] == ["active"]
        assert len(s.scalars(select(FactSource)).all()) == 1


def test_dry_run_writes_nothing(business_id, chrome_text, model, capsys):
    _setup(business_id, chrome_text)
    model.answers["Day Pass"] = [raw_fact(), raw_fact(quote="made up")]
    run.extract_facts(business_id, dry_run=True)
    out = capsys.readouterr().out
    assert "A Bathhouse Day Pass starts at $39." in out and "quote not found in page" in out
    assert _facts(business_id) == {}
    with tenant_session(business_id) as s:
        assert all(p.extracted_hash is None for p in s.scalars(select(Page)))


def test_conflict_best_page_type_wins_and_loser_is_kept(business_id, chrome_text, model):
    ids = _setup(business_id, chrome_text)
    with tenant_session(business_id) as s:  # the blog post says $45; make it a service page's rival
        s.get(Page, ids["blogpost"]).page_type = "faq"
    model.answers["Starts at $39"] = [raw_fact(quote="Starts at $39")]
    model.answers["$45 this week"] = [raw_fact(value="$45", quote="Day Pass is $45", statement="Day Pass is $45.")]
    run.extract_facts(business_id)

    facts = _facts(business_id)
    assert set(facts) == {("price", "from $39", "active"), ("price", "$45", "conflict")}  # service > faq
    with tenant_session(business_id) as s:
        report = conflicts_report(s, business_id)
    assert report[0].startswith("price · Day Pass · price") and any("'$45'" in line and "journal/post" in line for line in report)


def test_same_value_from_two_pages_is_one_fact_with_two_sources(business_id, chrome_text, model):
    ids = _setup(business_id, chrome_text)
    with tenant_session(business_id) as s:
        s.get(Page, ids["blogpost"]).page_type = "faq"
    model.answers["Starts at $39"] = [raw_fact(quote="Starts at $39")]
    model.answers["$45 this week"] = [raw_fact(value="From $45.", quote="Day Pass is $45")]
    # the blog text has $45; make the second page agree on $39 instead
    with tenant_session(business_id) as s:
        s.get(Page, ids["blogpost"]).markdown = "Day Pass: Starts at $39 (also on the FAQ)."
    model.answers["also on the FAQ"] = [raw_fact(value="From $39", quote="Starts at $39")]
    run.extract_facts(business_id)
    with tenant_session(business_id) as s:
        (fact,) = s.scalars(select(Fact).where(Fact.business_id == business_id)).all()
        assert fact.status == "active"
        assert len(s.scalars(select(FactSource).where(FactSource.fact_id == fact.id)).all()) == 2


def test_owner_confirmed_fact_wins_and_is_never_overwritten(business_id, chrome_text, model):
    _setup(business_id, chrome_text)
    key = make_fact_key("price", None, "Day Pass", "price")
    with tenant_session(business_id) as s:
        s.add(Fact(business_id=business_id, type="price", subject="Day Pass", attribute="price", value="$42",
                   statement="Owner says $42.", fact_key=key, status="active", origin="owner",
                   owner_confirmed_at=datetime.now(UTC)))
    model.answers["Starts at $39"] = [raw_fact(location="all", quote="Starts at $39")]
    run.extract_facts(business_id)
    facts = _facts(business_id)
    assert set(facts) == {("price", "$42", "active"), ("price", "from $39", "conflict")}
    assert facts[("price", "$42", "active")].statement == "Owner says $42."


def test_fact_whose_source_changed_goes_stale_and_losing_value_is_promoted(business_id, chrome_text, model):
    ids = _setup(business_id, chrome_text)
    with tenant_session(business_id) as s:
        s.get(Page, ids["blogpost"]).page_type = "faq"
    model.answers["Starts at $39"] = [raw_fact(quote="Starts at $39")]
    model.answers["$45 this week"] = [raw_fact(value="$45", quote="Day Pass is $45")]
    run.extract_facts(business_id)
    assert ("price", "$45", "conflict") in _facts(business_id)

    # the service page no longer lists a price: its fact loses its only source
    with tenant_session(business_id) as s:
        p = s.get(Page, ids["daypass"])
        p.markdown, p.content_hash = "# Day Pass\n\nCome visit.", "d2"
    model.answers.pop("Starts at $39")
    run.extract_facts(business_id)
    assert set(_facts(business_id)) == {("price", "from $39", "stale"), ("price", "$45", "active")}


def test_failed_page_is_retried_and_does_not_stop_the_others(business_id, chrome_text, model, monkeypatch):
    _setup(business_id, chrome_text)
    real = FakeModel()
    real.answers["Starts at $39"] = [raw_fact(quote="Starts at $39")]

    def flaky(messages, schema, name, model=None):
        if "header/footer" in messages[-1]["content"]:
            raise RuntimeError("LM Studio down")
        return real(messages, schema, name, model)

    monkeypatch.setattr(facts_mod, "chat_json", flaky)
    out = run.extract_facts(business_id)
    assert out["failed"] == 1 and out["facts_created"] == 1
    with tenant_session(business_id) as s:
        chrome = s.scalar(select(Page).where(Page.business_id == business_id, Page.page_type == "site_chrome"))
        assert chrome.extracted_hash is None  # retried next run
