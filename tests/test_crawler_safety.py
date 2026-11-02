"""Crawler safety (Opus crawler review 2026-11-01): robots.txt per RFC 9309 with our own bot
name and wildcards, noindex in any form, retries with backoff, and a failed crawl that keeps
the stored pages instead of deleting them."""

import uuid

import httpx
import pytest
from sqlalchemy import delete, select

from app.db import tenant_session
from app.db.models import Business, Page
from app.ingest import fetch, run
from app.ingest.discover import BOT_TOKEN, RobotsChecker, has_noindex

ROOT = "https://safety.example.com/"


# --- robots.txt rules ------------------------------------------------------------------


def test_our_own_bot_group_applies():
    robots = f"User-agent: {BOT_TOKEN}\nDisallow: /\n\nUser-agent: *\nAllow: /\n"
    assert not RobotsChecker(robots).can_fetch("https://x.com/anything")


def test_wildcard_rules_are_honoured():
    checker = RobotsChecker("User-agent: *\nDisallow: /private*.html\nDisallow: /*.pdf$\n")
    assert not checker.can_fetch("https://x.com/private-prices.html")
    assert not checker.can_fetch("https://x.com/files/menu.pdf")
    assert checker.can_fetch("https://x.com/files/menu.pdf?v=2")
    assert checker.can_fetch("https://x.com/about")


def test_another_bots_group_does_not_apply_and_delays_still_round_up():
    assert RobotsChecker("User-agent: googlebot\nDisallow: /\nCrawl-delay: 9\n").can_fetch("https://x.com/a")
    assert RobotsChecker("User-agent: googlebot\nCrawl-delay: 9\n").crawl_delay() is None
    assert RobotsChecker("User-agent: *\nCrawl-delay: 2.5\n").crawl_delay() == 3.0


@pytest.mark.parametrize("html, expected", [
    ('<meta name="robots" content="noindex, nofollow">', True),
    ('<meta content="noindex" name="robots">', True),
    ("<META NAME=ROBOTS CONTENT=NOINDEX>", True),
    ('<meta name="robots" content="index, follow">', False),
    ('<meta name="description" content="noindex is a word here">', False),
])
def test_noindex_in_any_attribute_order(html, expected):
    assert has_noindex(f"<head>{html}</head>") is expected


# --- fetching: robots outcomes, retries, X-Robots-Tag ----------------------------------


def _client(responses: dict[str, list]) -> httpx.Client:
    """Each path answers with the next item of its list: (status, body, headers) or an exception."""
    def handler(request: httpx.Request) -> httpx.Response:
        queue = responses[request.url.path]
        item = queue.pop(0) if len(queue) > 1 else queue[0]
        if isinstance(item, Exception):
            raise item
        status, body, headers = item
        return httpx.Response(status, text=body, headers={"content-type": "text/html", **headers})

    client = httpx.Client(transport=httpx.MockTransport(handler))
    client._crawl_delay = 0
    return client


@pytest.fixture
def sleeps(monkeypatch):
    seen: list[float] = []
    monkeypatch.setattr(fetch, "_sleep", seen.append)
    return seen


def test_robots_404_means_no_rules(sleeps):
    assert fetch.fetch_robots_txt(ROOT, _client({"/robots.txt": [(404, "", {})]})) is None
    assert sleeps == []


def test_robots_503_aborts_after_retries(sleeps):
    with pytest.raises(fetch.RobotsUnavailable, match="503"):
        fetch.fetch_robots_txt(ROOT, _client({"/robots.txt": [(503, "", {})]}))
    assert sleeps == [2.0, 4.0]  # 3 tries, backoff 2 s then 4 s


def test_robots_timeout_aborts(sleeps):
    with pytest.raises(fetch.RobotsUnavailable):
        fetch.fetch_robots_txt(ROOT, _client({"/robots.txt": [httpx.ReadTimeout("slow")]}))


def test_page_retried_after_a_transient_error_and_retry_after_is_honoured(sleeps):
    client = _client({"/p": [(503, "", {}), (200, "<p>ok</p>", {})], "/q": [(429, "", {"retry-after": "7"}), (200, "q", {})]})
    assert fetch.fetch_text(f"{ROOT}p", client) == "<p>ok</p>"
    assert fetch.fetch_document(f"{ROOT}q", client).html == "q"
    assert sleeps == [2.0, 7.0]


def test_retry_after_is_capped_and_a_date_falls_back_to_backoff(sleeps):
    client = _client({"/p": [(429, "", {"retry-after": "999"}), (429, "", {"retry-after": "Wed, 21 Oct 2026 07:28:00 GMT"}), (200, "ok", {})]})
    assert fetch.fetch_text(f"{ROOT}p", client) == "ok"
    assert sleeps == [fetch.MAX_RETRY_AFTER_SECONDS, 4.0]


def test_page_that_keeps_failing_raises(sleeps):
    with pytest.raises(httpx.HTTPStatusError):
        fetch.fetch_text(f"{ROOT}p", _client({"/p": [(500, "", {})]}))


def test_x_robots_tag_noindex(sleeps):
    client = _client({"/p": [(200, "<p>x</p>", {"x-robots-tag": "nofollow, NOINDEX"})], "/q": [(200, "<p>y</p>", {})]})
    assert fetch.fetch_document(f"{ROOT}p", client).noindex is True
    assert fetch.fetch_document(f"{ROOT}q", client).noindex is False


# --- crawl level: abort on unreadable robots, keep pages after a failed crawl ----------


@pytest.fixture
def business(monkeypatch):
    monkeypatch.setattr(run.time, "sleep", lambda s: None)
    monkeypatch.setattr(fetch, "_sleep", lambda s: None)
    bid = uuid.uuid4()
    with tenant_session(bid) as s:
        s.add(Business(id=bid, name="Safety Spa", domain=f"safety-{bid.hex[:8]}.example.com"))
        for path in ("", "about", "prices", "team"):
            s.add(Page(business_id=bid, url=f"{ROOT}{path}", markdown=f"# {path or 'home'}", content_hash=path or "home"))
    yield bid
    with tenant_session(bid) as s:
        s.execute(delete(Business).where(Business.id == bid))


def _site(monkeypatch, site: dict):
    def handler(request: httpx.Request) -> httpx.Response:
        status, body = site.get(request.url.path, (404, ""))
        return httpx.Response(status, text=body, headers={"content-type": "text/plain" if request.url.path.endswith(".txt") else "text/html"})

    def fake_client(delay_seconds: float = 0.5) -> httpx.Client:
        client = httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=True)
        client._crawl_delay = 0
        return client

    monkeypatch.setattr(run, "make_client", fake_client)


def _urls(bid):
    with tenant_session(bid) as s:
        return sorted(p.url for p in s.scalars(select(Page).where(Page.business_id == bid)))


def test_unreadable_robots_aborts_and_keeps_everything(monkeypatch, business):
    before = _urls(business)
    _site(monkeypatch, {"/robots.txt": (503, "")})
    assert run.crawl_business(business, ROOT, extract_profile=False) == {"aborted": "robots.txt 503"}
    assert _urls(business) == before


def test_site_down_keeps_stored_pages(monkeypatch, business):
    before = _urls(business)
    _site(monkeypatch, {"/robots.txt": (404, "")})  # no rules, but every page fails
    summary = run.crawl_business(business, ROOT, extract_profile=False)
    assert summary["pages_crawled"] == 0 and summary["deleted_pages"] == 0
    assert set(before) <= set(_urls(business))


# --- review round (sonnet5.5_REVIEW 2026-11-01) ----------------------------------------


@pytest.mark.parametrize("html", ['<meta data-name="robots" content="noindex">', '<meta name="robots" data-content="noindex">'])
def test_noindex_ignores_data_attributes(html):
    assert has_noindex(f"<head>{html}</head>") is False


def test_noindex_none_counts():
    assert has_noindex('<head><meta name="robots" content="none"></head>')


@pytest.mark.parametrize("values, expected", [
    (["noindex"], True), (["none"], True), (["nofollow"], False),
    (["googlebot: noindex"], False), (["googlebot: noindex, nofollow"], False),
    ([f"{BOT_TOKEN}: noindex"], True), (["unavailable_after: 25 Jun 2030 15:00:00 PST"], False),
    (["googlebot: nofollow", "noindex"], True),
])
def test_x_robots_tag_directives(values, expected):
    assert fetch.x_robots_noindex(values) is expected


def test_robots_redirect_loop_aborts(sleeps):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(302, headers={"location": "/robots.txt"})

    client = httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=True)
    with pytest.raises(fetch.RobotsUnavailable, match="TooManyRedirects"):
        fetch.fetch_robots_txt(ROOT, client)


def test_failed_body_read_is_not_retried(sleeps):
    class Broken(httpx.SyncByteStream):
        def __iter__(self):
            raise httpx.ReadError("connection dropped mid-body")
            yield b""

    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(200, stream=Broken(), headers={"content-type": "text/html"})

    with pytest.raises(httpx.ReadError):
        fetch.fetch_text(f"{ROOT}p", httpx.Client(transport=httpx.MockTransport(handler)))
    assert len(calls) == 1 and sleeps == []


def test_small_shrink_deletes_but_one_of_four_skips(monkeypatch, business):
    home = '<html><body><main><h1>Home</h1><p>Welcome to the spa.</p><a href="/about">About</a><a href="/prices">Prices</a></main></body></html>'
    page = "<html><body><main><h1>{0}</h1><p>All about {0} at the spa.</p></main></body></html>"
    _site(monkeypatch, {"/robots.txt": (404, ""), "/": (200, home), "/about": (200, page.format("about")), "/prices": (200, page.format("prices"))})
    summary = run.crawl_business(business, ROOT, extract_profile=False)
    assert summary["deleted_pages"] == 1 and summary["deletes_skipped"] is None  # /team is gone: 3 of 4 fetched
    assert f"{ROOT}team" not in _urls(business)

    with tenant_session(business) as s:
        for path in ("team", "spa", "gift-cards"):
            s.add(Page(business_id=business, url=f"{ROOT}{path}", markdown=f"# {path}", content_hash=path))
    _site(monkeypatch, {"/robots.txt": (404, ""), "/": (200, "<html><body><main><h1>Home</h1><p>Only the home page answers today.</p></main></body></html>")})
    summary = run.crawl_business(business, ROOT, extract_profile=False)
    assert summary["deleted_pages"] == 0 and "fetched 1 pages" in summary["deletes_skipped"]
    assert f"{ROOT}gift-cards" in _urls(business)
