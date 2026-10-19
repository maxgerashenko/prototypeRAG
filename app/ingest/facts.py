"""Fact extraction, one page at a time (plan/07-knowledge-quality.md §5.3, DEC-38).

`extract_page_facts` asks the LLM for atomic, sourced facts from one page's text (the
site-chrome pseudo-page included -- that is where the opening hours live, V23) and then
checks every one in code, because local models are weaker (R1, correctness first):
the verbatim `quote` must be in the page, numbers in `value` must be in the quote, the
type must be in the enum, placeholder values are dropped, and time-sensitive types from
blog posts are rejected. Nothing here touches the database except `pages_to_extract`;
`merge.py` stores what comes out.

Which model: `FACTS_LLM_MODEL` (falls back to `LLM_MODEL`) -- OPEN-20.
"""

import re
from dataclasses import dataclass, field
from urllib.parse import urlparse

from app.config import get_settings
from app.db.models import Page
from app.ingest.profile import _is_placeholder
from app.llm import chat_json

EXTRACTOR_VERSION = "1"  # bump to re-extract every page regardless of content_hash

FACT_TYPES = (
    "address", "hours", "contact", "price", "service", "amenity", "policy", "booking",
    "capacity", "accessibility", "event", "about", "location_status",
)
# Facts that go stale (a blog post's prices and hours are from the day it was written)
TIME_SENSITIVE_TYPES = {"hours", "price", "booking", "event", "location_status"}

# Page types that never feed the facts library: legal and careers text isn't about what
# the business offers; the chrome pseudo-page is the opposite and is always read.
SKIP_PAGE_TYPES = {"legal", "careers"}

FACT_SCHEMA = {
    "type": "object",
    "properties": {
        "facts": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "type": {"type": "string", "enum": list(FACT_TYPES)},
                    "subject": {"type": "string"},
                    "attribute": {"type": "string"},
                    "value": {"type": "string"},
                    "location": {"type": "string"},
                    "statement": {"type": "string"},
                    "quote": {"type": "string"},
                    "confidence": {"type": "number"},
                },
                "required": [
                    "type", "subject", "attribute", "value", "location", "statement", "quote", "confidence",
                ],
                "additionalProperties": False,
            },
        }
    },
    "required": ["facts"],
    "additionalProperties": False,
}

_SYSTEM_PROMPT = (
    "Extract facts about a business from ONE page of its website. A fact is a short, "
    "self-contained statement that answers where, when, how, how much or how many.\n"
    f"- type: one of {', '.join(FACT_TYPES)}.\n"
    "- subject: the thing the fact is about ('Day Pass', 'Banya', 'Williamsburg'). "
    "attribute: what is stated about it ('price', 'duration', 'temperature', 'hours', 'phone').\n"
    "- value: the value exactly as written, short ('from $39', '8am to 11:30pm daily').\n"
    "- location: 'this' for the location this page is about, 'all' when it holds for every "
    "location, or the exact name of one of the listed locations.\n"
    "- statement: one sentence that stands alone and names the business (and the location "
    "when the fact is specific to one).\n"
    "- quote: the words from the page that state it, copied EXACTLY, character for character.\n"
    "- confidence: 0 to 1.\n"
    "Only facts the page states. Never guess, never convert or compute numbers. No opinions, "
    "testimonials, marketing adjectives, history or advice. Return an empty list if there are none."
)

SECTION_CHARS = 12000  # ~3k tokens; longer pages are split at headings (plan §5.3)

_WS_RE = re.compile(r"\s+")
_NUMBER_RE = re.compile(r"\d[\d,]*(?:\.\d+)?")
# Markdown that sits between words on the page but isn't part of what a model quotes
_MD_NOISE_RE = re.compile(r"[*_`#>\[\]]|\\")


@dataclass
class ExtractedFact:
    type: str
    subject: str
    attribute: str
    value: str
    statement: str
    quote: str
    confidence: float
    location_id: object = None  # uuid.UUID | None -- None = business-wide
    value_json: dict | None = None
    page_id: object = None
    page_type: str | None = None


@dataclass
class PageResult:
    facts: list[ExtractedFact] = field(default_factory=list)
    rejected: list[tuple[dict, str]] = field(default_factory=list)  # (raw fact, reason)


def extraction_stamp(page: Page) -> str:
    return f"{page.content_hash}:{EXTRACTOR_VERSION}"


def pages_to_extract(pages: list[Page], force: bool = False) -> list[Page]:
    """Pages whose text or extractor version changed since their facts were last
    extracted (`extracted_hash`); the site-chrome pseudo-page is included, legal and
    careers pages and duplicates are not. Unchanged site -> empty list (done-when)."""
    picked = [
        p for p in pages
        if p.duplicate_of is None
        and p.page_type is not None
        and p.page_type not in SKIP_PAGE_TYPES
        and (force or p.extracted_hash != extraction_stamp(p))
    ]
    return sorted(picked, key=lambda p: p.url)


def normalize_text(text: str) -> str:
    """For quote matching: markdown noise gone, whitespace collapsed, case folded,
    typographic quotes straightened."""
    text = text.translate({0x2018: "'", 0x2019: "'", 0x201C: '"', 0x201D: '"', 0x2013: "-", 0x2014: "-", 0xA0: " "})
    return _WS_RE.sub(" ", _MD_NOISE_RE.sub("", text)).strip().lower()


def _numbers(text: str) -> set[str]:
    """Numbers as digit strings ("$1,200" -> "1200", "11:30" -> {"11", "30"})."""
    return {n.replace(",", "").rstrip(".") for n in _NUMBER_RE.findall(text)}


def split_sections(markdown: str, limit: int = SECTION_CHARS) -> list[str]:
    """Cut at headings into pieces of at most `limit` characters (a single section longer
    than that is cut hard)."""
    if len(markdown) <= limit:
        return [markdown]
    parts = re.split(r"(?m)^(?=#{1,3} )", markdown)
    sections, current = [], ""
    for part in parts:
        while len(part) > limit:
            if current:
                sections.append(current)
                current = ""
            sections.append(part[:limit])
            part = part[limit:]
        if len(current) + len(part) > limit and current:
            sections.append(current)
            current = ""
        current += part
    if current.strip():
        sections.append(current)
    return sections


def validate_fact(
    raw: dict, page_text_norm: str, page_type: str | None, locations: dict[str, object], page_location_id
) -> tuple[ExtractedFact | None, str | None]:
    """One raw model fact -> (fact, None) or (None, reason it was rejected). `locations`
    maps lower-cased location names to ids."""
    ftype = raw.get("type")
    if ftype not in FACT_TYPES:
        return None, "unknown type"
    fields = {k: raw.get(k) for k in ("subject", "attribute", "value", "statement", "quote")}
    for key, val in fields.items():
        if not isinstance(val, str) or _is_placeholder(key, val):
            return None, f"empty or placeholder {key}"
        fields[key] = val.strip()
    quote_norm = normalize_text(fields["quote"])
    if not quote_norm or quote_norm not in page_text_norm:
        return None, "quote not found in page"
    if _numbers(fields["value"]) - _numbers(fields["quote"]):
        return None, "number in value not in quote"
    if page_type == "blog" and ftype in TIME_SENSITIVE_TYPES:
        return None, "time-sensitive fact from a blog post"

    where = raw.get("location")
    where = where.strip().lower() if isinstance(where, str) else ""
    if where == "all":
        location_id = None
    elif where in ("this", ""):
        location_id = page_location_id
    elif where in locations:
        location_id = locations[where]
    else:
        return None, f"unknown location {raw.get('location')!r}"

    try:
        confidence = min(1.0, max(0.0, float(raw.get("confidence"))))
    except (TypeError, ValueError):
        confidence = 0.5
    fact = ExtractedFact(
        type=ftype, subject=fields["subject"], attribute=fields["attribute"], value=fields["value"],
        statement=fields["statement"], quote=fields["quote"], confidence=confidence,
        location_id=location_id, page_type=page_type,
    )
    if ftype == "hours":
        fact.value_json = parse_hours(fact.value)
    return fact, None


# --- opening hours -> structured (V23) ------------------------------------------------

_DAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
_TIME_RE = r"(\d{1,2})(?::(\d{2}))?\s*(am|pm|a\.m\.|p\.m\.)?"
_RANGE_RE = re.compile(rf"{_TIME_RE}\s*(?:to|-|–|—|until|till)\s*{_TIME_RE}", re.IGNORECASE)
_EVERY_DAY_RE = re.compile(r"\b(7 days|seven days|every day|daily|all week)\b", re.IGNORECASE)


def _hhmm(hour: str, minute: str | None, meridiem: str | None, fallback_meridiem: str | None) -> str | None:
    h, m = int(hour), int(minute or 0)
    mer = (meridiem or fallback_meridiem or "").lower().replace(".", "")
    if mer == "pm" and h < 12:
        h += 12
    elif mer == "am" and h == 12:
        h = 0
    if h > 24 or m > 59:
        return None
    return f"{h:02d}:{m:02d}"


def parse_hours(value: str) -> dict | None:
    """'7 days a week 8am to 11:30pm' / 'daily 8:00-23:30' -> same hours for each day.
    Anything per-day or otherwise irregular returns None: the text `value` is then all we
    keep, never a guessed structure."""
    if not _EVERY_DAY_RE.search(value):
        return None
    ranges = _RANGE_RE.findall(value)
    if len(ranges) != 1:
        return None
    h1, m1, mer1, h2, m2, mer2 = ranges[0]
    open_ = _hhmm(h1, m1, mer1, mer2)
    close = _hhmm(h2, m2, mer2, mer1)
    if open_ is None or close is None:
        return None
    return {day: [[open_, close]] for day in _DAYS}


# --- extraction -----------------------------------------------------------------------


def _user_message(page: Page, business_name: str | None, location_names: list[str], page_location: str | None,
                  section: str) -> str:
    lines = []
    if business_name:
        lines.append(f"Business: {business_name}")
    if location_names:
        lines.append("Locations: " + ", ".join(location_names))
    if page.page_type == "site_chrome":
        lines.append("This text is the header/footer shown on every page of the site.")
    else:
        path = urlparse(page.url).path or "/"
        lines.append(f"Page: {path} (type: {page.page_type})")
        if page_location:
            lines.append(f"This page is about the location: {page_location}")
    return "\n".join(lines) + "\n\n" + section


def extract_page_facts(
    page: Page, business_name: str | None, locations: dict[str, object], page_location_name: str | None = None,
    model: str | None = None,
) -> PageResult:
    """Run the model over the page (in sections if long) and keep the facts that pass
    `validate_fact`. `locations` maps location name -> location id."""
    model = model or get_settings().facts_llm_model or None
    location_names = sorted(locations)
    by_lower = {name.strip().lower(): loc_id for name, loc_id in locations.items()}
    page_norm = normalize_text(page.markdown)
    result = PageResult()
    seen: set[tuple] = set()
    for section in split_sections(page.markdown):
        raw = chat_json(
            [
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user", "content": _user_message(page, business_name, location_names, page_location_name, section)},
            ],
            FACT_SCHEMA,
            "page_facts",
            model=model,
        )
        for item in raw.get("facts") or []:
            fact, reason = validate_fact(item, page_norm, page.page_type, by_lower, page.location_id)
            if fact is None:
                result.rejected.append((item, reason))
                continue
            fact.page_id = page.id
            identity = (fact.type, fact.location_id, fact.subject.lower(), fact.attribute.lower(), fact.value.lower())
            if identity not in seen:  # the same fact stated twice on one page
                seen.add(identity)
                result.facts.append(fact)
    return result
