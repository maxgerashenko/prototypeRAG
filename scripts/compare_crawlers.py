"""Issue #116: compare app/ingest/ with Scrapy (crawl) and MarkItDown (PDF, HTML).

Not part of the app -- needs extra packages, run from the repo root:

  uv run --with scrapy --with 'markitdown[pdf]' python scripts/compare_crawlers.py crawl URL [--max-pages 40]
  uv run --with 'markitdown[pdf]' python scripts/compare_crawlers.py pdf FILE_OR_URL [...]

`crawl` runs our discovery/fetch/clean (no DB) and a Scrapy spider with the same page
cap, delay and robots handling, then prints pages found, content size after cleaning, and
the time. `pdf` prints our extract_pdf_text next to MarkItDown's for the same PDFs.
"""

import argparse
import io
import json
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from urllib.parse import urlparse

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.ingest.clean import clean_page  # noqa: E402
from app.ingest.discover import (  # noqa: E402
    RobotsChecker,
    discover_links,
    discover_pages,
    discover_pdf_links,
    parse_sitemap_index,
)
from app.ingest.fetch import (  # noqa: E402
    extract_pdf_text,
    fetch_child_sitemaps,
    fetch_document,
    fetch_robots_txt,
    fetch_sitemap,
    make_client,
)
from app.ingest.run import _expand_by_links  # noqa: E402

DELAY = 0.5


def ours(start_url: str, max_pages: int) -> dict:
    t0 = time.time()
    client = make_client(DELAY)
    origin = urlparse(start_url)
    base = f"{origin.scheme}://{origin.netloc}"
    robots_txt = fetch_robots_txt(base, client)
    sitemap_xml = fetch_sitemap(base, client)
    checker = RobotsChecker(robots_txt) if robots_txt else None
    delay = checker.crawl_delay() if checker else None
    if delay and delay > client._crawl_delay:
        client._crawl_delay = delay
    children = parse_sitemap_index(sitemap_xml) if sitemap_xml else []
    child_bodies = (
        fetch_child_sitemaps(children, client, checker.can_fetch if checker else lambda u: True, base)
        if children
        else []
    )
    urls = discover_pages(start_url, sitemap_xml, robots_txt, max_pages, 3, child_sitemaps=child_bodies)
    via = "sitemap"
    fetched: dict[str, str] = {}
    if len(urls) <= 1:
        via = "links"
        urls, fetched = _expand_by_links(start_url, client, checker, max_pages, 3)
    pages, pdfs, chars = {}, set(), 0
    for u in urls:
        try:
            html = fetched.get(u)
            if html is None:
                doc = fetch_document(u, client)
                time.sleep(client._crawl_delay)
                html = doc.html
            if html is None:
                continue
            cleaned = clean_page(html)
            pages[u] = len(cleaned.main_markdown) + len(cleaned.chrome_markdown)
            chars += pages[u]
            pdfs.update(discover_pdf_links(html, u))
        except Exception as exc:  # noqa: BLE001
            print(f"  ours: skip {u}: {exc}", file=sys.stderr)
    return {"via": via, "sitemap_children": len(children), "pages": pages, "pdfs": sorted(pdfs),
            "chars": chars, "secs": round(time.time() - t0, 1), "crawl_delay": delay}


def scrapy_run(start_url: str, max_pages: int) -> dict:
    # a child process: Scrapy's Twisted reactor can only start once per process
    code = f"""
import json, sys, time
import scrapy
from scrapy.crawler import CrawlerProcess
from scrapy.spiders import SitemapSpider
from urllib.parse import urlparse
sys.path.insert(0, {str(Path(__file__).resolve().parent.parent)!r})
from app.ingest.clean import clean_page
from app.ingest.discover import is_skippable, same_domain
START = {start_url!r}
out = {{"pages": {{}}, "pdfs": [], "via": "links", "statuses": {{}}}}
class S(SitemapSpider):
    name = "cmp"
    # robots.txt Sitemap: lines + /sitemap.xml; index files are followed by default
    sitemap_urls = [START.rstrip("/").split("//")[0] + "//" + urlparse(START).netloc + "/robots.txt",
                    START.rstrip("/").split("//")[0] + "//" + urlparse(START).netloc + "/sitemap.xml"]
    def _parse_sitemap(self, response):
        out["via"] = "sitemap"
        return super()._parse_sitemap(response)
    def sitemap_filter(self, entries):
        for e in entries:
            if same_domain(e["loc"], START) and not is_skippable(e["loc"]):
                yield e
    sitemap_rules = [("", "parse_page")]
    def start_requests(self):
        yield from super().start_requests()
        yield scrapy.Request(START, callback=self.parse_page, priority=10)
    def parse_page(self, response):
        out["statuses"][response.url] = response.status
        if "html" not in response.headers.get("Content-Type", b"").decode().lower():
            return
        c = clean_page(response.text)
        out["pages"][response.url] = len(c.main_markdown) + len(c.chrome_markdown)
        # an item per page: CLOSESPIDER_PAGECOUNT would also count robots.txt and sitemap responses
        yield {{"url": response.url}}
        if out["via"] != "sitemap" and response.meta.get("depth", 0) < 3:
            for href in response.css("a::attr(href)").getall():
                url = response.urljoin(href).split("#")[0]
                if not same_domain(url, START) or is_skippable(url):
                    continue
                if url.lower().endswith(".pdf"):
                    if url not in out["pdfs"]:
                        out["pdfs"].append(url)
                    continue
                yield response.follow(url, callback=self.parse_page)
t0 = time.time()
p = CrawlerProcess(settings={{
    "ROBOTSTXT_OBEY": True, "DOWNLOAD_DELAY": {DELAY}, "RANDOMIZE_DOWNLOAD_DELAY": False,
    "CONCURRENT_REQUESTS": 1, "DEPTH_LIMIT": 3, "CLOSESPIDER_ITEMCOUNT": {max_pages},
    "USER_AGENT": "prototypeRAG-crawler/0.1 (compare #116)", "LOG_LEVEL": "WARNING",
    "TELNETCONSOLE_ENABLED": False}})
p.crawl(S); p.start()
out["secs"] = round(time.time() - t0, 1)
out["chars"] = sum(out["pages"].values())
print("JSON>>" + json.dumps(out))
"""
    # a file, not `-c`: Scrapy reads the callbacks' source (inspect.getsource)
    with tempfile.NamedTemporaryFile("w", suffix="_spider.py", delete=False) as f:
        f.write(code)
    proc = subprocess.run([sys.executable, f.name], capture_output=True, text=True, timeout=900)
    Path(f.name).unlink()
    if "--verbose" in sys.argv:
        print(proc.stderr[-3000:])
    for line in proc.stdout.splitlines():
        if line.startswith("JSON>>"):
            return json.loads(line[6:])
    return {"error": proc.stderr[-800:]}


def crawl(args) -> None:
    a = ours(args.url, args.max_pages)
    b = scrapy_run(args.url, args.max_pages)
    if "error" in b:
        print(b["error"])
        return
    sa, sb = set(a["pages"]), set(b["pages"])
    norm = lambda s: {u.rstrip("/") for u in s}  # noqa: E731
    na, nb = norm(sa), norm(sb)
    print(f"== {args.url} (cap {args.max_pages})")
    print(f"ours:   via {a['via']}, {len(sa)} pages, {a['chars']} chars, {a['secs']}s, "
          f"sitemap children {a['sitemap_children']}, pdfs linked {len(a['pdfs'])}")
    print(f"scrapy: via {b['via']}, {len(sb)} pages, {b['chars']} chars, {b['secs']}s, pdfs linked {len(b['pdfs'])}")
    print(f"only ours {len(na - nb)}, only scrapy {len(nb - na)}, both {len(na & nb)}")
    for label, urls in (("only ours", na - nb), ("only scrapy", nb - na)):
        for u in sorted(urls)[:8]:
            print(f"  {label}: {u}")
    print("pdfs ours:", a["pdfs"][:6])


def pdf(args) -> None:
    from markitdown import MarkItDown

    import httpx

    md = MarkItDown()
    for src in args.files:
        if src.startswith("http"):
            data = httpx.get(src, follow_redirects=True, timeout=30, headers={"User-Agent": "prototypeRAG-compare"}).content
        else:
            data = Path(src).read_bytes()
        t = time.time()
        o = extract_pdf_text(data)
        to = time.time() - t
        t = time.time()
        try:
            m = md.convert_stream(io.BytesIO(data), file_extension=".pdf").text_content
        except Exception as exc:  # noqa: BLE001
            m = f"ERROR {exc}"
        tm = time.time() - t
        print(f"\n===== {src} ({len(data)} bytes)\nours: {len(o)} chars {to:.2f}s | markitdown: {len(m)} chars {tm:.2f}s")
        print("--- ours (first 1200)\n" + o[:1200])
        print("--- markitdown (first 1200)\n" + m[:1200])


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("crawl")
    c.add_argument("url")
    c.add_argument("--max-pages", type=int, default=40)
    c.add_argument("--verbose", action="store_true")
    p = sub.add_parser("pdf")
    p.add_argument("files", nargs="+")
    args = ap.parse_args()
    crawl(args) if args.cmd == "crawl" else pdf(args)
