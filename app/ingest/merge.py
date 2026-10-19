"""Store extracted facts and decide which one wins (plan/07-knowledge-quality.md §5.4, DEC-38).

`apply_page_facts` replaces one page's sources with what its latest extraction found;
`settle_facts` then recomputes, for every `fact_key` of the business, which fact is
`active` -- so the outcome depends only on the facts and their sources, never on the order
pages were processed in, and running it twice changes nothing:

- an owner-confirmed fact always wins (DEC-11); crawl values that differ become `conflict`;
- otherwise the best source wins: page-type priority (`PAGE_PRIORITY`), tie -> newest page;
- a fact with several sources keeps all of them; losers with a source are `conflict`
  (kept, listed by `--report conflicts`), facts left without any source are `stale`
  (excluded from retrieval) -- except owner-confirmed ones, which stay `active`.
"""

import re
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.db.models import Fact, FactSource, Location, Page
from app.ingest.facts import ExtractedFact, extraction_stamp

# lower = wins (plan §5.4): location · contact · pricing · service · site_chrome >
# faq · policy > about > landing > blog. `event`/`other` sit with landing: no plan rank.
PAGE_PRIORITY = {
    "location": 0, "contact": 0, "pricing": 0, "service": 0, "site_chrome": 0,
    "faq": 1, "policy": 1, "about": 2, "landing": 3, "event": 3, "other": 3, "blog": 4,
}
_UNRANKED = 3

_VALUE_NOISE_RE = re.compile(r"[\s$,.;:!]+")


def normalize_value(value: str | None) -> str:
    """So '$39', 'from $39.' and 'From  $39' compare equal."""
    return _VALUE_NOISE_RE.sub(" ", (value or "").lower()).strip()


def _norm_key_part(text: str | None) -> str:
    return re.sub(r"\s+", " ", (text or "").lower()).strip()


def make_fact_key(type_: str, location_id: uuid.UUID | None, subject: str | None, attribute: str | None) -> str:
    return "|".join((type_, str(location_id) if location_id else "-", _norm_key_part(subject), _norm_key_part(attribute)))


@dataclass
class MergeStats:
    pages: int = 0
    facts_seen: int = 0
    created: int = 0
    changed_status: int = 0
    by_status: dict[str, int] = field(default_factory=dict)


def apply_page_facts(
    session: Session, business_id: uuid.UUID, page: Page, extracted: list[ExtractedFact], stats: MergeStats | None = None,
) -> None:
    """Make `extracted` the only facts this page backs: its old `fact_sources` rows go,
    each new fact joins the existing fact with the same key and value (one more source)
    or becomes a new row, and the page is stamped as extracted. Statuses are decided by
    `settle_facts`, called once after all pages."""
    now = datetime.now(UTC)
    session.execute(delete(FactSource).where(FactSource.business_id == business_id, FactSource.page_id == page.id))
    session.flush()

    keys = {make_fact_key(f.type, f.location_id, f.subject, f.attribute) for f in extracted}
    existing: dict[str, list[Fact]] = {}
    if keys:
        for fact in session.scalars(select(Fact).where(Fact.business_id == business_id, Fact.fact_key.in_(keys))):
            existing.setdefault(fact.fact_key, []).append(fact)

    for f in extracted:
        key = make_fact_key(f.type, f.location_id, f.subject, f.attribute)
        match = next((r for r in existing.get(key, []) if normalize_value(r.value) == normalize_value(f.value)), None)
        if match is None:
            # 'conflict' until settle_facts picks the winner of this key (only one 'active'
            # row per key is allowed by the partial unique index)
            match = Fact(
                business_id=business_id, location_id=f.location_id, type=f.type, subject=f.subject,
                attribute=f.attribute, value=f.value, value_json=f.value_json, statement=f.statement,
                fact_key=key, status="conflict", origin="crawl", confidence=f.confidence,
            )
            session.add(match)
            session.flush()
            existing.setdefault(key, []).append(match)
            if stats:
                stats.created += 1
        elif match.owner_confirmed_at is None and match.origin == "crawl":
            # same value, possibly new wording; the owner's own text is never touched
            match.statement = f.statement
            match.value_json = f.value_json
            match.confidence = max(match.confidence or 0.0, f.confidence)
        match.last_seen_at = now
        session.add(FactSource(
            business_id=business_id, fact_id=match.id, page_id=page.id, quote=f.quote, page_hash=page.content_hash,
        ))
        if stats:
            stats.facts_seen += 1

    page.extracted_hash = extraction_stamp(page)
    page.extractor_version = page.extracted_hash.rsplit(":", 1)[1]
    session.flush()
    if stats:
        stats.pages += 1


def _source_rank(page: Page) -> tuple[int, float]:
    """Sort key of one source, best first: page-type priority, then newest page."""
    when = page.published_at or page.scraped_at
    return PAGE_PRIORITY.get(page.page_type or "", _UNRANKED), -(when.timestamp() if when else 0.0)


def settle_facts(session: Session, business_id: uuid.UUID, stats: MergeStats | None = None) -> None:
    """Decide the status of every non-rejected fact of the business (module docstring)."""
    facts = list(session.scalars(select(Fact).where(Fact.business_id == business_id, Fact.status != "rejected")))
    if not facts:
        return
    pages_by_fact: dict[uuid.UUID, list[Page]] = {}
    rows = session.execute(
        select(FactSource.fact_id, Page)
        .join(Page, (Page.business_id == FactSource.business_id) & (Page.id == FactSource.page_id))
        .where(FactSource.business_id == business_id)
    )
    for fact_id, page in rows:
        pages_by_fact.setdefault(fact_id, []).append(page)

    by_key: dict[str, list[Fact]] = {}
    for fact in facts:
        by_key.setdefault(fact.fact_key, []).append(fact)

    new_status: dict[uuid.UUID, str] = {}
    for group in by_key.values():
        owner = [f for f in group if f.owner_confirmed_at is not None or f.origin == "owner"]
        if owner:
            winner = max(owner, key=lambda f: f.owner_confirmed_at or datetime.min.replace(tzinfo=UTC))
        else:
            sourced = [f for f in group if pages_by_fact.get(f.id)]
            winner = min(
                sourced, key=lambda f: min(_source_rank(p) for p in pages_by_fact[f.id]), default=None
            )
        for f in group:
            if f is winner:
                new_status[f.id] = "active"
            elif f.owner_confirmed_at is not None or f.origin == "owner":
                new_status[f.id] = f.status  # a second owner fact for the key: the owner sorts it out
            else:
                new_status[f.id] = "conflict" if pages_by_fact.get(f.id) else "stale"

    # demote first, then promote: only one 'active' row per key may exist at any moment
    for promote in (False, True):
        for f in facts:
            target = new_status[f.id]
            if (target == "active") is promote and f.status != target:
                f.status = target
                if stats:
                    stats.changed_status += 1
        session.flush()
    if stats:
        for f in facts:
            stats.by_status[f.status] = stats.by_status.get(f.status, 0) + 1


def conflicts_report(session: Session, business_id: uuid.UUID) -> list[str]:
    """One block of lines per fact key that has a `conflict` row: the active value and
    each losing value with the pages that state it (`--report conflicts`)."""
    facts = list(session.scalars(
        select(Fact).where(Fact.business_id == business_id, Fact.status.in_(("active", "conflict")))
        .order_by(Fact.fact_key, Fact.status)
    ))
    urls: dict[uuid.UUID, list[str]] = {}
    for fact_id, url in session.execute(
        select(FactSource.fact_id, Page.url)
        .join(Page, (Page.business_id == FactSource.business_id) & (Page.id == FactSource.page_id))
        .where(FactSource.business_id == business_id)
    ):
        urls.setdefault(fact_id, []).append(url)
    location_names = {
        loc.id: loc.name for loc in session.scalars(select(Location).where(Location.business_id == business_id))
    }

    by_key: dict[str, list[Fact]] = {}
    for f in facts:
        by_key.setdefault(f.fact_key, []).append(f)
    lines = []
    for group in by_key.values():
        if not any(f.status == "conflict" for f in group):
            continue
        head = group[0]
        where = location_names.get(head.location_id) or "all locations"
        lines.append(f"{head.type} · {head.subject} · {head.attribute} · {where}")
        for f in sorted(group, key=lambda f: f.status != "active"):
            owner = " (owner-confirmed)" if f.owner_confirmed_at else ""
            lines.append(f"  {f.status:<8} {f.value!r}{owner}  <- {', '.join(sorted(urls.get(f.id, []))) or 'no source'}")
    return lines
