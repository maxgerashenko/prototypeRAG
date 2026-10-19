"""Fetching (plan/01-crawler.md step 2). httpx only for now — the test site (DEC-31) is
server-rendered; Crawl4AI/headless rendering is added when a JS-heavy site is hit.
"""

import io
import re
from dataclasses import dataclass
from urllib.parse import unquote, urlparse

import httpx
from pypdf import PdfReader

USER_AGENT = "prototypeRAG-crawler/0.1 (+https://github.com/placeholder; learning project, see robots.txt)"
# a menu or price list is well under this; bigger PDFs are brochures/scans not worth the download
MAX_PDF_BYTES = 20_000_000


def fetch_text(url: str, client: httpx.Client, timeout: float = 15.0) -> str:
    """GET `url` and return its body text. Raises on a non-2xx response."""
    response = client.get(url, headers={"User-Agent": USER_AGENT}, timeout=timeout)
    response.raise_for_status()
    return response.text


@dataclass
class FetchedDocument:
    """One crawled URL: an HTML page (`html` set) or a PDF (`pdf_text`/`pdf_title` set)."""

    html: str | None = None
    pdf_text: str | None = None
    pdf_title: str | None = None


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
    with client.stream("GET", url, headers={"User-Agent": USER_AGENT}, timeout=timeout) as response:
        response.raise_for_status()
        if not _is_pdf_response(response):
            response.read()
            return FetchedDocument(html=response.text)
        if int(response.headers.get("content-length") or 0) > MAX_PDF_BYTES:
            raise ValueError(f"PDF larger than {MAX_PDF_BYTES} bytes")
        body = bytearray()
        for part in response.iter_bytes():
            body.extend(part)
            if len(body) > MAX_PDF_BYTES:
                raise ValueError(f"PDF larger than {MAX_PDF_BYTES} bytes")
    pdf_bytes = bytes(body)
    return FetchedDocument(pdf_text=extract_pdf_text(pdf_bytes), pdf_title=pdf_title(pdf_bytes, url))


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
    """Text from a menu/price-list PDF, one paragraph per page, runs of blank lines and
    trailing spaces squeezed. "" on any failure or for a scanned-image-only PDF (no OCR
    yet, plan/01-crawler.md risks)."""
    try:
        reader = PdfReader(io.BytesIO(pdf_bytes))
        pages = [page.extract_text() or "" for page in reader.pages]
    except Exception:
        return ""
    text = "\n\n".join(p.strip() for p in pages if p.strip())
    text = re.sub(r"[ \t]+\n", "\n", text)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


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
