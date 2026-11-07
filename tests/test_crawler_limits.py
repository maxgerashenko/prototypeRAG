"""Crawler fetch fixes from the 2026-11-01 review: redirects and Content-Type on the BFS path
(#188), one URL spelling per page (#189), size/time limits (#190), a huge Crawl-delay (#191),
max_depth on the sitemap path (#199). Network is mocked with httpx.MockTransport."""

import httpx
import pytest

from app.ingest import fetch, run
from app.ingest.discover import RobotsChecker, discover_pages, normalize_url
from app.ingest.fetch import extract_pdf_text_limited, fetch_document, fetch_robots_txt, fetch_sitemap, fetch_text

SITE = "https://shop.example.com/"


def _client(handler) -> httpx.Client:
    client = httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=True)
    client._crawl_delay = 0
    return client


# --- #188 redirects + Content-Type on the BFS path -------------------------------------


def test_fetch_document_returns_final_url_and_rejects_a_disallowed_redirect():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "shop.example.com" and request.url.path == "/book":
            return httpx.Response(302, headers={"location": "https://widget.other.com/x"})
        if request.url.path == "/old":
            return httpx.Response(301, headers={"location": "/new"})
        return httpx.Response(200, text="<p>hi</p>", headers={"content-type": "text/html"})

    client = _client(handler)
    allow = lambda u: "shop.example.com" in u  # noqa: E731
    doc = fetch_document(f"{SITE}old", client, allow_final=allow)
    assert doc.url == f"{SITE}new" and doc.html == "<p>hi</p>"
    with pytest.raises(ValueError, match="not allowed"):
        fetch_document(f"{SITE}book", client, allow_final=allow)
    assert fetch_document(f"{SITE}book", client).url == "https://widget.other.com/x"  # no check asked for


def test_bfs_parses_a_pdf_link_as_pdf_and_does_not_refetch_failures(monkeypatch):
    calls: list[str] = []

    def fake_fetch(url, client, timeout=15.0, allow_final=None):
        calls.append(url)
        if url == f"{SITE}menu":
            return fetch.FetchedDocument(pdf_text="Negroni 14", pdf_title="Menu", url=url)
        if url == f"{SITE}broken":
            raise httpx.ReadTimeout("slow")
        return fetch.FetchedDocument(html='<a href="/menu">m</a><a href="/broken">b</a>', url=url)

    monkeypatch.setattr(run, "fetch_document", fake_fetch)
    monkeypatch.setattr(run.time, "sleep", lambda s: None)
    client = httpx.Client()
    client._crawl_delay = 0
    urls, fetched = run._expand_by_links(SITE, client, None, 10, 3)
    assert fetched[f"{SITE}menu"].pdf_text == "Negroni 14"  # kept as a PDF, not decoded as text
    assert fetched[f"{SITE}broken"] is None  # the main loop skips it instead of asking again
    assert calls.count(f"{SITE}broken") == 1
    assert f"{SITE}menu" in urls and f"{SITE}broken" in urls


# --- #189 one spelling per page ---------------------------------------------------------


@pytest.mark.parametrize("variant", [
    "https://shop.example.com", "https://shop.example.com/", "http://shop.example.com/",
    "https://www.shop.example.com/", "http://SHOP.example.com/#top", "https://shop.example.com/?utm_source=x&fbclid=1",
])
def test_one_page_one_url(variant):
    assert normalize_url(variant, SITE) == SITE


def test_query_strings_are_kept_except_tracking_and_sorted():
    a = normalize_url("https://shop.example.com/menu?location=williamsburg&b=1&utm_medium=x", SITE)
    assert a == "https://shop.example.com/menu?b=1&location=williamsburg"
    assert normalize_url("https://shop.example.com/menu?location=soho", SITE) != a  # different page


def test_sitemap_urls_are_normalised_and_deduped():
    xml = (
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
        "<url><loc>https://shop.example.com</loc></url><url><loc>http://www.shop.example.com/about/</loc></url>"
        "<url><loc>https://shop.example.com/about</loc></url></urlset>"
    )
    assert discover_pages(SITE, xml, None) == [SITE, f"{SITE}about"]


# --- #199 max_depth on the sitemap path --------------------------------------------------


def test_sitemap_urls_deeper_than_max_depth_are_dropped():
    xml = (
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
        + "".join(f"<url><loc>{SITE}{p}</loc></url>" for p in ("a", "a/b", "a/b/c", "a/b/c/d"))
        + "</urlset>"
    )
    assert discover_pages(SITE, xml, None, max_depth=3) == [SITE, f"{SITE}a", f"{SITE}a/b", f"{SITE}a/b/c"]
    assert f"{SITE}a/b/c/d" in discover_pages(SITE, xml, None, max_depth=4)


# --- #190 size and time limits ------------------------------------------------------------


def test_html_over_the_byte_cap_is_refused(monkeypatch):
    monkeypatch.setattr(fetch, "MAX_HTML_BYTES", 100)
    client = _client(lambda r: httpx.Response(200, content=b"x" * 500, headers={"content-type": "text/html"}))
    with pytest.raises(ValueError):
        fetch_text(SITE, client)
    with pytest.raises(ValueError):
        fetch_document(SITE, client)
    ok = _client(lambda r: httpx.Response(200, text="<p>small</p>", headers={"content-type": "text/html"}))
    assert fetch_text(SITE, ok) == "<p>small</p>"


def test_endless_body_hits_the_total_time_limit(monkeypatch):
    clock = iter(range(0, 1000, 10))
    monkeypatch.setattr(fetch.time, "monotonic", lambda: next(clock))
    monkeypatch.setattr(fetch, "MAX_BODY_SECONDS", 30.0)

    def handler(request):
        return httpx.Response(200, content=iter([b"a"] * 50), headers={"content-type": "text/html"})

    with pytest.raises(TimeoutError):
        fetch_text(SITE, _client(handler))


def test_robots_txt_is_truncated_not_read_whole(monkeypatch):
    monkeypatch.setattr(fetch, "MAX_ROBOTS_BYTES", 40)
    body = "User-agent: *\nDisallow: /a\n" + "# pad\n" * 1000
    client = _client(lambda r: httpx.Response(200, text=body))
    text = fetch_robots_txt(SITE, client)
    assert len(text) == 40 and text.startswith("User-agent: *\nDisallow: /a")


def test_oversized_sitemap_is_skipped_with_a_message(monkeypatch, capsys):
    monkeypatch.setattr(fetch, "MAX_SITEMAP_BYTES", 50)

    def handler(request):
        if request.url.path == "/sitemap.xml":
            return httpx.Response(200, content=b"<urlset>" + b"x" * 500)
        return httpx.Response(404)

    assert fetch_sitemap("https://shop.example.com", _client(handler)) is None
    assert "sitemap skipped" in capsys.readouterr().out


def test_pdf_extraction_runs_in_a_child_and_is_killed_on_timeout(monkeypatch, capsys):
    from tests.test_crawler import _text_pdf

    assert "Negroni 14" in extract_pdf_text_limited(_text_pdf(["Negroni 14"]))
    assert extract_pdf_text_limited(b"not a pdf") == ""
    # a zero timeout stands for a PDF that never finishes: the child is killed, the crawl goes on
    assert extract_pdf_text_limited(_text_pdf(["Negroni 14"]), timeout=0) == ""
    assert "took longer" in capsys.readouterr().out


# --- #191 huge Crawl-delay --------------------------------------------------------------


def test_huge_crawl_delay_aborts_before_any_page_request(monkeypatch, capsys):
    requested: list[str] = []

    def handler(request):
        requested.append(request.url.path)
        if request.url.path == "/robots.txt":
            return httpx.Response(200, text="User-agent: *\nCrawl-delay: 3600\n")
        return httpx.Response(200, text="<p>x</p>")

    monkeypatch.setattr(run, "make_client", lambda delay=0.5: _client(handler))
    summary = run.crawl_business(__import__("uuid").uuid4(), SITE, max_pages=200, extract_profile=False)
    assert "Crawl-delay 3600 s x 200 pages" in summary["aborted"]
    assert requested == ["/robots.txt"]  # nothing else was asked, nothing was slept
    assert "crawl aborted" in capsys.readouterr().out
    assert RobotsChecker("User-agent: *\nCrawl-delay: 3600\n").crawl_delay() == 3600.0
