"""Organize step between clean and chunk/index (plan/07-knowledge-quality.md §4, DEC-40).

Runs after fetch+clean, before chunking: dedupes site chrome into one pseudo-page,
assigns every page a type and a location (or business-wide), filters junk chunks and
cuts testimonials out of the text that gets chunked. Never touches `pages.markdown` —
the raw cleaned text stays untouched for re-runs (DEC-09); everything this module
writes lives in the new metadata columns and in `locations`.

Scope note (this milestone, plan 07 §9 "Stage 1" first block): page typing uses URL
rules + simple content heuristics only, no LLM classification. The plan's "LLM
classification (JSON schema) for the rest" is deferred — the pilot's crawled page set is
fully covered by URL rules (location/journal/legal/contact/.../service slugs are all
known paths), and doing so keeps this step network-free and deterministic for the unit
tests (matching the crawler's own "no network in tests" rule). Add an LLM fallback for
pages the rules below return "other" for, next time a page type can't be resolved this
way.

Locations are detected from the site chrome (the footer lists each location's name and
postal address; the header nav gives each one's own page path) rather than from the
crawl's start URL (DEC-37, A1) -- `detect_locations_from_chrome()` below.
"""

import re
import uuid
from collections import Counter
from dataclasses import dataclass
from datetime import datetime
from urllib.parse import urlparse

from sqlalchemy import select

from app.db import tenant_session
from app.db.models import Business, Location, Page
from app.ingest.chunk import ChunkDraft
from app.ingest.clean import content_hash as sha256_hash
from app.ingest.profile import is_placeholder_name

ORGANIZER_VERSION = "1"  # bump to force re-organizing every page regardless of content_hash

# --- site chrome dedupe (K2) ---------------------------------------------------------


def detect_site_chrome(chrome_texts: list[str], min_pages: int = 3, min_fraction: float = 0.5) -> str:
    """Blocks (split on blank lines) that repeat on >= min_fraction of pages (and at
    least min_pages pages) are site chrome -- kept once, in first-seen order. `[]` or all-
    empty input -> "".
    """
    non_empty = [t for t in chrome_texts if t and t.strip()]
    if not non_empty:
        return ""

    threshold = max(min_pages, round(min_fraction * len(non_empty)))
    # count each block once per page (a block repeated within one page's own chrome --
    # e.g. a nav menu rendered twice for desktop/mobile -- must not inflate its count);
    # dict.fromkeys dedupes while keeping each page's own block order (a plain set
    # wouldn't, and the output below is in first-seen order across pages).
    per_page_blocks = [
        list(dict.fromkeys(b.strip() for b in re.split(r"\n\s*\n", text) if b.strip()))
        for text in non_empty
    ]
    counts = Counter(block for blocks in per_page_blocks for block in blocks)

    seen: set[str] = set()
    kept: list[str] = []
    for blocks in per_page_blocks:
        for block in blocks:
            if block in seen:
                continue
            seen.add(block)
            if counts[block] >= threshold:
                kept.append(block)
    return "\n\n".join(kept)


# --- location detection from site content (DEC-37, A1) -------------------------------


@dataclass(frozen=True)
class LocationCandidate:
    name: str
    address: str
    url_path: str | None  # relative path from the header nav link, e.g. "/flatiron"; None if not found


# "Name\n<street line>\nCity, ST ZIP" -- as markdownify renders the footer's
# "Locations & HOURS" block (a markdown link around the name when the site links it to
# its own page, so the name itself is captured regardless of whether it's inside [...]).
_ADDRESS_BLOCK_RE = re.compile(
    r"(?:\[(?P<name_linked>[A-Za-z][A-Za-z .]{1,40})\]\([^)]*\)"
    r"|(?P<name_plain>^[A-Za-z][A-Za-z .]{1,40})$)"
    r"\s*\n(?P<street>[^\n]{3,80})\s*\n(?P<city>[A-Za-z .]+,\s*[A-Z]{2}\s*\d{5})",
    re.MULTILINE,
)

# "[LocationName](/relative-path)" anywhere in the chrome (header nav links each
# location to its own page) -- used to recover the page path for a location name found
# by _ADDRESS_BLOCK_RE above (the footer's own location links often point at Google
# Maps instead of the site's own page, so the nav is the more reliable source for this).
_NAV_LINK_RE = re.compile(r"\[(?P<name>[A-Za-z][A-Za-z .]{1,40})\]\((?P<path>/[^)\s]*)\)")


def detect_locations_from_chrome(chrome_markdown: str) -> list[LocationCandidate]:
    """Location name + postal address pairs found in the site chrome, with each one's
    own page path if a relative link to it is also found in the chrome. [] if the site
    has no such block (e.g. a single-location business with no footer address list)."""
    addresses: dict[str, str] = {}
    for m in _ADDRESS_BLOCK_RE.finditer(chrome_markdown):
        name = (m.group("name_linked") or m.group("name_plain") or "").strip()
        if not name:
            continue
        address = f"{m.group('street').strip()}, {m.group('city').strip()}"
        addresses.setdefault(name, address)

    nav_paths: dict[str, str] = {}
    for m in _NAV_LINK_RE.finditer(chrome_markdown):
        name = m.group("name").strip()
        if name in addresses and name not in nav_paths:
            nav_paths[name] = m.group("path")

    return [
        LocationCandidate(name=name, address=address, url_path=nav_paths.get(name))
        for name, address in addresses.items()
    ]


def slugify(name: str) -> str:
    """Lowercase, spaces/underscores -> hyphens -- used to match a location name to a
    page URL's last path segment when no nav link gave the path directly."""
    return re.sub(r"[\s_]+", "-", name.strip().lower())


def location_url(candidate: LocationCandidate, site_root: str, crawled_paths: set[str]) -> str | None:
    """Absolute URL for `candidate`: its nav-link path if we have one, else a slug match
    against the business's own crawled page paths. None if neither resolves (the
    location's page wasn't crawled, or the business has no page for it at all)."""
    root = site_root.rstrip("/")
    if candidate.url_path:
        return f"{root}{candidate.url_path}"
    slug = slugify(candidate.name)
    for path in crawled_paths:
        if path.strip("/").lower() == slug:
            return f"{root}/{path.strip('/')}"
    return None


# --- page type (URL rules; plan 07 §4) ------------------------------------------------

_BLOG_PREFIXES = ("journal", "blog")
_LEGAL_SLUGS = {"privacy-policy", "terms-of-service"}
_FAQ_SLUGS = {"faq"}
_POLICY_SLUGS = {"health-and-safety", "know-before-you-go", "code-of-conduct", "what-to-expect"}
_CONTACT_SLUGS = {"contact"}
_CAREERS_SLUGS = {"careers"}
_LANDING_SLUGS = {""}  # site root

PAGE_TYPES = (
    "location", "service", "pricing", "faq", "policy", "contact", "about", "blog",
    "event", "legal", "careers", "landing", "site_chrome", "other",
)


def classify_page_type(url: str, location_slugs: set[str]) -> str:
    """URL rules only this milestone -- see module docstring. `location_slugs` are
    slugified location names (from `detect_locations_from_chrome`), so a page whose path
    matches one is the location's own page, even for locations not yet given a url by
    `location_url` (a site can link a location before we've crawled its page)."""
    path = urlparse(url).path.strip("/").lower()
    if path in _LANDING_SLUGS:
        return "landing"
    if path in location_slugs:
        return "location"
    first_segment = path.split("/", 1)[0]
    if first_segment in _BLOG_PREFIXES:
        return "blog"
    if path in _LEGAL_SLUGS:
        return "legal"
    if path in _CAREERS_SLUGS:
        return "careers"
    if path in _CONTACT_SLUGS:
        return "contact"
    if path in _FAQ_SLUGS:
        return "faq"
    if path in _POLICY_SLUGS:
        return "policy"
    if first_segment == "treatments" or path in {
        "day-pass", "memberships", "packs", "scrub", "massage", "sauna-rituals-aufguss",
        "gift-cards", "rooftop-pool-nyc", "rooftop-pool-bar-menu", "referrals", "first-timers",
    }:
        return "service"
    if "coming-soon" in path:
        return "event"
    return "other"


_NOT_RETRIEVABLE_TYPES = {"blog", "legal", "careers", "landing", "site_chrome"}


def is_retrievable(page_type: str, noindex: bool = False, is_duplicate: bool = False) -> bool:
    """plan 07 §4 "Retrievable?": blog/legal/careers/landing/site_chrome, duplicates and
    noindex pages are summary input at most, never chunked for retrieval."""
    if noindex or is_duplicate:
        return False
    return page_type not in _NOT_RETRIEVABLE_TYPES


# --- blog/event dates (best-effort; feeds published_at) -------------------------------

_MONTHS = (
    "january|february|march|april|may|june|july|august|september|october|november|december"
)
_DATE_RE = re.compile(rf"\b({_MONTHS})\s+\d{{1,2}},?\s+\d{{4}}\b", re.IGNORECASE)


def extract_published_at(markdown: str) -> datetime | None:
    """First "Month DD, YYYY"-shaped date found in the text, else None. Best-effort only
    -- good enough to keep an obviously-dated blog post out of a time-sensitive fact
    (plan 07 §4 "Staleness"); a page with no such date just gets published_at=None."""
    m = _DATE_RE.search(markdown)
    if not m:
        return None
    for fmt in ("%B %d, %Y", "%B %d %Y"):
        try:
            return datetime.strptime(m.group(0).replace(",", ""), fmt.replace(",", ""))
        except ValueError:
            continue
    return None


# --- junk-chunk filter + testimonial cut (K5, K6) -------------------------------------

_MD_LINK_RE = re.compile(r"\[([^\]]*)\]\([^)]*\)")
_JUNK_TEXT_RE = re.compile(r"^(Previous|Next|Read More|Back|Home)$", re.IGNORECASE)


def strip_markdown_links(text: str) -> str:
    """"[Title](/journal/x)" -> "Title" -- plan 07 §4 "strip Markdown links from section_heading"."""
    return _MD_LINK_RE.sub(r"\1", text).strip()


def is_junk_chunk(text: str, min_chars: int = 40) -> bool:
    """Drop chunks that are link-only navigation debris (K5): "Previous"/"Next"/"Read
    More" blocks (optionally still wrapped as a Markdown link) and anything left under
    `min_chars` once its links are unwrapped to plain text."""
    stripped = strip_markdown_links(text)
    if not stripped:
        return True
    if _JUNK_TEXT_RE.match(stripped):
        return True
    return len(stripped) < min_chars


def filter_junk_chunks(drafts: list[ChunkDraft]) -> list[ChunkDraft]:
    """Drop junk drafts (K5) and strip Markdown links from the surviving ones' headings;
    `chunk_index` is renumbered so it stays continuous (run.py/store.py assume that)."""
    kept = [d for d in drafts if not is_junk_chunk(d.text)]
    return [
        ChunkDraft(
            section_heading=strip_markdown_links(d.section_heading) if d.section_heading else d.section_heading,
            text=d.text,
            chunk_index=i,
        )
        for i, d in enumerate(kept)
    ]


# "<Heading line with 'Reviews'>\n\n" followed by one or more "* ## “quote”\n\n  Name." items
# (K6) -- markdownify's rendering of a Squarespace testimonial list. Smart or straight
# quotes around the quote; the name line is whatever follows, not re-validated as a name.
_TESTIMONIAL_SECTION_RE = re.compile(
    r"\n[^\n]*\bReviews\b[^\n]*\n\n"
    r"(?:\*\s*##\s*[“\"][^\n]*\n\n[^\n*][^\n]*\n?)+",
)


def cut_testimonials(markdown: str) -> tuple[str, list[str]]:
    """Cut review/testimonial blocks (K6) out of `markdown` before it's chunked, so
    opinions and customer names don't end up in retrievable chunks. Returns (cleaned
    text, cut blocks) -- the cut text isn't stored anywhere yet (business_summaries /
    summary.py, which plan 07 §6 says should receive it, is a later milestone); callers
    that don't need it can discard the second element."""
    cut: list[str] = []

    def _replace(m: re.Match) -> str:
        cut.append(m.group().strip())
        return "\n"

    cleaned = _TESTIMONIAL_SECTION_RE.sub(_replace, markdown)
    return cleaned, cut


# --- orchestration helper used by run.py ----------------------------------------------


def chunk_prefix(business_name: str, location_name: str) -> str:
    """Text-embedded location tag (A2): "Bathhouse Williamsburg — "."""
    return f"{business_name} {location_name} — "


def prefix_chunk_drafts(drafts: list[ChunkDraft], prefix: str) -> list[ChunkDraft]:
    """Prepend `prefix` to every draft's text (A2: the location must be in the embedded
    text itself, not only in the `location_id` column, so keyword search on the location
    name also works)."""
    return [
        ChunkDraft(section_heading=d.section_heading, text=f"{prefix}{d.text}", chunk_index=d.chunk_index)
        for d in drafts
    ]


CHROME_PSEUDO_PATH = "__site_chrome__"  # appended to the business's site root; never a real crawled URL


def chrome_pseudo_url(site_root: str) -> str:
    return f"{site_root.rstrip('/')}/{CHROME_PSEUDO_PATH}"


@dataclass(frozen=True)
class OrganizeResult:
    organized_page_ids: list[uuid.UUID]  # pages run.py should (re)chunk this run
    locations_found: int
    chrome_chars: int


def organize_business(
    business_id: uuid.UUID, start_url: str, site_root: str, noindex_urls: frozenset[str] = frozenset()
) -> OrganizeResult:
    """DB-touching orchestration, called once per crawl after all pages are fetched and
    stored (A4): dedupe site chrome into one pseudo-page, detect/upsert `locations` from
    that chrome (A1 — never from `start_url`'s path), and assign every page a type,
    location and retrievable flag. Idempotent: a page already organized under the
    current content_hash + ORGANIZER_VERSION is left untouched and not returned, so
    run.py's chunking step (which uses `organized_page_ids`) also does nothing for it —
    "re-running on an unchanged site changes nothing" (plan 07 §4 "Re-run rules" / done-when).
    """
    chrome_url = chrome_pseudo_url(site_root)
    with tenant_session(business_id) as session:
        pages = list(
            session.scalars(select(Page).where(Page.business_id == business_id, Page.url != chrome_url))
        )

        chrome_text = detect_site_chrome([p.chrome_markdown or "" for p in pages])
        if chrome_text:
            _upsert_chrome_page(session, business_id, chrome_url, chrome_text)

        candidates = detect_locations_from_chrome(chrome_text)
        crawled_paths = {urlparse(p.url).path for p in pages}
        name_to_location = _upsert_locations(session, business_id, candidates, crawled_paths, site_root, start_url)

        location_by_slug = {slugify(name): loc for name, loc in name_to_location.items()}
        for loc in session.scalars(select(Location).where(Location.business_id == business_id)):
            if loc.name:
                location_by_slug.setdefault(slugify(loc.name), loc)
        slug_set = set(location_by_slug)

        organized_ids = _organize_pages(pages, location_by_slug, slug_set, noindex_urls)
        session.flush()

    return OrganizeResult(
        organized_page_ids=organized_ids, locations_found=len(name_to_location), chrome_chars=len(chrome_text)
    )


def _upsert_chrome_page(session, business_id: uuid.UUID, chrome_url: str, chrome_text: str) -> None:
    chrome_hash = sha256_hash(chrome_text)
    page = session.scalar(select(Page).where(Page.business_id == business_id, Page.url == chrome_url))
    if page is None:
        session.add(Page(
            business_id=business_id, url=chrome_url, title="Site chrome", markdown=chrome_text,
            content_hash=chrome_hash, page_type="site_chrome", retrievable=False,
            organized_hash=f"{chrome_hash}:{ORGANIZER_VERSION}",
        ))
    elif page.content_hash != chrome_hash:
        page.markdown = chrome_text
        page.content_hash = chrome_hash
        page.page_type = "site_chrome"
        page.retrievable = False
        page.organized_hash = f"{chrome_hash}:{ORGANIZER_VERSION}"
    session.flush()


def _upsert_locations(
    session, business_id: uuid.UUID, candidates: list[LocationCandidate], crawled_paths: set[str],
    site_root: str, start_url: str,
) -> dict[str, Location]:
    existing_rows = list(session.scalars(select(Location).where(Location.business_id == business_id)))
    # Matched primarily by url (stable identity for a pre-existing row created before
    # this organize step existed, e.g. migration 0002's backfill, which named the
    # business's one location after the *business* -- "Bathhouse Williamsburg" rather
    # than the chrome's own "Williamsburg"); name is only a fallback for a location that
    # was never resolved to a url yet. Either way the match is renamed to the chrome's
    # name (A3) rather than left stale or duplicated.
    existing_by_name = {loc.name.strip().lower(): loc for loc in existing_rows if loc.name}
    existing_by_url = {loc.url: loc for loc in existing_rows if loc.url}
    has_default = any(loc.is_default for loc in existing_rows)
    start_slug = slugify(urlparse(start_url).path.strip("/") or "main")

    by_name: dict[str, Location] = {}
    for cand in candidates:
        url = location_url(cand, site_root, crawled_paths)
        key = cand.name.strip().lower()
        loc = (url and existing_by_url.get(url)) or existing_by_name.get(key)
        if loc is None:
            loc = Location(
                business_id=business_id, name=cand.name, url=url, address=cand.address,
                is_default=(not has_default and slugify(cand.name) == start_slug),
            )
            session.add(loc)
            session.flush()
            has_default = has_default or loc.is_default
        else:
            if loc.name != cand.name:
                loc.name = cand.name  # A3: "Bathhouse Williamsburg" -> "Williamsburg"
            if url and loc.url != url:
                loc.url = url
            if cand.address and loc.address != cand.address:
                loc.address = cand.address
        by_name[key] = loc

    # Fallback default (DEC-37/A1/A4: "every business has a default location ... (the
    # one the start URL is about if that page is a location page, else the first found,
    # else one named after the business)"): only reached when the business has no
    # default at all yet.
    if not has_default and by_name:
        fallback = next((loc for loc in by_name.values() if loc.url == start_url), None)
        fallback = fallback or next(iter(by_name.values()))
        fallback.is_default = True
    elif not has_default and not by_name:
        # No location block found anywhere in the site chrome at all (e.g. a genuinely
        # single-location business with no footer address list) -- still must end up
        # with exactly one default location, named after the business itself.
        business = session.get(Business, business_id)
        name = (business.name if business and not is_placeholder_name(business.name) else None) or "Main"
        fallback = Location(business_id=business_id, name=name, url=start_url, is_default=True)
        session.add(fallback)
        session.flush()
        by_name[name.strip().lower()] = fallback

    session.flush()
    return by_name


def _organize_pages(
    pages: list[Page], location_by_slug: dict[str, Location], slug_set: set[str],
    noindex_urls: frozenset[str] = frozenset(),
) -> list[uuid.UUID]:
    by_hash: dict[str, list[Page]] = {}
    for p in pages:
        by_hash.setdefault(p.content_hash, []).append(p)

    organized: list[uuid.UUID] = []
    for p in pages:
        dupes = by_hash[p.content_hash]
        canonical = min(dupes, key=lambda x: len(x.url)) if len(dupes) > 1 else p
        duplicate_of = canonical.id if canonical.id != p.id else None
        noindex = p.url in noindex_urls

        page_hash = f"{p.content_hash}:{ORGANIZER_VERSION}"
        # noindex isn't persisted (no DB column for it -- it's only known at crawl time,
        # from the raw HTML, which isn't stored, DEC-09): a page already organized under
        # this content+version is skipped UNLESS this run observed noindex and the page
        # is still marked retrievable, so that signal still gets applied even though it
        # can't be re-detected on a later `--only organize` run without a fresh fetch.
        if p.organized_hash == page_hash and p.duplicate_of == duplicate_of and not (noindex and p.retrievable):
            continue  # already organized under this content + version -- no-op (idempotency)

        page_type = classify_page_type(p.url, slug_set)
        slug = urlparse(p.url).path.strip("/").lower()
        location = location_by_slug.get(slug) if page_type == "location" else None

        p.page_type = page_type
        p.location_id = location.id if location else None
        p.duplicate_of = duplicate_of
        p.retrievable = is_retrievable(page_type, noindex=noindex, is_duplicate=duplicate_of is not None)
        if page_type == "blog":
            p.published_at = extract_published_at(p.markdown)
        p.organized_hash = page_hash
        organized.append(p.id)
    return organized
