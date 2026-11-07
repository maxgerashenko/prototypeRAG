"""Fetching (plan/01-crawler.md step 2). httpx only for now — the test site (DEC-31) is
server-rendered; Crawl4AI/headless rendering is added when a JS-heavy site is hit.
"""

import io
import math
import re
import time
import xml.etree.ElementTree as ET
import zlib
from contextlib import contextmanager
from dataclasses import dataclass
from collections.abc import Callable, Iterator
from functools import lru_cache
from urllib.parse import unquote, urlparse

import httpx
from pypdf import PdfReader

from app.config import get_settings
from app.ingest.discover import BOT_TOKEN, parse_sitemap_index, same_domain

USER_AGENT = f"{BOT_TOKEN}/0.1 (+{get_settings().crawler_contact_url}; learning project, see robots.txt)"
# httpx's default `Accept: */*` gets a 403 from some bot rules (bhbarbershop.com, #121); send
# realistic content negotiation headers but keep our honest User-Agent.
REQUEST_HEADERS = {
    "User-Agent": USER_AGENT,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}
# a menu or price list is well under this; bigger PDFs are brochures/scans not worth the download
MAX_PDF_BYTES = 20_000_000

# Transient failures (5xx, 429, timeouts) get RETRIES more tries: Retry-After when it is a
# plain number of seconds (capped), else 2 s then 4 s. `_sleep` is replaced in tests.
RETRIES = 2
RETRY_BASE_SECONDS = 2.0
MAX_RETRY_AFTER_SECONDS = 60.0
_sleep = time.sleep


class RobotsUnavailable(Exception):
    """robots.txt could not be read (5xx, 429, timeout, connection error) -- the crawl must
    stop and keep the stored data (RFC 9309: unreachable means complete disallow)."""


def _retryable(status_code: int) -> bool:
    return status_code == 429 or status_code >= 500


def _calculate_wait(resp: httpx.Response | None, attempt: int) -> float:
    """Retry-After in seconds (capped) when the server sends a usable number; an HTTP-date,
    junk, a negative or non-finite value falls back to exponential backoff."""
    if resp is not None:
        try:
            value = float(resp.headers.get("retry-after", ""))
        except ValueError:
            value = None
        if value is not None and math.isfinite(value) and value >= 0:
            return min(value, MAX_RETRY_AFTER_SECONDS)
    return RETRY_BASE_SECONDS * (2 ** attempt)


@contextmanager
def _get(url: str, client: httpx.Client, timeout: float) -> Iterator[httpx.Response]:
    """Streamed GET with retries. Yields the first response that isn't worth retrying (or
    the last one), body not read yet, and closes it afterwards; re-raises a timeout or
    connection error after the last try. A failure while the caller reads the body is
    not retried (the response was already handed out)."""
    for attempt in range(RETRIES + 1):
        yielded = False
        try:
            with client.stream("GET", url, headers=REQUEST_HEADERS, timeout=timeout) as response:
                if _retryable(response.status_code) and attempt < RETRIES:
                    wait = _calculate_wait(response, attempt)
                else:
                    yielded = True
                    yield response
                    return
        except (httpx.TimeoutException, httpx.TransportError):
            if yielded or attempt == RETRIES:
                raise
            wait = _calculate_wait(None, attempt)
        _sleep(wait)


def x_robots_noindex(values: list[str]) -> bool:
    """True when an `X-Robots-Tag` header says noindex (or `none`) for every bot or for ours;
    a directive for another bot ("googlebot: noindex") doesn't apply to us."""
    for value in values:
        agent = None
        for part in (p.strip().lower() for p in value.split(",")):
            name, sep, rest = part.partition(":")
            if sep and name.strip() and " " not in name.strip() and name.strip() not in ("unavailable_after",):
                agent, part = name.strip(), rest.strip()
            if agent not in (None, "*", BOT_TOKEN.lower()):
                continue
            if part in ("noindex", "none"):
                return True
    return False


def fetch_text(url: str, client: httpx.Client, timeout: float = 15.0) -> str:
    """GET `url` and return its body text. Raises on a non-2xx response (after retries)."""
    with _get(url, client, timeout) as response:
        response.read()
        response.raise_for_status()
        return response.text


@dataclass
class FetchedDocument:
    """One crawled URL: an HTML page (`html` set) or a PDF (`pdf_text`/`pdf_title` set)."""

    html: str | None = None
    pdf_text: str | None = None
    pdf_title: str | None = None
    noindex: bool = False


def _is_pdf_response(response: httpx.Response) -> bool:
    """Mostly by the response, not the url: "/menu" can serve a PDF, and "/menu.pdf" can
    redirect to an HTML "file not found" page. A .pdf url served with a generic type
    (application/octet-stream, none) still counts -- some hosts are set up that way."""
    content_type = response.headers.get("content-type", "").lower()
    if "application/pdf" in content_type:
        return True
    return response.url.path.lower().endswith(".pdf") and "html" not in content_type and "text/" not in content_type


def fetch_document(url: str, client: httpx.Client, timeout: float = 15.0) -> FetchedDocument:
    """GET `url` as HTML or PDF, decided by the response's Content-Type. Raises on a
    non-2xx response or a PDF over MAX_PDF_BYTES (checked from Content-Length before the
    body is read, and again on the body)."""
    with _get(url, client, timeout) as response:
        response.raise_for_status()
        noindex = x_robots_noindex(response.headers.get_list("x-robots-tag"))
        if not _is_pdf_response(response):
            response.read()
            return FetchedDocument(html=response.text, noindex=noindex)
        if int(response.headers.get("content-length") or 0) > MAX_PDF_BYTES:
            raise ValueError(f"PDF larger than {MAX_PDF_BYTES} bytes")
        body = bytearray()
        for part in response.iter_bytes():
            body.extend(part)
            if len(body) > MAX_PDF_BYTES:
                raise ValueError(f"PDF larger than {MAX_PDF_BYTES} bytes")
    pdf_bytes = bytes(body)
    return FetchedDocument(pdf_text=extract_pdf_text(pdf_bytes), pdf_title=pdf_title(pdf_bytes, url), noindex=noindex)


def fetch_robots_txt(base_url: str, client: httpx.Client) -> str | None:
    """GET `<scheme>://<netloc>/robots.txt` (RFC 9309): its text on 2xx; None on a 4xx other
    than 429 (no robots.txt = no rules). Raises RobotsUnavailable on 429, 5xx, a timeout or
    a connection error after the retries -- the crawl must not go on without the rules."""
    parsed = urlparse(base_url)
    url = f"{parsed.scheme}://{parsed.netloc}/robots.txt"
    try:
        with _get(url, client, 15.0) as response:
            if 200 <= response.status_code < 300:
                response.read()
                return response.text
            if 400 <= response.status_code < 500 and response.status_code != 429:
                return None
            raise RobotsUnavailable(f"robots.txt {response.status_code}")
    except httpx.RequestError as exc:  # timeouts, connection errors, redirect loops
        raise RobotsUnavailable(f"robots.txt {type(exc).__name__}") from exc


# the sitemap protocol's own limit per file; also the cap on what a .gz may inflate to
MAX_SITEMAP_BYTES = 50_000_000
_SITEMAP_ROOTS = ("urlset", "sitemapindex")


def _gunzip_capped(body: bytes) -> bytes | None:
    """Inflate a gzip body, None when it is corrupt or inflates past MAX_SITEMAP_BYTES
    (a small .gz can expand enormously)."""
    decoder = zlib.decompressobj(wbits=31)
    try:
        out = decoder.decompress(body, MAX_SITEMAP_BYTES + 1)
    except zlib.error:
        return None
    return None if len(out) > MAX_SITEMAP_BYTES else out


def _fetch_sitemap_body(url: str, client: httpx.Client) -> str | None:
    """One sitemap's XML text, or None when the request fails, is not 200, or the body is
    not a sitemap (`<urlset>` / `<sitemapindex>`; a soft-404 HTML page or login wall is not).
    Gzip is detected by its magic bytes, not the `.gz` name or Content-Type: a server may
    already have inflated a .xml.gz (Content-Encoding) or serve plain XML under that name."""
    try:
        with _get(url, client, 15.0) as response:
            if response.status_code != 200:
                return None
            body = response.read()
    except httpx.HTTPError:
        return None
    if body[:2] == b"\x1f\x8b":
        body = _gunzip_capped(body)
        if body is None:
            return None
    try:
        root = ET.fromstring(body)
    except ET.ParseError:
        return None
    if root.tag.rsplit("}", 1)[-1] not in _SITEMAP_ROOTS:
        return None
    return body.decode("utf-8", errors="replace")


def fetch_sitemap(
    base_url: str,
    client: httpx.Client,
    declared_urls: list[str] | None = None,
    can_fetch: Callable[[str], bool] = lambda url: True,
) -> str | None:
    """The first valid sitemap: the `Sitemap:` URLs declared in robots.txt (`declared_urls`;
    same domain as `base_url` and allowed by `can_fetch` only, each request preceded by the
    crawl delay), then `<scheme>://<netloc>/sitemap.xml`. Requests retry like pages do; a
    candidate that is not a sitemap (HTML, junk) is passed over for the next one. The text
    is returned decoded (gzip inflated). None when none works -- never raises."""
    parsed = urlparse(base_url)
    default = f"{parsed.scheme}://{parsed.netloc}/sitemap.xml"
    candidates = [u for u in (declared_urls or []) if u != default and same_domain(u, base_url) and can_fetch(u)]
    for i, url in enumerate(candidates + [default]):
        if i:
            time.sleep(client._crawl_delay)
        body = _fetch_sitemap_body(url, client)
        if body is not None:
            return body
    return None


# a big WordPress site lists a dozen or so child sitemaps; more than this in total (over all
# nesting levels) is not a small business's site and would cost a crawl-delay each
MAX_CHILD_SITEMAPS = 20
# the top-level index is depth 0; its children 1, theirs 2 -- deeper indexes are not followed
MAX_SITEMAP_DEPTH = 2


def fetch_child_sitemaps(
    child_urls: list[str],
    client: httpx.Client,
    can_fetch: Callable[[str], bool] = lambda url: True,
    base_url: str | None = None,
) -> list[str]:
    """Bodies of the URL sitemaps under a sitemap index. A child that is itself an index is
    followed down to MAX_SITEMAP_DEPTH levels; at most MAX_CHILD_SITEMAPS sitemaps are
    fetched in all, each URL once (an index listing itself is no loop). Each fetch waits the
    crawl delay first and skips URLs robots.txt disallows (`can_fetch`) or, when `base_url`
    is given, on another domain. A failed or non-sitemap child is skipped -- never raises."""
    bodies: list[str] = []
    visited: set[str] = set()
    fetched = 0
    level = list(child_urls)
    for _depth in range(MAX_SITEMAP_DEPTH):
        next_level: list[str] = []
        for url in level:
            if fetched >= MAX_CHILD_SITEMAPS:
                return bodies
            if url in visited or not can_fetch(url) or (base_url and not same_domain(url, base_url)):
                continue
            visited.add(url)
            time.sleep(client._crawl_delay)
            fetched += 1
            body = _fetch_sitemap_body(url, client)
            if body is None:
                continue
            nested = parse_sitemap_index(body)
            if nested:
                next_level.extend(nested)
            else:
                bodies.append(body)
        level = next_level
    return bodies


_TABLE_ROW_RE = re.compile(r"^\|.*\|[ \t]*$", re.MULTILINE)


def extract_pdf_text(pdf_bytes: bytes) -> str:
    """Text of a menu/price-list PDF. MarkItDown (pdfminer.six) when it finds a table, so a
    row stays on one line ("| Negroni | $13 |") instead of one cell per line (#135, DEC-51);
    otherwise pypdf, which keeps one line per item where MarkItDown can glue them together
    (gnocco.com's line menu: "One choice of:GNOCCOHomemade", 30 of 163 words unsearchable).
    A PDF with a table and prose keeps MarkItDown's text, so its prose may still be glued.
    Runs of blank lines and trailing spaces squeezed. "" on any failure or for a
    scanned-image-only PDF (no OCR yet, plan/01-crawler.md risks)."""
    if b"%PDF-" not in pdf_bytes[:1024]:
        return ""  # MarkItDown would read other bytes as plain text instead of failing
    try:
        text = _markitdown().convert_stream(io.BytesIO(pdf_bytes), file_extension=".pdf").text_content or ""
    except Exception:
        text = ""
    if not _TABLE_ROW_RE.search(text):
        text = _pypdf_text(pdf_bytes) or text
    text = re.sub(r"[ \t]+\n", "\n", text)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def _pypdf_text(pdf_bytes: bytes) -> str:
    """Line-preserving text of every page; "" when pypdf can't read the file."""
    try:
        return "\n".join(page.extract_text() or "" for page in PdfReader(io.BytesIO(pdf_bytes)).pages)
    except Exception:
        return ""


@lru_cache(maxsize=1)
def _markitdown():
    """One converter per process, imported lazily (markitdown pulls in a lot at import)."""
    from markitdown import MarkItDown

    return MarkItDown()


def pdf_title(pdf_bytes: bytes, url: str) -> str:
    """The PDF's own /Title metadata, else its file name ("Bar Menu.pdf" for
    ".../Bar%20Menu.pdf") -- a page title for the pages table."""
    try:
        title = PdfReader(io.BytesIO(pdf_bytes)).metadata.title
    except Exception:
        title = None
    if title and title.strip():
        return title.strip()
    return unquote(urlparse(url).path.rsplit("/", 1)[-1]) or url


def make_client(delay_seconds: float = 0.5) -> httpx.Client:
    """One client for a whole crawl. `run.py` reads `_crawl_delay` and sleeps between
    fetches; `crawl_business` raises it to robots.txt's Crawl-delay when that is longer."""
    client = httpx.Client(follow_redirects=True, timeout=15.0)
    client._crawl_delay = delay_seconds
    return client
