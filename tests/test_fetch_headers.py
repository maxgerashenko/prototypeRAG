"""Every crawl request carries Accept / Accept-Language and our honest User-Agent (#121)."""

import httpx

from app.ingest import fetch

SITEMAP_INDEX = '<sitemapindex><sitemap><loc>https://x.test/child.xml</loc></sitemap></sitemapindex>'


def _client() -> tuple[httpx.Client, list[httpx.Request]]:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.url.path.endswith("child.xml"):
            return httpx.Response(200, text="<urlset></urlset>", headers={"content-type": "application/xml"})
        if request.url.path.endswith(".xml"):
            return httpx.Response(200, text=SITEMAP_INDEX)
        return httpx.Response(200, text="ok", headers={"content-type": "text/html"})

    client = httpx.Client(transport=httpx.MockTransport(handler))
    client._crawl_delay = 0
    return client, seen


def _assert_headers(requests: list[httpx.Request]) -> None:
    assert requests
    for r in requests:
        assert r.headers["accept"].startswith("text/html")
        assert r.headers["accept-language"]
        assert r.headers["user-agent"] == fetch.USER_AGENT


def test_fetch_text_and_document_send_headers():
    client, seen = _client()
    fetch.fetch_text("https://x.test/", client)
    fetch.fetch_document("https://x.test/page", client)
    _assert_headers(seen)


def test_robots_and_sitemaps_send_headers():
    client, seen = _client()
    fetch.fetch_robots_txt("https://x.test/", client)
    fetch.fetch_sitemap("https://x.test/", client)
    fetch.fetch_child_sitemaps(["https://x.test/child.xml"], client)
    assert len(seen) == 3
    _assert_headers(seen)
