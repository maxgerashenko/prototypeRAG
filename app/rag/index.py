"""Indexer: embed chunks missing an embedding or with a stale embed_model
(plan/02-local-rag.md). nomic-embed-text-v1.5 needs a task prefix: "search_document: "
here for chunks being indexed, "search_query: " in retrieve.py for the question.
"""

import argparse
import uuid

from sqlalchemy import or_, select

from app.config import get_settings
from app.db import tenant_session
from app.db.models import Chunk
from app.llm import embed


def chunks_needing_embedding(session, business_id: uuid.UUID) -> list[Chunk]:
    """Chunks missing an embedding, or embedded with a model that's no longer configured."""
    embed_model = get_settings().embed_model
    stmt = (
        select(Chunk)
        .where(
            Chunk.business_id == business_id,
            or_(Chunk.embedding.is_(None), Chunk.embed_model != embed_model),
        )
        .order_by(Chunk.id)
    )
    return list(session.scalars(stmt))


def index_business(business_id: uuid.UUID, batch_size: int = 64) -> dict:
    """Embed this business's un-indexed chunks, one committed transaction per batch
    so a later batch's failure can never roll back earlier batches' progress."""
    with tenant_session(business_id) as session:
        chunk_ids = [c.id for c in chunks_needing_embedding(session, business_id)]

    embed_model = get_settings().embed_model
    chunks_indexed = 0
    batches_failed = 0

    for i in range(0, len(chunk_ids), batch_size):
        batch_ids = chunk_ids[i : i + batch_size]
        try:
            with tenant_session(business_id) as session:
                batch = list(session.scalars(select(Chunk).where(Chunk.id.in_(batch_ids))))
                vectors = embed([f"search_document: {c.text}" for c in batch])
                for chunk, vector in zip(batch, vectors, strict=True):
                    chunk.embedding = vector
                    chunk.embed_model = embed_model
            chunks_indexed += len(batch_ids)
        except Exception as exc:
            print(f"batch failed, skipping: {exc}")
            batches_failed += 1

    return {"chunks_indexed": chunks_indexed, "batches_failed": batches_failed}


def main() -> None:
    parser = argparse.ArgumentParser(description="Embed un-indexed chunks for a business")
    parser.add_argument("--business-id", required=True, type=uuid.UUID)
    parser.add_argument("--batch-size", type=int, default=64)
    args = parser.parse_args()

    summary = index_business(args.business_id, args.batch_size)
    for key, value in summary.items():
        print(f"{key}: {value}")


if __name__ == "__main__":
    main()
