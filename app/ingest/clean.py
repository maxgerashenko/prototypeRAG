"""Cleaning: boilerplate removal, HTML → Markdown (plan/01-crawler.md step 3).

plan/07-knowledge-quality.md §4 (K2, DEC-40): site chrome (nav/header/footer) used to be
decomposed and thrown away here, which silently dropped the only copy of the site's
opening hours and the other locations' addresses/phones (they live in the footer, not
on any single page). `clean_page()` now keeps that text, just routed separately from the
main content, so `organize.py` can dedupe it across pages and store it once instead of
re-chunking it on every page.
"""

import hashlib
import re
from dataclasses import dataclass

from bs4 import BeautifulSoup
from markdownify import markdownify as md

_CHROME_TAGS = ("nav", "footer", "header")
_DISCARD_TAGS = ("script", "style", "noscript", "svg")
_BOILERPLATE_KEYWORDS = ("cookie", "banner", "popup", "modal", "newsletter")


def extract_title(html: str) -> str | None:
    """Prefer <title>, fall back to the first <h1>, else None."""
    soup = BeautifulSoup(html, "html.parser")

    title_tag = soup.find("title")
    if title_tag and title_tag.get_text(strip=True):
        return title_tag.get_text(strip=True)

    h1_tag = soup.find("h1")
    if h1_tag and h1_tag.get_text(strip=True):
        return h1_tag.get_text(strip=True)

    return None


def _extract_chrome_tags(soup: BeautifulSoup) -> list[str]:
    """Pop (remove-and-return) nav/footer/header tags from `soup`, converted to Markdown
    each, in document order. Popping (not just reading) them means the main-content pass
    below never sees them again — same end state as the old decompose-and-discard, minus
    the discarding."""
    chrome_markdown: list[str] = []
    for tag_name in _CHROME_TAGS:
        for tag in soup.find_all(tag_name):
            text = md(str(tag), heading_style="ATX", strip=["img"]).strip()
            if text:
                chrome_markdown.append(text)
            tag.decompose()
    return chrome_markdown


def strip_boilerplate(soup: BeautifulSoup) -> None:
    """Mutate `soup`: drop script/style/svg, forms, and cookie/banner/popup/modal/newsletter
    blocks. Chrome tags (nav/footer/header) are handled separately by `_extract_chrome_tags`
    — call that first if you want their text kept; otherwise they're still structural
    noise for a caller that only wants this function's cleanup."""
    for tag_name in _DISCARD_TAGS:
        for tag in soup.find_all(tag_name):
            tag.decompose()

    for form in soup.find_all("form"):
        form.decompose()

    for tag in soup.find_all(True):
        if tag.attrs is None:
            continue  # already decomposed as a descendant of an earlier match in this loop
        if tag.name in ("html", "head", "body", "main", "article"):
            # never drop structural containers on a keyword match — a layout utility class
            # like Squarespace's "has-banner-image" on <body> would otherwise take the
            # whole page with it. Only nested widgets (div/section/aside/...) get checked.
            continue
        attr_values = [c.lower() for c in (tag.get("class") or [])]
        if tag.get("id"):
            attr_values.append(tag.get("id").lower())
        if any(keyword in value for keyword in _BOILERPLATE_KEYWORDS for value in attr_values):
            tag.decompose()


@dataclass(frozen=True)
class CleanedPage:
    """Output of `clean_page()`: main content and site chrome, kept apart (DEC-40 K2)."""

    main_markdown: str
    chrome_markdown: str  # "" if the page had no nav/footer/header, or none had text
    language: str | None  # <html lang="..">, as given (not validated/normalized)


def extract_og_site_name(html: str) -> str | None:
    """<meta property="og:site_name" content="..">, stripped; None if absent/empty.

    plan/07-knowledge-quality.md §3 (A3): the business's brand name ("Bathhouse") is a
    site-wide signal like this one, not a page's own title -- a location page's title is
    about that *location* ("Bathhouse Williamsburg | Sauna, Steam & Cold Plunge
    Brooklyn"), so taking the brand from it needs a title-suffix heuristic that's more
    failure-prone than this tag when the site provides it.
    """
    soup = BeautifulSoup(html, "html.parser")
    tag = soup.find("meta", property="og:site_name")
    content = tag.get("content") if tag else None
    return content.strip() or None if isinstance(content, str) else None


def extract_html_lang(html: str) -> str | None:
    """<html lang="..">, stripped; None if absent or empty."""
    soup = BeautifulSoup(html, "html.parser")
    html_tag = soup.find("html")
    lang = html_tag.get("lang") if html_tag else None
    return lang.strip() or None if isinstance(lang, str) else None


def clean_page(html: str) -> CleanedPage:
    """Clean `html` into (main content, site chrome, language). Chrome tags are popped
    from the tree before the rest of the boilerplate removal runs, so they contribute to
    neither `main_markdown` nor get silently discarded (K2)."""
    soup = BeautifulSoup(html, "html.parser")
    language = extract_html_lang(html)
    chrome_blocks = _extract_chrome_tags(soup)
    strip_boilerplate(soup)

    container = soup.find("main") or soup.find("article") or soup.body or soup
    main_markdown = md(str(container), heading_style="ATX", strip=["img"])
    main_markdown = re.sub(r"\n{3,}", "\n\n", main_markdown).strip()

    chrome_markdown = re.sub(r"\n{3,}", "\n\n", "\n\n".join(chrome_blocks)).strip()
    return CleanedPage(main_markdown=main_markdown, chrome_markdown=chrome_markdown, language=language)


def html_to_markdown(html: str) -> str:
    """Clean `html` and convert its main content to Markdown. Kept as the simple entry
    point for callers that only want the main text; `clean_page()` also returns the site
    chrome and language for the organize step."""
    return clean_page(html).main_markdown


def content_hash(markdown: str) -> str:
    """SHA-256 of the cleaned Markdown — detects unchanged pages on re-crawl (step 7)."""
    return hashlib.sha256(markdown.encode("utf-8")).hexdigest()
