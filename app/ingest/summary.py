"""Business summary from the website's own marketing text and testimonials
(plan/07-knowledge-quality.md §6, DEC-39): `python -m app.ingest.summary --business-id …`
or `python -m app.ingest.run --url … --only summary`; also run at the end of every crawl.

Sources are the business's own pages only (DEC-11): the site root, about and location
pages, plus any page with testimonials published on it (`pages.testimonials`, cut by
organize.cut_testimonials). Never Google reviews (R9).

The summary is not a source of facts: code drops any sentence or item with a digit (no
prices, hours or numbers) and customer names are removed from the testimonials before
the model sees them, then checked again on the output (R16).
Regenerated only when its source pages changed; an owner-confirmed summary is never
overwritten (the dashboard's "suggested update" is step 5).
"""

import argparse
import re
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from urllib.parse import urlparse

from sqlalchemy import select

from app.db import tenant_session
from app.db.models import Business, BusinessSummary, Page
from app.ingest.organize import cut_testimonials
from app.ingest.profile import _is_placeholder, is_placeholder_name
from app.llm import chat_json

SUMMARY_SCHEMA = {
    "type": "object",
    "properties": {
        "one_liner": {"type": ["string", "null"]},
        "description": {"type": ["string", "null"]},
        "highlights": {"type": "array", "items": {"type": "string"}},
        "guest_themes": {"type": "array", "items": {"type": "string"}},
        "tone": {"type": ["string", "null"]},
    },
    "required": ["one_liner", "description", "highlights", "guest_themes", "tone"],
    "additionalProperties": False,
}

_SYSTEM_PROMPT = (
    "Write a short summary of a business from its own website text and the guest quotes "
    "published on it.\n"
    "- one_liner: what the business is, at most 20 words.\n"
    "- description: what it is and what it's like, at most 80 words.\n"
    "- highlights: up to 5 short phrases about what makes it stand out.\n"
    "- guest_themes: up to 4 short phrases about what guests often mention, only from the "
    "guest quotes (empty if there are none).\n"
    "- tone: how the business talks about itself — formality and a few brand words.\n"
    "Never include prices, opening hours, temperatures, phone numbers or any other numbers, "
    "and never any person's name. Use only the text given; use null or [] when it says nothing."
)

# Page types whose text describes the business as a whole (plan 07 §6 "home/about/location
# pages, marketing copy"). The site root is typed `landing` by organize, so it's picked by
# URL instead. Any other page counts only for the testimonials on it.
SOURCE_PAGE_TYPES = {"about", "location"}
_NEVER_SOURCE_TYPES = {"site_chrome", "legal", "careers"}

PAGE_TEXT_CHARS = 2500  # per page: the intro and first sections carry the marketing copy
MAX_INPUT_CHARS = 12000  # same budget as profile.py's extraction

LIMITS = {"one_liner": 20, "description": 80}  # words
MAX_HIGHLIGHTS = 5
MAX_GUEST_THEMES = 4

# One testimonial in a cut block (organize._TESTIMONIAL_SECTION_RE): "* ## “quote”" then
# the attribution line ("David R.").
_QUOTE_RE = re.compile(r"^\*\s*##\s*[“\"](.+?)[”\"]?\s*$\n\n\s*([^\n*][^\n]*)", re.MULTILINE)
_DIGIT_RE = re.compile(r"\d")
_SENTENCE_RE = re.compile(r"(?<=[.!?])\s+")


@dataclass(frozen=True)
class GuestQuotes:
    quotes: list[str]
    names: set[str]  # name words from the attribution lines, never sent to the model


def split_testimonials(blocks: list[str]) -> GuestQuotes:
    """Quotes without their attribution lines. Names are collected (words of 2+ letters,
    "David R." -> {"David"}) so the output can be checked for them too."""
    quotes, names = [], set()
    for block in blocks:
        for m in _QUOTE_RE.finditer(block):
            quotes.append(m.group(1).strip())
            names.update(w for w in re.findall(r"[^\W\d_]{2,}", m.group(2)))
    return GuestQuotes(quotes, names)


def _is_site_root(url: str) -> bool:
    return urlparse(url).path.strip("/").lower() in ("", "home")


def select_source_pages(pages: list[Page]) -> list[Page]:
    """Site root first, then about and location pages, then other pages that carry
    testimonials; duplicates and site chrome/legal/careers never."""

    def rank(p: Page) -> int:
        if _is_site_root(p.url):
            return 0
        return 1 if p.page_type in SOURCE_PAGE_TYPES else 2

    picked = [
        p for p in pages
        if p.duplicate_of is None
        and p.page_type not in _NEVER_SOURCE_TYPES
        and (_is_site_root(p.url) or p.page_type in SOURCE_PAGE_TYPES or p.testimonials)
    ]
    return sorted(picked, key=lambda p: (rank(p), p.url))


def build_summary_input(business_name: str | None, pages: list[Page]) -> tuple[str, set[str]]:
    """The user message for the model, and the customer names kept out of it."""
    parts = [f"Business: {business_name}"] if business_name else []
    all_quotes: list[str] = []
    names: set[str] = set()
    for p in pages:
        if p.page_type in SOURCE_PAGE_TYPES or _is_site_root(p.url):
            text, _ = cut_testimonials(p.markdown)
            parts.append(f"## Page {urlparse(p.url).path or '/'}\n{text.strip()[:PAGE_TEXT_CHARS]}")
        guest = split_testimonials(p.testimonials or [])
        all_quotes.extend(q for q in guest.quotes if q not in all_quotes)
        names |= guest.names
    if all_quotes:
        parts.append("## Guest quotes published on the website\n" + "\n".join(f"- {q}" for q in all_quotes))
    return "\n\n".join(parts)[:MAX_INPUT_CHARS], names


def _mentions(text: str, names: set[str]) -> bool:
    words = set(re.findall(r"[^\W\d_]+", text))
    return bool(words & names)


def _clean_text(value, key: str, names: set[str]) -> str | None:
    """Sentences with a digit or a customer name dropped, then cut to the word limit."""
    if not isinstance(value, str) or _is_placeholder(key, value):
        return None
    kept = [s for s in _SENTENCE_RE.split(value.strip()) if not _DIGIT_RE.search(s) and not _mentions(s, names)]
    words = " ".join(kept).split()
    if not words:
        return None
    if len(words) > LIMITS[key]:
        words = words[: LIMITS[key]]
        words[-1] = words[-1].rstrip(",;:—-") + "…"
    return " ".join(words)


def _clean_items(values, key: str, names: set[str], limit: int) -> list[str]:
    items = []
    for v in values or []:
        if not isinstance(v, str) or _is_placeholder(key, v):
            continue
        v = v.strip().rstrip(".")
        if v and not _DIGIT_RE.search(v) and not _mentions(v, names) and v not in items:
            items.append(v)
    return items[:limit]


def _clean_tone(value, names: set[str]) -> str | None:
    if not isinstance(value, str) or _is_placeholder("tone", value):
        return None
    value = value.strip()
    if not value or _DIGIT_RE.search(value) or _mentions(value, names):
        return None
    return " ".join(value.split()[:20])


def validate_summary(raw: dict, names: set[str]) -> dict:
    """Code-side rules (local models are weaker, R1): word/item limits, no numbers, no
    customer names, placeholders dropped."""
    return {
        "one_liner": _clean_text(raw.get("one_liner"), "one_liner", names),
        "description": _clean_text(raw.get("description"), "description", names),
        "highlights": _clean_items(raw.get("highlights"), "highlights", names, MAX_HIGHLIGHTS),
        "guest_themes": _clean_items(raw.get("guest_themes"), "guest_themes", names, MAX_GUEST_THEMES),
        "tone": _clean_tone(raw.get("tone"), names),
    }


def needs_regeneration(existing: BusinessSummary | None, sources: list[Page]) -> bool:
    """New, a different set of source pages, or a source page re-scraped with changed
    content (upsert_page only moves `scraped_at` when `content_hash` changes)."""
    if existing is None or existing.generated_at is None:
        return True
    if set(existing.source_page_ids or []) != {p.id for p in sources}:
        return True
    return any(p.scraped_at > existing.generated_at for p in sources)


def summarize_business(business_id: uuid.UUID, force: bool = False) -> str:
    """Generate and store the summary if needed. Returns what happened, for the CLI."""
    with tenant_session(business_id) as session:
        business = session.get(Business, business_id)
        if business is None:
            raise LookupError(f"unknown business {business_id}")
        existing = session.get(BusinessSummary, business_id)
        if existing is not None and existing.owner_confirmed_at is not None:
            return "owner_confirmed"
        pages = session.scalars(select(Page).where(Page.business_id == business_id)).all()
        sources = select_source_pages(list(pages))
        if not sources:
            return "no_sources"
        if not force and not needs_regeneration(existing, sources):
            return "unchanged"
        name = None if is_placeholder_name(business.name) else business.name
        user_message, names = build_summary_input(name, sources)
        source_ids = [p.id for p in sources]

    # LLM call outside the transaction (an offline job, thinking allowed: DEC-29)
    raw = chat_json(
        [{"role": "system", "content": _SYSTEM_PROMPT}, {"role": "user", "content": user_message}],
        SUMMARY_SCHEMA,
        "business_summary",
    )
    summary = validate_summary(raw, names)

    with tenant_session(business_id) as session:
        row = session.get(BusinessSummary, business_id)
        if row is None:
            row = BusinessSummary(business_id=business_id)
            session.add(row)
        elif row.owner_confirmed_at is not None:  # confirmed while the model was running
            return "owner_confirmed"
        for key, value in summary.items():
            setattr(row, key, value)
        row.source_page_ids = source_ids
        row.generated_at = datetime.now(UTC)
    return "generated"


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate the business summary (plan 07 §6)")
    parser.add_argument("--business-id", type=uuid.UUID, required=True)
    parser.add_argument("--force", action="store_true", help="regenerate even if no source page changed")
    args = parser.parse_args()
    print(f"summary: {summarize_business(args.business_id, force=args.force)}")


if __name__ == "__main__":
    main()
