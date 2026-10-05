"""Write pages and chunks to Postgres (plan/01-crawler.md step 6 "Save").

Every function here takes an already-open `session` — the caller (`run.py`) opens one
`tenant_session(business_id)` transaction per page, so one bad page can't roll back the rest.
"""

import uuid
from datetime import UTC, datetime

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.db.models import Chunk, Page
from app.ingest.chunk import ChunkDraft
from app.ingest.clean import content_hash as sha256_hash


def upsert_page(
    session: Session,
    business_id: uuid.UUID,
    url: str,
    title: str | None,
    markdown: str,
    content_hash: str,
    chrome_markdown: str | None = None,
    language: str | None = None,
) -> tuple[Page, bool]:
    """Insert or update the page by (business_id, url). Returns (page, changed).

    `changed` only reflects `markdown`/`content_hash` (step 7's re-chunk trigger) --
    `chrome_markdown`/`language` are always written to the latest fetch regardless, since
    they're organize-step inputs, not the content identity a re-crawl guards against
    re-processing (plan/07-knowledge-quality.md §4, K2).
    """
    existing = session.scalars(select(Page).where(Page.business_id == business_id, Page.url == url)).first()

    if existing is None:
        page = Page(
            business_id=business_id, url=url, title=title, markdown=markdown, content_hash=content_hash,
            chrome_markdown=chrome_markdown, language=language,
        )
        session.add(page)
        session.flush()
        return page, True

    existing.chrome_markdown = chrome_markdown
    existing.language = language
    if existing.content_hash == content_hash:
        session.flush()
        return existing, False

    existing.title = title
    existing.markdown = markdown
    existing.content_hash = content_hash
    existing.scraped_at = datetime.now(UTC)
    session.flush()
    return existing, True


def replace_chunks(
    session: Session,
    business_id: uuid.UUID,
    page_id: uuid.UUID,
    drafts: list[ChunkDraft],
    location_id: uuid.UUID | None = None,
) -> None:
    """Full replace of this page's scraped chunks — simplest correct approach for step 2.
    `location_id` (A2, plan/07-knowledge-quality.md §4) tags every chunk from this page;
    the location name itself is expected to already be in `draft.text` (the caller
    prefixes it), not added here — storage stays dumb about content."""
    session.execute(
        delete(Chunk).where(Chunk.business_id == business_id, Chunk.page_id == page_id, Chunk.kind == "scraped")
    )
    for draft in drafts:
        session.add(
            Chunk(
                business_id=business_id,
                page_id=page_id,
                kind="scraped",
                chunk_index=draft.chunk_index,
                section_heading=draft.section_heading,
                text=draft.text,
                embedding=None,
                embed_model=None,
                content_hash=sha256_hash(draft.text),
                location_id=location_id,
            )
        )
    session.flush()


def delete_pages_not_in(session: Session, business_id: uuid.UUID, keep_urls: list[str]) -> int:
    """Delete pages that disappeared from the site (step 7 "Refresh"); cascades to their chunks."""
    result = session.execute(
        delete(Page).where(Page.business_id == business_id, Page.url.not_in(keep_urls))
    )
    return result.rowcount
