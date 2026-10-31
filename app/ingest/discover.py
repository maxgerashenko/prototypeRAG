"""Page discovery: sitemap parsing, link extraction, robots.txt incl. Crawl-delay
(plan/01-crawler.md step 1; priority order + noindex: plan/07-knowledge-quality.md §4 K1).

No network calls here — this module only parses strings it's given; `run.py` does the
fetching and drives the breadth-first expansion when no sitemap is available.
"""

import math
import re
import xml.etree.ElementTree as ET
from urllib.parse import urldefrag, urljoin, urlparse
from urllib.robotparser import RobotFileParser

from bs4 import BeautifulSoup

_SITEMAP_NS = {"sm": "http://www.sitemaps.org/schemas/sitemap/0.9"}
# .pdf is not here: PDFs (menus, price lists) are crawled -- see is_pdf_url
_SKIP_EXTENSIONS = (".jpg", ".jpeg", ".png", ".gif", ".svg", ".css", ".js", ".ico", ".woff", ".woff2")
_SKIP_PATH_SUBSTRINGS = ("/cart", "/checkout", "/login", "/account", "/search")
# Same prefixes app/ingest/organize.py's classify_page_type uses for page_type='blog' --
# duplicated rather than imported (discover.py has no other dependency on organize.py,
# and this one check is cheap to keep in sync by hand, same tradeoff as migration 0002
# inlining identity.py's domain_of rather than importing it).
_BLOG_PATH_PREFIXES = ("journal", "blog")
DEFAULT_BLOG_CAP = 20


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


def parse_sitemap_index(xml_text: str) -> list[str]:
    """Return the child sitemap URLs from a <sitemapindex> string (Yoast/WordPress serve
    one at /sitemap.xml), or [] if it isn't an index or doesn't parse."""
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return []
    locs = root.findall(".//sm:sitemap/sm:loc", _SITEMAP_NS) or root.findall(".//sitemap/loc")
    return [loc.text.strip() for loc in locs if loc.text]


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


def is_pdf_url(url: str) -> bool:
    """True for a link to a .pdf file (menu, price list). Only the extension is known
    before fetching; `fetch.fetch_document` decides by the response itself."""
    return urlparse(url).path.lower().endswith(".pdf")


def normalize_url(url: str) -> str:
    """Drop fragment and query, lowercase the netloc, strip a trailing slash. For deduping."""
    url, _ = urldefrag(url)
    parsed = urlparse(url)
    path = parsed.path
    if path != "/" and path.endswith("/"):
        path = path[:-1]
    return f"{parsed.scheme}://{parsed.netloc.lower()}{path}"


def _internal_links(html: str, base_url: str) -> list[str]:
    """Same-domain, non-skippable links in `html`, resolved absolute, normalized, deduped."""
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


def discover_links(html: str, base_url: str) -> list[str]:
    """Internal page links in `html` (no PDFs: the BFS fetches these to find more links,
    and a PDF has none -- PDFs come from `discover_pdf_links`)."""
    return [u for u in _internal_links(html, base_url) if not is_pdf_url(u)]


def discover_pdf_links(html: str, base_url: str) -> list[str]:
    """Internal links to PDFs in `html`. Same-domain only, like every other link: a PDF
    on a CDN host (e.g. Squarespace's static1.squarespace.com) is not picked up yet."""
    return [u for u in _internal_links(html, base_url) if is_pdf_url(u)]


def is_blog_path(url: str) -> bool:
    """True for /journal/... or /blog/... -- used only to order and cap the crawl, not
    to decide page_type (that's organize.py's classify_page_type, on the same prefixes)."""
    first_segment = urlparse(url).path.strip("/").split("/", 1)[0].lower()
    return first_segment in _BLOG_PATH_PREFIXES


_NOINDEX_RE = re.compile(
    r'<meta[^>]+name=["\']robots["\'][^>]*content=["\'][^"\']*noindex[^"\']*["\']', re.IGNORECASE
)


def has_noindex(html: str) -> bool:
    """True if `html`'s <head> carries <meta name="robots" content="...noindex...">
    (plan 07 §4 K1: "honour noindex as not retrievable"). A real parse would also need
    to catch an HTTP X-Robots-Tag header -- out of scope here, this only ever sees the
    body."""
    return bool(_NOINDEX_RE.search(html))


def prioritize(urls: list[str], start_url: str, blog_cap: int = DEFAULT_BLOG_CAP) -> list[str]:
    """Priority order (K1): `start_url` first, then every other depth-1 page (where
    location and top-level service pages live, e.g. /williamsburg, /treatments), then
    deeper non-blog pages, then at most `blog_cap` blog posts. The pilot's bug was a
    sitemap that listed the journal before the other 3 locations, so a low --max-pages
    truncated the crawl down to "mostly blog" -- capping the blog here (not just sorting
    it last) means the cap holds even when --max-pages is generous enough to reach it.

    Locations aren't known yet at this point (they're detected from site content by
    organize.py, which runs after the crawl) -- depth-1 pages are a stand-in tier that
    catches them without needing that information up front.
    """
    start_normalized = normalize_url(start_url)
    rest = [u for u in urls if normalize_url(u) != start_normalized]

    depth1, deeper, blog = [], [], []
    for u in rest:
        if is_blog_path(u):
            blog.append(u)
        elif "/" not in urlparse(u).path.strip("/"):
            depth1.append(u)
        else:
            deeper.append(u)

    return [start_url, *depth1, *deeper, *blog[:blog_cap]]


# stdlib robotparser only reads whole-number Crawl-delay values and silently drops
# "Crawl-delay: 2.5" -- rounded up here instead, so a fractional delay is still honoured
_FRACTIONAL_CRAWL_DELAY_RE = re.compile(r"^(\s*crawl-delay\s*:\s*)(\d*\.\d+)\s*$", re.IGNORECASE | re.MULTILINE)


def _round_up_crawl_delays(robots_txt: str) -> str:
    return _FRACTIONAL_CRAWL_DELAY_RE.sub(lambda m: f"{m.group(1)}{math.ceil(float(m.group(2)))}", robots_txt)


def parse_robots_sitemaps(robots_txt: str | None) -> list[str]:
    """URLs of the `Sitemap:` lines in a robots.txt body, in order, without duplicates.
    The directive is independent of user-agent groups, so it can appear anywhere."""
    urls: list[str] = []
    for line in (robots_txt or "").splitlines():
        line = line.split("#", 1)[0].strip()
        key, sep, value = line.partition(":")
        if sep and key.strip().lower() == "sitemap":
            value = value.strip()
            if value.startswith(("http://", "https://")) and value not in urls:
                urls.append(value)
    return urls


class RobotsChecker:
    """Wraps stdlib robotparser over an already-fetched robots.txt body."""

    def __init__(self, robots_txt: str, user_agent: str = "*"):
        self.user_agent = user_agent
        self._parser = RobotFileParser()
        self._parser.parse(_round_up_crawl_delays(robots_txt).splitlines())

    def can_fetch(self, url: str) -> bool:
        return self._parser.can_fetch(self.user_agent, url)

    def crawl_delay(self) -> float | None:
        """Seconds to wait between requests per robots.txt, or None if it sets none.
        `Crawl-delay: N` wins; else `Request-rate: R/S` gives S/R seconds."""
        delay = self._parser.crawl_delay(self.user_agent)
        if delay is not None:
            return float(delay)
        rate = self._parser.request_rate(self.user_agent)
        if rate is not None and rate.requests > 0:
            return rate.seconds / rate.requests
        return None


def discover_pages(
    start_url: str,
    sitemap_xml: str | None,
    robots_txt: str | None,
    max_pages: int = 200,
    max_depth: int = 3,  # noqa: ARG001 — BFS expansion (depth-aware) happens in run.py
    blog_cap: int = DEFAULT_BLOG_CAP,
    child_sitemaps: list[str] | None = None,
) -> list[str]:
    """The sitemap fast path: same-domain, non-skippable, robots-allowed URLs, priority-
    ordered (K1) and capped.

    Falls back to `[start_url]` when there's no sitemap or it doesn't parse — `run.py`
    then expands from there with `discover_links` page by page.

    `child_sitemaps` are the already-fetched bodies of the children of a sitemap index
    (one level, `fetch.fetch_child_sitemaps`); their URLs are added to the main sitemap's.
    """
    urls = parse_sitemap(sitemap_xml) if sitemap_xml else []
    for child in child_sitemaps or []:
        urls.extend(parse_sitemap(child))
    urls = list(dict.fromkeys(urls))
    if not urls:
        return [start_url]

    urls = [u for u in urls if same_domain(u, start_url) and not is_skippable(u)]
    if robots_txt:
        checker = RobotsChecker(robots_txt)
        urls = [u for u in urls if checker.can_fetch(u)]

    # priority order first (K1), *then* truncate to max_pages -- otherwise a generous
    # max_pages still lets an unbounded blog section crowd out location/service pages
    # whenever the sitemap happens to list the blog first.
    return prioritize(urls, start_url, blog_cap)[:max_pages]
