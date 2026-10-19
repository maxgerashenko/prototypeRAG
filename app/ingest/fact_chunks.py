"""Fact chunks: one `chunks` row (`kind='fact'`) per active fact, `text = statement`
(plan/07-knowledge-quality.md §5.2, DEC-38), so facts ride the same hybrid search, the
same indexer (`app.rag.index`) and the same `embed_model` re-index logic (R6) as page
chunks and custom replies. The `facts` row stays the truth; this table is rebuilt from it.

Run at the end of the facts step (`run.extract_facts`). Idempotent: a fact whose statement
is unchanged keeps its chunk and its embedding.
"""

import uuid
from dataclasses import dataclass

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.db.models import Chunk, Fact
from app.ingest.clean import content_hash as sha256_hash


@dataclass
class FactChunkStats:
    created: int = 0
    updated: int = 0
    deleted: int = 0


def sync_fact_chunks(session: Session, business_id: uuid.UUID) -> FactChunkStats:
    """Make the business's fact chunks match its active facts: a chunk per active fact,
    none for stale/conflict/rejected ones (a wrong or old value must never be retrieved).
    A changed statement drops the chunk's embedding so the indexer re-embeds it."""
    stats = FactChunkStats()
    active = {f.id: f for f in session.scalars(select(Fact).where(Fact.business_id == business_id, Fact.status == "active"))}
    existing = {
        c.fact_id: c
        for c in session.scalars(select(Chunk).where(Chunk.business_id == business_id, Chunk.kind == "fact"))
    }

    gone = [fact_id for fact_id in existing if fact_id not in active]
    if gone:
        session.execute(delete(Chunk).where(Chunk.business_id == business_id, Chunk.fact_id.in_(gone)))
        stats.deleted = len(gone)

    for fact_id, fact in active.items():
        digest = sha256_hash(fact.statement)
        chunk = existing.get(fact_id)
        if chunk is None:
            session.add(Chunk(
                business_id=business_id, fact_id=fact_id, kind="fact", text=fact.statement,
                content_hash=digest, location_id=fact.location_id,
            ))
            stats.created += 1
        elif chunk.content_hash != digest or chunk.location_id != fact.location_id:
            chunk.text = fact.statement
            chunk.content_hash = digest
            chunk.location_id = fact.location_id
            chunk.embedding = None
            chunk.embed_model = None
            stats.updated += 1
    session.flush()
    return stats
