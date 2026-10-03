"""Fetching (plan/01-crawler.md step 2). httpx only for now — the test site (DEC-31) is
server-rendered; Crawl4AI/headless rendering is added when a JS-heavy site is hit.
"""

import io
from urllib.parse import urlparse

import httpx
from pypdf import PdfReader

USER_AGENT = "prototypeRAG-crawler/0.1 (+https://github.com/placeholder; learning project, see robots.txt)"


def fetch_text(url: str, client: httpx.Client, timeout: float = 15.0) -> str:
    """GET `url` and return its body text. Raises on a non-2xx response."""
    response = client.get(url, headers={"User-Agent": USER_AGENT}, timeout=timeout)
    response.raise_for_status()
    return response.text


def fetch_robots_txt(base_url: str, client: httpx.Client) -> str | None:
    """GET `<scheme>://<netloc>/robots.txt`. None on any failure or non-200 — never raises."""
    parsed = urlparse(base_url)
    try:
        response = client.get(f"{parsed.scheme}://{parsed.netloc}/robots.txt", headers={"User-Agent": USER_AGENT})
    except httpx.HTTPError:
        return None
    return response.text if response.status_code == 200 else None


def fetch_sitemap(base_url: str, client: httpx.Client) -> str | None:
    """GET `<scheme>://<netloc>/sitemap.xml`. None on any failure or non-200 — never raises."""
    parsed = urlparse(base_url)
    try:
        response = client.get(f"{parsed.scheme}://{parsed.netloc}/sitemap.xml", headers={"User-Agent": USER_AGENT})
    except httpx.HTTPError:
        return None
    return response.text if response.status_code == 200 else None


def extract_pdf_text(pdf_bytes: bytes) -> str:
    """Text from a menu/price-list PDF. "" on any failure (malformed or scanned-image-only)."""
    try:
        reader = PdfReader(io.BytesIO(pdf_bytes))
        return "\n\n".join(page.extract_text() or "" for page in reader.pages)
    except Exception:
        return ""


def make_client(delay_seconds: float = 0.5) -> httpx.Client:
    """One client for a whole crawl. `run.py` reads `_crawl_delay` and sleeps between fetches."""
    client = httpx.Client(follow_redirects=True, timeout=15.0)
    client._crawl_delay = delay_seconds
    return client
