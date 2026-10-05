"""Base tables (plan/02-local-rag.md → Data model). Every tenant table has `business_id`.

Child tables reference their parent by `(business_id, id)`, so a row can never point at
another business's row — the database rejects cross-tenant links even before RLS.
Action tables (bookings, ...) are added in step 5 (plan/04-actions.md).
"""

import uuid
from datetime import datetime

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    Boolean,
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
    # Business identity (plan/01-crawler.md): the site's host, www-stripped
    # (app/ingest/identity.py:domain_of). NULL for a business without a crawlable
    # website. Plain UNIQUE already allows multiple NULLs in Postgres -- no need for a
    # separate partial index to get "unique where not null".
    domain: Mapped[str | None] = mapped_column(Text, unique=True)
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


class Location(Base):
    """One location of a business (plan/01-crawler.md: "assign a default location").
    The crawler's start URL becomes a location; the first one created for a business is
    its default. Exactly one default per business -- partial unique index below.
    `timezone` is left for the owner/V22 to fill in; the business-level timezone is the
    fallback until then.
    """

    __tablename__ = "locations"
    __table_args__ = (
        UniqueConstraint("business_id", "id"),
        UniqueConstraint("business_id", "url"),
        Index(
            "uq_locations_business_id_default", "business_id", unique=True,
            postgresql_where=text("is_default"),
        ),
    )

    id: Mapped[uuid.UUID] = _id()
    business_id: Mapped[uuid.UUID] = _business_id()
    name: Mapped[str | None] = mapped_column(Text)
    url: Mapped[str | None] = mapped_column(Text)
    address: Mapped[str | None] = mapped_column(Text)
    timezone: Mapped[str | None] = mapped_column(Text)
    is_default: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    created_at: Mapped[datetime] = _now()


class Page(Base):
    """One cleaned page of the business website, as Markdown (DEC-09).

    The organize-step metadata below (plan/07-knowledge-quality.md §4/§5.2, DEC-40) is
    written by `app/ingest/organize.py`, never by hand: `page_type`/`location_id` etc.
    are re-derived whenever `content_hash` or `ORGANIZER_VERSION` changes
    (`organized_hash` records what they were last derived from) -- `markdown` itself is
    never overwritten to make room for them.
    """

    __tablename__ = "pages"
    __table_args__ = (
        UniqueConstraint("business_id", "url"),
        UniqueConstraint("business_id", "id"),
        ForeignKeyConstraint(
            ["business_id", "location_id"], ["locations.business_id", "locations.id"], ondelete="SET NULL"
        ),
        ForeignKeyConstraint(
            ["business_id", "duplicate_of"], ["pages.business_id", "pages.id"], ondelete="SET NULL"
        ),
    )

    id: Mapped[uuid.UUID] = _id()
    business_id: Mapped[uuid.UUID] = _business_id()
    url: Mapped[str] = mapped_column(Text, nullable=False)
    title: Mapped[str | None] = mapped_column(Text)
    markdown: Mapped[str] = mapped_column(Text, nullable=False)
    # Site chrome (nav/header/footer), kept apart from `markdown` (DEC-40 K2) so
    # app/ingest/organize.py can dedupe it across pages instead of it being thrown away
    # by clean.py or repeated verbatim in every page's main content.
    chrome_markdown: Mapped[str | None] = mapped_column(Text)
    content_hash: Mapped[str] = mapped_column(Text, nullable=False)
    scraped_at: Mapped[datetime] = _now()

    # --- organize-step metadata (plan/07-knowledge-quality.md §4/§5.2) ---------------
    page_type: Mapped[str | None] = mapped_column(Text)
    location_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    language: Mapped[str | None] = mapped_column(Text)
    retrievable: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))
    duplicate_of: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    organized_hash: Mapped[str | None] = mapped_column(Text)
    extracted_hash: Mapped[str | None] = mapped_column(Text)
    extractor_version: Mapped[str | None] = mapped_column(Text)


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
    """Retrieval unit. `embedding` is NULL until the indexer fills it (step 3).

    `kind='fact'` rows (plan/07-knowledge-quality.md §5.2, DEC-38) are retrieved through
    the same hybrid search as `scraped`/`custom_reply` rows — one per active `Fact`,
    `text = facts.statement`, `fact_id` set instead of `page_id`/`custom_reply_id`.
    `location_id` (any kind) is the chunk-level location tag from A2: NULL for
    business-wide content, set when the content is specific to one location — carried
    alongside the location name already embedded in `text` (e.g. "Bathhouse
    Williamsburg — ...") so keyword search on the location name also works.
    """

    __tablename__ = "chunks"
    __table_args__ = (
        CheckConstraint("kind IN ('scraped', 'custom_reply', 'fact')", name="kind"),
        CheckConstraint(
            "(kind = 'scraped' AND page_id IS NOT NULL AND custom_reply_id IS NULL AND fact_id IS NULL)"
            " OR (kind = 'custom_reply' AND custom_reply_id IS NOT NULL AND page_id IS NULL AND fact_id IS NULL)"
            " OR (kind = 'fact' AND fact_id IS NOT NULL AND page_id IS NULL AND custom_reply_id IS NULL)",
            name="source",
        ),
        ForeignKeyConstraint(["business_id", "page_id"], ["pages.business_id", "pages.id"], ondelete="CASCADE"),
        ForeignKeyConstraint(
            ["business_id", "custom_reply_id"],
            ["custom_replies.business_id", "custom_replies.id"],
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(["business_id", "fact_id"], ["facts.business_id", "facts.id"], ondelete="CASCADE"),
        ForeignKeyConstraint(
            ["business_id", "location_id"], ["locations.business_id", "locations.id"], ondelete="SET NULL"
        ),
        Index("ix_chunks_tsv", "tsv", postgresql_using="gin"),
    )

    id: Mapped[uuid.UUID] = _id()
    business_id: Mapped[uuid.UUID] = _business_id()
    page_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), index=True)
    custom_reply_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    fact_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), index=True)
    location_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), index=True)
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


class Fact(Base):
    """Atomic, sourced, location-scoped fact (plan/07-knowledge-quality.md §5, DEC-38).

    Schema only in this change — extraction/merge (`app/ingest/facts.py`/`merge.py`) is
    the next milestone (OPEN-20). `UNIQUE (business_id, fact_key) WHERE status='active'`
    is the dedupe key: a re-crawl that confirms the same fact updates `last_seen_at`
    instead of inserting a duplicate active row.
    """

    __tablename__ = "facts"
    __table_args__ = (
        UniqueConstraint("business_id", "id"),
        # Partial unique index, not a UniqueConstraint (SQLAlchemy's UniqueConstraint
        # doesn't accept postgresql_where) -- same pattern as Location's
        # uq_locations_business_id_default: only one *active* fact per (business, key).
        Index(
            "uq_facts_business_id_fact_key_active", "business_id", "fact_key", unique=True,
            postgresql_where=text("status = 'active'"),
        ),
        ForeignKeyConstraint(
            ["business_id", "location_id"], ["locations.business_id", "locations.id"], ondelete="SET NULL"
        ),
    )

    id: Mapped[uuid.UUID] = _id()
    business_id: Mapped[uuid.UUID] = _business_id()
    location_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), index=True)
    type: Mapped[str] = mapped_column(Text, nullable=False)
    subject: Mapped[str | None] = mapped_column(Text)
    attribute: Mapped[str | None] = mapped_column(Text)
    value: Mapped[str | None] = mapped_column(Text)
    value_json: Mapped[dict | None] = mapped_column(JSONB)
    statement: Mapped[str] = mapped_column(Text, nullable=False)
    fact_key: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default="candidate")
    origin: Mapped[str] = mapped_column(Text, nullable=False, server_default="crawl")
    confidence: Mapped[float | None] = mapped_column()
    valid_from: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    valid_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    first_seen_at: Mapped[datetime] = _now()
    last_seen_at: Mapped[datetime] = _now()
    owner_confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class FactSource(Base):
    """One page a `Fact` was extracted from, with the verbatim quote found there (§5.3:
    quote must be found in the page text — validated in code, not by this schema)."""

    __tablename__ = "fact_sources"
    __table_args__ = (
        ForeignKeyConstraint(["business_id", "fact_id"], ["facts.business_id", "facts.id"], ondelete="CASCADE"),
        ForeignKeyConstraint(["business_id", "page_id"], ["pages.business_id", "pages.id"], ondelete="CASCADE"),
    )

    id: Mapped[uuid.UUID] = _id()
    business_id: Mapped[uuid.UUID] = _business_id()
    fact_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)
    page_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)
    quote: Mapped[str] = mapped_column(Text, nullable=False)
    page_hash: Mapped[str] = mapped_column(Text, nullable=False)
    extracted_at: Mapped[datetime] = _now()


class BusinessSummary(Base):
    """Business summary from testimonials/marketing text (plan/07-knowledge-quality.md
    §6, DEC-39). Schema only in this change — `app/ingest/summary.py` is a later milestone."""

    __tablename__ = "business_summaries"

    business_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("businesses.id", ondelete="CASCADE"), primary_key=True
    )
    one_liner: Mapped[str | None] = mapped_column(Text)
    description: Mapped[str | None] = mapped_column(Text)
    highlights: Mapped[list[str] | None] = mapped_column(ARRAY(Text))
    guest_themes: Mapped[list[str] | None] = mapped_column(ARRAY(Text))
    tone: Mapped[str | None] = mapped_column(Text)
    source_page_ids: Mapped[list[uuid.UUID] | None] = mapped_column(ARRAY(UUID(as_uuid=True)))
    generated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    owner_confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


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
