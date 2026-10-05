"""Crawler unit tests on saved fixtures (plan/01-crawler.md task: "no network in tests").

Fixtures were captured from the real stage-1 pilot business (DEC-31, abathhouse.com).
"""

import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy import delete, select

from app.db import tenant_session
from app.db.models import Business, Chunk, Location, Page
from app.ingest import organize, run
from app.ingest.chunk import ChunkDraft, chunk_markdown, chunk_text, split_by_headers
from app.ingest.clean import clean_page, content_hash, extract_html_lang, extract_og_site_name, extract_title, html_to_markdown
from app.ingest.discover import (
    RobotsChecker,
    discover_links,
    discover_pages,
    has_noindex,
    is_blog_path,
    is_skippable,
    normalize_url,
    prioritize,
    same_domain,
)
from app.ingest.identity import domain_of, site_root
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


def test_discover_pages_priority_order_fixes_k1_blog_crowding_out_locations(sitemap_xml: str, robots_txt: str):
    """K1 regression: the real pilot sitemap lists the journal before the other 3
    locations, so the old order (sitemap order, no blog cap) took the start page + 7
    journal posts at --max-pages 8 and never reached /flatiron, /atlantic-ave,
    /philadelphia, /treatments/*, etc. Fixed two ways: (1) no blog post can appear before
    every depth-1 page does, so a small max_pages no longer gets crowded out entirely by
    the blog; (2) at the --max-pages 80 this plan's re-crawl actually uses, there's room
    for all 38 depth-1 pages (so all 4 locations) well before the blog's cap is even hit.
    """
    small = discover_pages(HOMEPAGE_URL, sitemap_xml, robots_txt, max_pages=8)
    assert small[0] == HOMEPAGE_URL
    assert sum(1 for p in small if is_blog_path(p)) == 0  # no room left for blog posts at max_pages=8

    full = discover_pages(HOMEPAGE_URL, sitemap_xml, robots_txt, max_pages=80)
    assert "https://www.abathhouse.com/flatiron" in full
    assert "https://www.abathhouse.com/atlantic-ave" in full
    assert "https://www.abathhouse.com/philadelphia" in full
    assert "https://www.abathhouse.com/treatments/massage" in full or any(
        "/treatments/" in p for p in full
    )


def test_is_blog_path():
    assert is_blog_path("https://x.com/journal/sauna-benefits")
    assert is_blog_path("https://x.com/blog/post")
    assert not is_blog_path("https://x.com/treatments/massage")


def test_prioritize_orders_depth1_before_deeper_before_capped_blog():
    start = "https://x.com/williamsburg"
    urls = [
        "https://x.com/journal/a", "https://x.com/journal/b", "https://x.com/journal/c",
        "https://x.com/treatments/massage",  # deeper, non-blog
        "https://x.com/flatiron",  # depth-1
        start,
    ]
    ordered = prioritize(urls, start, blog_cap=2)
    assert ordered[0] == start
    assert ordered[1] == "https://x.com/flatiron"
    assert "https://x.com/treatments/massage" in ordered[1:3] or "https://x.com/treatments/massage" in ordered
    assert ordered.index("https://x.com/treatments/massage") < ordered.index("https://x.com/journal/a")
    assert sum(1 for u in ordered if is_blog_path(u)) == 2  # capped from 3 to 2


def test_has_noindex():
    assert has_noindex('<head><meta name="robots" content="noindex, nofollow"></head>')
    assert not has_noindex("<head><title>fine</title></head>")


# --- clean.py ------------------------------------------------------------------------


def test_extract_title(homepage_html: str):
    assert extract_title(homepage_html) == "Bathhouse Williamsburg | Sauna, Steam & Cold Plunge Brooklyn"


def test_html_to_markdown_strips_scripts_and_nav(homepage_markdown: str):
    assert "function(" not in homepage_markdown
    assert "@font-face" not in homepage_markdown
    assert "Sauna" in homepage_markdown


def test_clean_page_keeps_chrome_text_apart_instead_of_discarding_it(homepage_html: str):
    """K2 regression: the footer's hours and the other 3 locations' addresses must
    survive somewhere once clean.py stops decomposing nav/header/footer unread."""
    cleaned = clean_page(homepage_html)
    assert cleaned.main_markdown == html_to_markdown(homepage_html)  # same main text as before
    assert "8am to 11:30pm" in cleaned.chrome_markdown  # V23's hours
    assert "Philadelphia" in cleaned.chrome_markdown and "Flatiron" in cleaned.chrome_markdown
    assert "function(" not in cleaned.chrome_markdown  # scripts still dropped, not kept as chrome
    assert cleaned.language == "en-US"


def test_extract_html_lang(homepage_html: str):
    assert extract_html_lang(homepage_html) == "en-US"
    assert extract_html_lang("<html><body>no lang</body></html>") is None


def test_extract_og_site_name(homepage_html: str):
    # the pilot's brand (A3): og:site_name = "Bathhouse", not the page's own title
    # ("Bathhouse Williamsburg | Sauna, Steam & Cold Plunge Brooklyn")
    assert extract_og_site_name(homepage_html) == "Bathhouse"
    assert extract_og_site_name("<html><head></head></html>") is None


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


# --- organize.py (no network; pure functions over fixture/synthetic text) -------------


def test_detect_site_chrome_needs_a_repeated_block_on_at_least_3_pages():
    a = "Nav\n\nFooter hours: 8am-11pm"
    assert organize.detect_site_chrome([a, a]) == ""  # only 2 pages -- below min_pages=3
    assert organize.detect_site_chrome([a, a, a]) == a
    assert organize.detect_site_chrome([]) == ""


def test_detect_site_chrome_drops_blocks_unique_to_one_page():
    shared = "Nav link"
    pages = [f"{shared}\n\nonly on page {i}" for i in range(3)]
    result = organize.detect_site_chrome(pages)
    assert result == shared
    assert "only on page" not in result


def test_detect_locations_from_chrome_on_the_real_pilot_footer(homepage_html: str):
    """K2/K3: all 4 open locations, with postal addresses and (where linked to their own
    page, not a Google Maps link) a url_path, found straight from the real footer."""
    chrome = clean_page(homepage_html).chrome_markdown
    candidates = {c.name: c for c in organize.detect_locations_from_chrome(chrome)}
    assert set(candidates) == {"Philadelphia", "Atlantic Ave", "Flatiron", "Williamsburg"}
    assert candidates["Williamsburg"].address == "103 North 10th Street, Brooklyn, NY 11249"
    assert candidates["Williamsburg"].url_path == "/williamsburg"
    assert candidates["Flatiron"].url_path == "/flatiron"


def test_slugify():
    assert organize.slugify("Atlantic Ave") == "atlantic-ave"
    assert organize.slugify("  Williamsburg  ") == "williamsburg"


def test_classify_page_type_url_rules():
    slugs = {"williamsburg", "flatiron"}
    cases = {
        "https://x.com/": "landing",
        "https://x.com/williamsburg": "location",
        "https://x.com/journal/post": "blog",
        "https://x.com/blog/post": "blog",
        "https://x.com/privacy-policy": "legal",
        "https://x.com/terms-of-service": "legal",
        "https://x.com/careers": "careers",
        "https://x.com/contact": "contact",
        "https://x.com/faq": "faq",
        "https://x.com/health-and-safety": "policy",
        "https://x.com/treatments/massage": "service",
        "https://x.com/day-pass": "service",
        "https://x.com/coming-soon-chicago": "event",
        "https://x.com/some-random-page": "other",
    }
    for url, expected in cases.items():
        assert organize.classify_page_type(url, slugs) == expected, url


def test_is_retrievable():
    assert organize.is_retrievable("service")
    assert organize.is_retrievable("location")
    assert not organize.is_retrievable("blog")
    assert not organize.is_retrievable("legal")
    assert not organize.is_retrievable("landing")
    assert not organize.is_retrievable("site_chrome")
    assert not organize.is_retrievable("service", noindex=True)  # noindex overrides page_type
    assert not organize.is_retrievable("service", is_duplicate=True)


def test_extract_published_at():
    assert organize.extract_published_at("Posted on October 1, 2026 by the team").year == 2026
    assert organize.extract_published_at("no date here") is None


def test_strip_markdown_links():
    assert organize.strip_markdown_links("[Sauna Benefits](/journal/sauna-benefits)") == "Sauna Benefits"
    assert organize.strip_markdown_links("Plain heading") == "Plain heading"


def test_is_junk_chunk_catches_previous_next_and_short_link_only_blocks():
    """K5 regression: Squarespace's "Previous/Next" post links became 11-char junk chunks."""
    assert organize.is_junk_chunk("[Previous](/journal/a)")
    assert organize.is_junk_chunk("Next")
    assert organize.is_junk_chunk("Read More")
    assert organize.is_junk_chunk("short")
    assert not organize.is_junk_chunk("A full paragraph of real content that is long enough to keep.")


def test_filter_junk_chunks_drops_junk_and_renumbers():
    drafts = [
        ChunkDraft(section_heading="[Sauna](/journal/sauna)", text="A real, sufficiently long paragraph of content here.", chunk_index=0),
        ChunkDraft(section_heading=None, text="[Previous](/journal/a)", chunk_index=1),
        ChunkDraft(section_heading=None, text="Another real, sufficiently long paragraph of content here too.", chunk_index=2),
    ]
    kept = organize.filter_junk_chunks(drafts)
    assert [d.chunk_index for d in kept] == [0, 1]
    assert kept[0].section_heading == "Sauna"  # link stripped


def test_cut_testimonials_on_the_real_williamsburg_page(homepage_html: str):
    """K6 regression: 5 customer testimonials used to be mixed into the amenities chunk."""
    main_markdown = clean_page(homepage_html).main_markdown
    cleaned, cut = organize.cut_testimonials(main_markdown)
    assert len(cut) == 1
    assert "David R." not in cleaned and "Reviews" not in cleaned
    assert "Banya runs between 185" in cleaned  # real amenity facts untouched


def test_chunk_prefix_and_prefix_chunk_drafts():
    prefix = organize.chunk_prefix("Bathhouse", "Williamsburg")
    assert prefix == "Bathhouse Williamsburg — "
    drafts = [ChunkDraft(section_heading="Massage", text="From $154", chunk_index=0)]
    prefixed = organize.prefix_chunk_drafts(drafts, prefix)
    assert prefixed[0].text == "Bathhouse Williamsburg — From $154"
    assert prefixed[0].section_heading == "Massage"  # heading untouched, only text is tagged


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


def test_rechunk_page_keeps_testimonials_for_the_summary(business_id, homepage_html: str):
    """Review finding on milestone 1: cut testimonials were discarded; now stored on the
    page (plan 07 §6) -- also for a non-retrievable page -- and never in its chunks."""
    main_markdown = clean_page(homepage_html).main_markdown
    with tenant_session(business_id) as s:
        page, _ = upsert_page(s, business_id, "https://x/home", "Home", main_markdown, "h")
        run._rechunk_page(s, business_id, page, "Bathhouse")
        chunk_texts = [c.text for c in s.scalars(select(Chunk).where(Chunk.page_id == page.id)).all()]
        stored = list(page.testimonials)

        page.retrievable = False
        run._rechunk_page(s, business_id, page, "Bathhouse")
        hidden_chunks = s.scalars(select(Chunk).where(Chunk.page_id == page.id)).all()

    assert len(stored) == 1 and "David R." in stored[0]
    assert chunk_texts and not any("David R." in t for t in chunk_texts)
    assert hidden_chunks == []
    with tenant_session(business_id) as s:
        assert s.get(Page, page.id).testimonials == stored  # persisted, not only in memory


def test_profile_placeholders_become_null_and_title_gives_fallback_name():
    from app.ingest.profile import _none_if_placeholder, name_from_title

    # the pilot's extraction stored the field's own name as its value (voice greeting
    # then said "you've reached business_name")
    assert _none_if_placeholder("name", "business_name") is None
    assert _none_if_placeholder("name", " Null ") is None
    assert _none_if_placeholder("email", "unknown") is None
    assert _none_if_placeholder("name", "Bathhouse") == "Bathhouse"
    assert _none_if_placeholder("languages", ["en"]) == ["en"]
    assert name_from_title("Bathhouse Williamsburg | Sauna, Steam & Cold Plunge Brooklyn") == "Bathhouse Williamsburg"
    assert name_from_title("Washed — Born Again — Bathhouse") == "Washed"
    assert name_from_title("Plain Name") == "Plain Name"
    assert name_from_title(None) is None and name_from_title("  ") is None


# --- identity.py ----------------------------------------------------------------------


def test_domain_of_strips_www_as_a_prefix_not_a_charset():
    assert domain_of("https://www.abathhouse.com/williamsburg") == "abathhouse.com"
    assert domain_of("https://wonderful.com/a") == "wonderful.com"  # not "onderful.com"


def test_domain_of_keeps_other_subdomains():
    assert domain_of("https://shop.example.com/path") == "shop.example.com"


def test_domain_of_drops_port_and_trailing_dot_and_lowercases():
    assert domain_of("https://ABATHHOUSE.com:8443/x") == "abathhouse.com"
    assert domain_of("https://abathhouse.com./x") == "abathhouse.com"


def test_domain_of_leaves_idn_as_is():
    # neither converted to punycode nor decoded from it -- whatever form the URL carries
    assert domain_of("https://münchen.de/") == "münchen.de"
    assert domain_of("https://xn--mnchen-3ya.de/") == "xn--mnchen-3ya.de"


def test_domain_of_invalid_url_returns_none():
    assert domain_of("not a url") is None
    assert domain_of("") is None
    assert domain_of("/just/a/path") is None


def test_site_root_resets_path_and_keeps_host_as_given():
    assert site_root("https://www.abathhouse.com/williamsburg") == "https://www.abathhouse.com/"
    assert site_root("http://shop.example.com:8080/a/b") == "http://shop.example.com:8080/"
    assert site_root("not a url") is None


# --- run.py: business/location identity (no network) ----------------------------------


def _cleanup_business(bid: uuid.UUID) -> None:
    with tenant_session(bid) as s:
        s.execute(delete(Business).where(Business.id == bid))  # cascades to locations


def test_resolve_business_reuses_same_business_for_same_domain():
    url_a = "https://www.identitytest.example.com/a"
    url_b = "https://identitytest.example.com/b"
    bid1 = run.resolve_business(url_a, None)
    try:
        bid2 = run.resolve_business(url_b, None)
        assert bid2 == bid1  # same domain (www-stripped) -> same business, regardless of path (A1)
    finally:
        _cleanup_business(bid1)


def test_resolve_business_rejects_business_id_for_a_different_existing_domain():
    bid1 = run.resolve_business("https://conflict-one.example.com/", None)
    try:
        with pytest.raises(SystemExit):
            run.resolve_business("https://conflict-one.example.com/", uuid.uuid4())
    finally:
        _cleanup_business(bid1)


def test_resolve_business_rejects_business_id_that_already_has_a_different_domain():
    bid = run.resolve_business("https://conflict-two.example.com/", None)
    try:
        with pytest.raises(SystemExit):
            run.resolve_business("https://other-domain.example.com/", bid)
    finally:
        _cleanup_business(bid)


# --- organize.py: locations from content, never from the start path (A1, DEC-37) ------


def test_organize_business_creates_one_default_location_when_no_address_block_exists():
    """Single-location business, no footer address list at all -- DEC-37 still requires
    exactly one default location, named after the business (fallback, not the start path)."""
    url = "https://singlelocationtest.example.com/"
    bid = run.resolve_business(url, None)
    try:
        with tenant_session(bid) as s:
            business = s.get(Business, bid)
            business.name = "Singleton Spa"
            s.add(Page(business_id=bid, url=url, title="Home", markdown="# Welcome", content_hash="h1"))

        organize.organize_business(bid, url, "https://singlelocationtest.example.com/")

        with tenant_session(bid) as s:
            locations = s.scalars(select(Location).where(Location.business_id == bid)).all()
        assert len(locations) == 1
        assert locations[0].is_default is True
        assert locations[0].name == "Singleton Spa"
        assert locations[0].url == url
    finally:
        _cleanup_business(bid)


def test_organize_business_detects_locations_from_chrome_not_start_path():
    """A4/A1 regression target: the old ensure_location() turned the start URL into a
    location by itself; organize_business must instead read the (deduped) site chrome
    for every location the footer lists, and leave the one matching the pre-existing
    default (Williamsburg, matching the start URL here) as the default."""
    root = "https://multiloc.example.com/"
    chrome = (
        "[Flatiron](/flatiron)\n\n[Williamsburg](/williamsburg)\n\n"
        "[Flatiron](/flatiron)  \n14 West 22nd Street  \nNew York, NY 10010\n\n"
        "[Williamsburg](/williamsburg)  \n103 North 10th Street  \nBrooklyn, NY 11249"
    )
    bid = run.resolve_business(f"{root}williamsburg", None)
    try:
        with tenant_session(bid) as s:
            # 3 pages carrying the identical chrome -- detect_site_chrome's threshold
            # (>= 3 pages) needs that many before a block counts as site-wide chrome.
            s.add(Page(business_id=bid, url=f"{root}williamsburg", title="W", markdown="# Williamsburg",
                       content_hash="h1", chrome_markdown=chrome))
            s.add(Page(business_id=bid, url=f"{root}flatiron", title="F", markdown="# Flatiron",
                       content_hash="h2", chrome_markdown=chrome))
            s.add(Page(business_id=bid, url=f"{root}about", title="About", markdown="# About us",
                       content_hash="h3", chrome_markdown=chrome))

        result = organize.organize_business(bid, f"{root}williamsburg", root)
        assert result.locations_found == 2

        with tenant_session(bid) as s:
            locs = {l.name: l for l in s.scalars(select(Location).where(Location.business_id == bid)).all()}
            pages = {p.url: p for p in s.scalars(select(Page).where(Page.business_id == bid)).all()}

        assert locs["Williamsburg"].is_default is True
        assert locs["Flatiron"].is_default is False
        assert locs["Flatiron"].address == "14 West 22nd Street, New York, NY 10010"
        assert locs["Flatiron"].url == f"{root}flatiron"
        assert pages[f"{root}williamsburg"].page_type == "location"
        assert pages[f"{root}williamsburg"].location_id == locs["Williamsburg"].id
        assert pages[f"{root}about"].page_type == "about" or pages[f"{root}about"].page_type == "other"
        assert pages[f"{root}about"].location_id is None  # business-wide, not a location page

        # idempotent: re-running organize on the same, unchanged pages touches nothing
        result2 = organize.organize_business(bid, f"{root}williamsburg", root)
        assert result2.organized_page_ids == []
    finally:
        _cleanup_business(bid)
