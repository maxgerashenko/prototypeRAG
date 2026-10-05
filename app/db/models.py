"""Base tables (plan/02-local-rag.md → Data model). Every tenant table has `business_id`.

Child tables reference their parent by `(business_id, id)`, so a row can never point at
another business's row — the database rejects cross-tenant links even before RLS.
Action tables (bookings, ...) are added in step 5 (plan/04-actions.md).
"""

import uuid
from datetime import datetime

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    case,
    CheckConstraint,
    MetaData,
    Computed,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, TSVECTOR, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

# Fixed column size in every stage: nomic-embed-text-v1.5 locally, Gemini with reduced
# output dimension in cloud (EMBED_DIM=768). Changing it needs a migration.
EMBED_DIM = 768


class Base(DeclarativeBase):
    # Deterministic constraint names, so migrations can refer to them.
    metadata = MetaData(
        naming_convention={
            "ix": "ix_%(table_name)s_%(column_0_N_name)s",
            "uq": "uq_%(table_name)s_%(column_0_N_name)s",
            "ck": "ck_%(table_name)s_%(constraint_name)s",
            "fk": "fk_%(table_name)s_%(column_0_N_name)s_%(referred_table_name)s",
            "pk": "pk_%(table_name)s",
        }
    )


def _id() -> Mapped[uuid.UUID]:
    return mapped_column(UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()"))


def _business_id() -> Mapped[uuid.UUID]:
    return mapped_column(
        UUID(as_uuid=True), ForeignKey("businesses.id", ondelete="CASCADE"), nullable=False, index=True
    )


def _now() -> Mapped[datetime]:
    return mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())


class Business(Base):
    __tablename__ = "businesses"

    id: Mapped[uuid.UUID] = _id()
    name: Mapped[str] = mapped_column(Text, nullable=False)
    website: Mapped[str | None] = mapped_column(Text)
    timezone: Mapped[str] = mapped_column(Text, nullable=False, server_default="UTC")
    phone_numbers: Mapped[list[str]] = mapped_column(ARRAY(Text), nullable=False, server_default="{}")
    settings: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default="{}")
    created_at: Mapped[datetime] = _now()


class BusinessProfile(Base):
    """Structured facts from the website, confirmed by the owner (DEC-11). Places: only place_id (R9)."""

    __tablename__ = "business_profile"

    business_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("businesses.id", ondelete="CASCADE"), primary_key=True
    )
    name: Mapped[str | None] = mapped_column(Text)
    address: Mapped[str | None] = mapped_column(Text)
    phone: Mapped[str | None] = mapped_column(Text)
    email: Mapped[str | None] = mapped_column(Text)
    opening_hours: Mapped[dict | None] = mapped_column(JSONB)
    booking_policy: Mapped[str | None] = mapped_column(Text)
    price_range: Mapped[str | None] = mapped_column(Text)
    languages: Mapped[list[str] | None] = mapped_column(ARRAY(Text))
    place_id: Mapped[str | None] = mapped_column(Text)
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = _now()


class Page(Base):
    """One cleaned page of the business website, as Markdown (DEC-09)."""

    __tablename__ = "pages"
    __table_args__ = (
        UniqueConstraint("business_id", "url"),
        UniqueConstraint("business_id", "id"),
    )

    id: Mapped[uuid.UUID] = _id()
    business_id: Mapped[uuid.UUID] = _business_id()
    url: Mapped[str] = mapped_column(Text, nullable=False)
    title: Mapped[str | None] = mapped_column(Text)
    markdown: Mapped[str] = mapped_column(Text, nullable=False)
    content_hash: Mapped[str] = mapped_column(Text, nullable=False)
    scraped_at: Mapped[datetime] = _now()


class CustomReply(Base):
    """Owner-written answer; also chunked (kind='custom_reply') and boosted in retrieval."""

    __tablename__ = "custom_replies"
    __table_args__ = (UniqueConstraint("business_id", "id"),)

    id: Mapped[uuid.UUID] = _id()
    business_id: Mapped[uuid.UUID] = _business_id()
    question: Mapped[str] = mapped_column(Text, nullable=False)
    answer: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = _now()
    updated_at: Mapped[datetime] = _now()


class Chunk(Base):
    """Retrieval unit. `embedding` is NULL until the indexer fills it (step 3)."""

    __tablename__ = "chunks"
    __table_args__ = (
        CheckConstraint("kind IN ('scraped', 'custom_reply')", name="kind"),
        CheckConstraint(
            "(kind = 'scraped' AND page_id IS NOT NULL AND custom_reply_id IS NULL)"
            " OR (kind = 'custom_reply' AND custom_reply_id IS NOT NULL AND page_id IS NULL)",
            name="source",
        ),
        ForeignKeyConstraint(["business_id", "page_id"], ["pages.business_id", "pages.id"], ondelete="CASCADE"),
        ForeignKeyConstraint(
            ["business_id", "custom_reply_id"],
            ["custom_replies.business_id", "custom_replies.id"],
            ondelete="CASCADE",
        ),
        Index("ix_chunks_tsv", "tsv", postgresql_using="gin"),
    )

    id: Mapped[uuid.UUID] = _id()
    business_id: Mapped[uuid.UUID] = _business_id()
    page_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), index=True)
    custom_reply_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    kind: Mapped[str] = mapped_column(Text, nullable=False)
    chunk_index: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    section_heading: Mapped[str | None] = mapped_column(Text)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    embedding: Mapped[list[float] | None] = mapped_column(Vector(EMBED_DIM))
    embed_model: Mapped[str | None] = mapped_column(Text)
    # 'simple' config: no language-specific stemming — sites and callers may use any language.
    tsv: Mapped[str] = mapped_column(TSVECTOR, Computed("to_tsvector('simple', text)", persisted=True))
    content_hash: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = _now()


class Conversation(Base):
    __tablename__ = "conversations"
    __table_args__ = (
        CheckConstraint("channel IN ('chat', 'voice')", name="channel"),
        UniqueConstraint("business_id", "id"),
    )

    id: Mapped[uuid.UUID] = _id()
    business_id: Mapped[uuid.UUID] = _business_id()
    channel: Mapped[str] = mapped_column(Text, nullable=False)
    caller: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime] = _now()
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Message(Base):
    __tablename__ = "messages"
    __table_args__ = (
        CheckConstraint("role IN ('user', 'assistant', 'tool', 'system')", name="role"),
        ForeignKeyConstraint(
            ["business_id", "conversation_id"],
            ["conversations.business_id", "conversations.id"],
            ondelete="CASCADE",
        ),
    )

    id: Mapped[uuid.UUID] = _id()
    business_id: Mapped[uuid.UUID] = _business_id()
    conversation_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)
    role: Mapped[str] = mapped_column(Text, nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = _now()


def message_order() -> tuple:
    """ORDER BY for a conversation's messages. A question and its answer are saved in one
    transaction, so both get the same `now()` (transaction start); `created_at` alone
    leaves their order undefined. Within a tie the question comes first."""
    return Message.created_at, case((Message.role == "user", 0), else_=1)
