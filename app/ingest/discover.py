"""Page discovery: sitemap parsing, link extraction, robots.txt (plan/01-crawler.md step 1).

No network calls here — this module only parses strings it's given; `run.py` does the
fetching and drives the breadth-first expansion when no sitemap is available.
"""

import xml.etree.ElementTree as ET
from urllib.parse import urldefrag, urljoin, urlparse
from urllib.robotparser import RobotFileParser

from bs4 import BeautifulSoup

_SITEMAP_NS = {"sm": "http://www.sitemaps.org/schemas/sitemap/0.9"}
_SKIP_EXTENSIONS = (".jpg", ".jpeg", ".png", ".gif", ".svg", ".pdf", ".css", ".js", ".ico", ".woff", ".woff2")
_SKIP_PATH_SUBSTRINGS = ("/cart", "/checkout", "/login", "/account", "/search")


def parse_sitemap(xml_text: str) -> list[str]:
    """Return the <loc> URLs from a sitemap.xml string, or [] if it doesn't parse."""
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return []

    locs = [loc.text.strip() for loc in root.findall(".//sm:url/sm:loc", _SITEMAP_NS) if loc.text]
    if locs:
        return locs
    # some sitemaps omit the namespace
    return [loc.text.strip() for loc in root.findall(".//url/loc") if loc.text]


def _bare_domain(netloc: str) -> str:
    netloc = netloc.lower()
    return netloc[4:] if netloc.startswith("www.") else netloc


def same_domain(url: str, base_url: str) -> bool:
    """True if `url` and `base_url` share a domain, ignoring a leading www."""
    return _bare_domain(urlparse(url).netloc) == _bare_domain(urlparse(base_url).netloc)


def is_skippable(url: str) -> bool:
    """True for assets and account/search/cart paths that aren't crawlable content."""
    path = urlparse(url).path.lower()
    if path.endswith(_SKIP_EXTENSIONS):
        return True
    return any(s in path for s in _SKIP_PATH_SUBSTRINGS)


def normalize_url(url: str) -> str:
    """Drop fragment and query, lowercase the netloc, strip a trailing slash. For deduping."""
    url, _ = urldefrag(url)
    parsed = urlparse(url)
    path = parsed.path
    if path != "/" and path.endswith("/"):
        path = path[:-1]
    return f"{parsed.scheme}://{parsed.netloc.lower()}{path}"


def discover_links(html: str, base_url: str) -> list[str]:
    """Internal, non-skippable links found in `html`, resolved absolute and deduped."""
    soup = BeautifulSoup(html, "html.parser")
    seen: set[str] = set()
    urls: list[str] = []
    for a in soup.find_all("a", href=True):
        absolute = urljoin(base_url, a["href"])
        if not same_domain(absolute, base_url) or is_skippable(absolute):
            continue
        normalized = normalize_url(absolute)
        if normalized not in seen:
            seen.add(normalized)
            urls.append(normalized)
    return urls


class RobotsChecker:
    """Wraps stdlib robotparser over an already-fetched robots.txt body."""

    def __init__(self, robots_txt: str, user_agent: str = "*"):
        self.user_agent = user_agent
        self._parser = RobotFileParser()
        self._parser.parse(robots_txt.splitlines())

    def can_fetch(self, url: str) -> bool:
        return self._parser.can_fetch(self.user_agent, url)


def discover_pages(
    start_url: str,
    sitemap_xml: str | None,
    robots_txt: str | None,
    max_pages: int = 200,
    max_depth: int = 3,  # noqa: ARG001 — BFS expansion (depth-aware) happens in run.py
) -> list[str]:
    """The sitemap fast path: same-domain, non-skippable, robots-allowed URLs, capped.

    Falls back to `[start_url]` when there's no sitemap or it doesn't parse — `run.py`
    then expands from there with `discover_links` page by page.
    """
    urls = parse_sitemap(sitemap_xml) if sitemap_xml else []
    if not urls:
        return [start_url]

    urls = [u for u in urls if same_domain(u, start_url) and not is_skippable(u)]
    if robots_txt:
        checker = RobotsChecker(robots_txt)
        urls = [u for u in urls if checker.can_fetch(u)]

    # the page the caller actually pointed at is presumably the most relevant one —
    # never let an arbitrary sitemap order crowd it out when max_pages truncates the list
    start_normalized = normalize_url(start_url)
    urls = [u for u in urls if normalize_url(u) != start_normalized]
    urls.insert(0, start_url)

    return urls[:max_pages]
