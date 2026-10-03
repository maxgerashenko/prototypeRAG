"""Hybrid retrieval: pgvector + Postgres full-text, reciprocal rank fusion
(plan/02-local-rag.md "Answer pipeline" / DEC-12). Callers pass an already-open
`session` — same convention as app/ingest/store.py.
"""

import uuid
from dataclasses import dataclass

from sqlalchemy import func, select

from app.config import get_settings
from app.db.models import Chunk
from app.llm import embed


@dataclass(frozen=True)
class RetrievedChunk:
    chunk_id: uuid.UUID
    text: str
    section_heading: str | None
    kind: str
    score: float
    source: str  # "vector" | "keyword" | "both"


def vector_search(session, business_id: uuid.UUID, query_embedding: list[float], top_k: int = 10) -> list[tuple[uuid.UUID, int]]:
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
        )
        .order_by(Chunk.embedding.cosine_distance(query_embedding))
        .limit(top_k)
    )
    rows = session.scalars(stmt).all()
    return [(cid, rank) for rank, cid in enumerate(rows, start=1)]


def keyword_search(session, business_id: uuid.UUID, query_text: str, top_k: int = 10) -> list[tuple[uuid.UUID, int]]:
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
        .where(Chunk.business_id == business_id, Chunk.tsv.op("@@")(ts_query))
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


def retrieve(session, business_id: uuid.UUID, question: str, top_n: int = 5) -> list[RetrievedChunk]:
    """Embed → vector + keyword search → fuse → boost custom replies → top_n chunks."""
    query_embedding = embed([f"search_query: {question}"])[0]

    vector_ranks = vector_search(session, business_id, query_embedding, top_k=10)
    keyword_ranks = keyword_search(session, business_id, question, top_k=10)
    fused = reciprocal_rank_fusion(vector_ranks, keyword_ranks)
    if not fused:
        return []

    vector_ids = {cid for cid, _ in vector_ranks}
    keyword_ids = {cid for cid, _ in keyword_ranks}

    # custom-reply boost -- only among chunks already retrieved, never pulls in new ones
    kinds = dict(
        session.execute(
            select(Chunk.id, Chunk.kind).where(Chunk.id.in_(fused.keys()), Chunk.business_id == business_id)
        ).all()
    )
    for cid, kind in kinds.items():
        if kind == "custom_reply":
            fused[cid] *= 2.0

    top_ids = sorted(fused, key=fused.get, reverse=True)[:top_n]
    chunks = {
        c.id: c
        for c in session.scalars(select(Chunk).where(Chunk.id.in_(top_ids), Chunk.business_id == business_id))
    }

    results = []
    for cid in top_ids:
        chunk = chunks[cid]
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
