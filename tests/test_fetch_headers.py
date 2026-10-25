"""
bhbarbershop.com's host answers 403 to httpx's default Accept: */*.
Issue #121 requires custom Accept and Accept-Language headers.
This module tests that make_client and per-request fetch functions
correctly set and merge headers on actual network calls.
"""

import httpx
import pytest

from app.ingest.fetch import (
    CRAWL_HEADERS,
    USER_AGENT,
    fetch_document,
    fetch_robots_txt,
    fetch_sitemap,
    fetch_text,
    make_client,
)


def test_fetch_headers_and_redirects() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        path = request.url.path
        if path == "/robots.txt":
            return httpx.Response(200, text="User-agent: *\nAllow: /\n")
        if path == "/sitemap.xml":
            return httpx.Response(301, headers={"location": "https://x.com/sitemap_index.xml"})
        if path == "/sitemap_index.xml":
            return httpx.Response(200, text="<sitemapindex/>")
        if path == "/page":
            return httpx.Response(200, text="<html><body>hi</body></html>", headers={"content-type": "text/html"})
        if path == "/doc":
            return httpx.Response(200, text="<html><body>doc</body></html>", headers={"content-type": "text/html"})
        return httpx.Response(404)

    client = make_client(0, transport=httpx.MockTransport(handler))
    fetch_robots_txt("https://x.com/", client)
    fetch_sitemap("https://x.com/", client)
    fetch_text("https://x.com/page", client)
    fetch_document("https://x.com/doc", client)

    assert {r.url.path for r in requests} == {"/robots.txt", "/sitemap.xml", "/sitemap_index.xml", "/page", "/doc"}
    for r in requests:
        assert r.headers["accept"] == CRAWL_HEADERS["Accept"]
        assert r.headers["accept-language"] == CRAWL_HEADERS["Accept-Language"]
        assert r.headers["user-agent"] == USER_AGENT


def test_user_agent_identity() -> None:
    assert USER_AGENT.startswith("prototypeRAG-crawler/")
    assert "Mozilla" not in USER_AGENT
    assert "Mozilla" not in CRAWL_HEADERS["User-Agent"]


def test_client_default_headers() -> None:
    client = make_client()
    assert client.headers["accept"] != "*/*"
    assert client.headers["accept-language"]
