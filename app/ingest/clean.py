"""Cleaning: boilerplate removal, HTML → Markdown (plan/01-crawler.md step 3)."""

import hashlib
import re

from bs4 import BeautifulSoup
from markdownify import markdownify as md

_BOILERPLATE_TAGS = ("nav", "footer", "header", "script", "style", "noscript", "svg")
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


def strip_boilerplate(soup: BeautifulSoup) -> None:
    """Mutate `soup`: drop nav/footer/forms and cookie/banner/popup/modal/newsletter blocks."""
    for tag_name in _BOILERPLATE_TAGS:
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


def html_to_markdown(html: str) -> str:
    """Clean `html` and convert its main content to Markdown."""
    soup = BeautifulSoup(html, "html.parser")
    strip_boilerplate(soup)

    container = soup.find("main") or soup.find("article") or soup.body or soup
    markdown = md(str(container), heading_style="ATX", strip=["img"])
    markdown = re.sub(r"\n{3,}", "\n\n", markdown)
    return markdown.strip()


def content_hash(markdown: str) -> str:
    """SHA-256 of the cleaned Markdown — detects unchanged pages on re-crawl (step 7)."""
    return hashlib.sha256(markdown.encode("utf-8")).hexdigest()
