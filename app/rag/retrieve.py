"""Hybrid retrieval: pgvector + Postgres full-text, reciprocal rank fusion
(plan/02-local-rag.md "Answer pipeline" / DEC-12). Callers pass an already-open
`session` — same convention as app/ingest/store.py.
"""

import uuid
from dataclasses import dataclass

from sqlalchemy import func, or_, select

from app.config import get_settings
from app.db.models import Chunk, FactSource
from app.ingest.facts import normalize_text
from app.llm import embed

CUSTOM_REPLY_BOOST = 2.0
FACT_BOOST = 1.5  # custom_reply x2 > fact x1.5 > scraped (plan/07 §5.5)


@dataclass(frozen=True)
class RetrievedChunk:
    chunk_id: uuid.UUID
    text: str
    section_heading: str | None
    kind: str
    score: float
    source: str  # "vector" | "keyword" | "both"


def _location_filter(location_id: uuid.UUID | None) -> list:
    """Business-wide chunks plus the current location's (plan/07 §5.5); no filter when
    the location isn't known (OPEN-23)."""
    if location_id is None:
        return []
    return [or_(Chunk.location_id.is_(None), Chunk.location_id == location_id)]


def vector_search(
    session, business_id: uuid.UUID, query_embedding: list[float], top_k: int = 10,
    location_id: uuid.UUID | None = None,
) -> list[tuple[uuid.UUID, int]]:
    """Chunk ids ordered by cosine distance to `query_embedding`; 1-based rank, best first.

    Only chunks embedded by the currently configured model: vectors from another model
    live in a different space (R6), so mid re-index they'd be ranked as noise.
    """
    stmt = (
        select(Chunk.id)
        .where(
            Chunk.business_id == business_id,
            Chunk.embedding.isnot(None),
            Chunk.embed_model == get_settings().embed_model,
            *_location_filter(location_id),
        )
        .order_by(Chunk.embedding.cosine_distance(query_embedding))
        .limit(top_k)
    )
    rows = session.scalars(stmt).all()
    return [(cid, rank) for rank, cid in enumerate(rows, start=1)]


def keyword_search(
    session, business_id: uuid.UUID, query_text: str, top_k: int = 10, location_id: uuid.UUID | None = None
) -> list[tuple[uuid.UUID, int]]:
    """Chunk ids ordered by ts_rank for `query_text`; [] for an empty/whitespace query.

    Uses an OR of the question's words, not `plainto_tsquery`'s implicit AND: the
    'simple' config keeps stopwords (DEC-12, no stemming), so ANDing every word of a
    natural question ("what... do you have") would require all of them present in one
    chunk -- in practice, close to never. `ts_rank` still weighs multi-word, specific
    matches above a single stopword hit, so switching to OR doesn't make this noisy.
    """
    if not query_text.strip():
        return []
    words = func.tsvector_to_array(func.to_tsvector("simple", query_text))
    ts_query = func.to_tsquery("simple", func.array_to_string(words, " | "))
    stmt = (
        select(Chunk.id)
        .where(Chunk.business_id == business_id, Chunk.tsv.op("@@")(ts_query), *_location_filter(location_id))
        .order_by(func.ts_rank(Chunk.tsv, ts_query).desc())
        .limit(top_k)
    )
    rows = session.scalars(stmt).all()
    return [(cid, rank) for rank, cid in enumerate(rows, start=1)]


def reciprocal_rank_fusion(
    vector_ranks: list[tuple[uuid.UUID, int]], keyword_ranks: list[tuple[uuid.UUID, int]], k: int = 60
) -> dict[uuid.UUID, float]:
    """score(doc) = sum of 1/(k + rank) over every ranking it appears in."""
    scores: dict[uuid.UUID, float] = {}
    for cid, rank in vector_ranks:
        scores[cid] = scores.get(cid, 0.0) + 1.0 / (k + rank)
    for cid, rank in keyword_ranks:
        scores[cid] = scores.get(cid, 0.0) + 1.0 / (k + rank)
    return scores


def retrieve(
    session, business_id: uuid.UUID, question: str, top_n: int = 5, location_id: uuid.UUID | None = None
) -> list[RetrievedChunk]:
    """Embed -> vector + keyword search -> fuse -> boost custom replies (x2) and facts
    (x1.5) -> drop page chunks that repeat a returned fact (DEC-47) -> top_n chunks.
    With `location_id`, only business-wide chunks and that location's are searched."""
    query_embedding = embed([f"search_query: {question}"])[0]

    vector_ranks = vector_search(session, business_id, query_embedding, top_k=10, location_id=location_id)
    keyword_ranks = keyword_search(session, business_id, question, top_k=10, location_id=location_id)
    fused = reciprocal_rank_fusion(vector_ranks, keyword_ranks)
    if not fused:
        return []

    vector_ids = {cid for cid, _ in vector_ranks}
    keyword_ids = {cid for cid, _ in keyword_ranks}

    # kind boosts -- only among chunks already retrieved, never pulls in new ones
    found = {
        c.id: c
        for c in session.scalars(select(Chunk).where(Chunk.id.in_(fused.keys()), Chunk.business_id == business_id))
    }
    for cid, chunk in found.items():
        if chunk.kind == "custom_reply":
            fused[cid] *= CUSTOM_REPLY_BOOST
        elif chunk.kind == "fact":
            fused[cid] *= FACT_BOOST

    ranked = sorted(fused, key=fused.get, reverse=True)
    top_ids = _drop_fact_duplicates(session, business_id, ranked, found, top_n)

    results = []
    for cid in top_ids:
        chunk = found[cid]
        if cid in vector_ids and cid in keyword_ids:
            source = "both"
        elif cid in vector_ids:
            source = "vector"
        else:
            source = "keyword"
        results.append(
            RetrievedChunk(
                chunk_id=cid,
                text=chunk.text,
                section_heading=chunk.section_heading,
                kind=chunk.kind,
                score=fused[cid],
                source=source,
            )
        )
    return results


def _drop_fact_duplicates(
    session, business_id: uuid.UUID, ranked: list[uuid.UUID], found: dict[uuid.UUID, Chunk], top_n: int
) -> list[uuid.UUID]:
    """DEC-47: a page chunk that contains the quote of a fact in the top_n adds nothing,
    so skip it and let the next candidate take its place. Page chunks that say anything
    the facts don't stay as fallback."""
    fact_ids = [found[cid].fact_id for cid in ranked[:top_n] if found[cid].kind == "fact"]
    quotes = []
    if fact_ids:
        rows = session.scalars(
            select(FactSource.quote).where(FactSource.business_id == business_id, FactSource.fact_id.in_(fact_ids))
        )
        quotes = [q for q in (normalize_text(r) for r in rows) if q]

    picked = []
    for cid in ranked:
        if found[cid].kind == "scraped" and quotes:
            text = normalize_text(found[cid].text)
            if any(q in text for q in quotes):
                continue
        picked.append(cid)
        if len(picked) == top_n:
            break
    return picked
