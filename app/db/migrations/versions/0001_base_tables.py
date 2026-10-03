"""base tables: businesses, profile, pages, chunks, custom replies, conversations, messages

Revision ID: 0001
Revises: 
Create Date: 2026-10-03 02:02:15.327530
"""

from collections.abc import Sequence

import pgvector.sqlalchemy
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = '0001'
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    op.create_table('businesses',
    sa.Column('id', sa.UUID(), server_default=sa.text('gen_random_uuid()'), nullable=False),
    sa.Column('name', sa.Text(), nullable=False),
    sa.Column('website', sa.Text(), nullable=True),
    sa.Column('timezone', sa.Text(), server_default='UTC', nullable=False),
    sa.Column('phone_numbers', postgresql.ARRAY(sa.Text()), server_default='{}', nullable=False),
    sa.Column('settings', postgresql.JSONB(astext_type=sa.Text()), server_default='{}', nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_businesses'))
    )
    op.create_table('business_profile',
    sa.Column('business_id', sa.UUID(), nullable=False),
    sa.Column('name', sa.Text(), nullable=True),
    sa.Column('address', sa.Text(), nullable=True),
    sa.Column('phone', sa.Text(), nullable=True),
    sa.Column('email', sa.Text(), nullable=True),
    sa.Column('opening_hours', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.Column('booking_policy', sa.Text(), nullable=True),
    sa.Column('price_range', sa.Text(), nullable=True),
    sa.Column('languages', postgresql.ARRAY(sa.Text()), nullable=True),
    sa.Column('place_id', sa.Text(), nullable=True),
    sa.Column('confirmed_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['business_id'], ['businesses.id'], name=op.f('fk_business_profile_business_id_businesses'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('business_id', name=op.f('pk_business_profile'))
    )
    op.create_table('conversations',
    sa.Column('id', sa.UUID(), server_default=sa.text('gen_random_uuid()'), nullable=False),
    sa.Column('business_id', sa.UUID(), nullable=False),
    sa.Column('channel', sa.Text(), nullable=False),
    sa.Column('caller', sa.Text(), nullable=True),
    sa.Column('started_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('ended_at', sa.DateTime(timezone=True), nullable=True),
    sa.CheckConstraint("channel IN ('chat', 'voice')", name=op.f('ck_conversations_channel')),
    sa.ForeignKeyConstraint(['business_id'], ['businesses.id'], name=op.f('fk_conversations_business_id_businesses'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_conversations')),
    sa.UniqueConstraint('business_id', 'id', name=op.f('uq_conversations_business_id_id'))
    )
    op.create_index(op.f('ix_conversations_business_id'), 'conversations', ['business_id'], unique=False)
    op.create_table('custom_replies',
    sa.Column('id', sa.UUID(), server_default=sa.text('gen_random_uuid()'), nullable=False),
    sa.Column('business_id', sa.UUID(), nullable=False),
    sa.Column('question', sa.Text(), nullable=False),
    sa.Column('answer', sa.Text(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['business_id'], ['businesses.id'], name=op.f('fk_custom_replies_business_id_businesses'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_custom_replies')),
    sa.UniqueConstraint('business_id', 'id', name=op.f('uq_custom_replies_business_id_id'))
    )
    op.create_index(op.f('ix_custom_replies_business_id'), 'custom_replies', ['business_id'], unique=False)
    op.create_table('pages',
    sa.Column('id', sa.UUID(), server_default=sa.text('gen_random_uuid()'), nullable=False),
    sa.Column('business_id', sa.UUID(), nullable=False),
    sa.Column('url', sa.Text(), nullable=False),
    sa.Column('title', sa.Text(), nullable=True),
    sa.Column('markdown', sa.Text(), nullable=False),
    sa.Column('content_hash', sa.Text(), nullable=False),
    sa.Column('scraped_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['business_id'], ['businesses.id'], name=op.f('fk_pages_business_id_businesses'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_pages')),
    sa.UniqueConstraint('business_id', 'id', name=op.f('uq_pages_business_id_id')),
    sa.UniqueConstraint('business_id', 'url', name=op.f('uq_pages_business_id_url'))
    )
    op.create_index(op.f('ix_pages_business_id'), 'pages', ['business_id'], unique=False)
    op.create_table('chunks',
    sa.Column('id', sa.UUID(), server_default=sa.text('gen_random_uuid()'), nullable=False),
    sa.Column('business_id', sa.UUID(), nullable=False),
    sa.Column('page_id', sa.UUID(), nullable=True),
    sa.Column('custom_reply_id', sa.UUID(), nullable=True),
    sa.Column('kind', sa.Text(), nullable=False),
    sa.Column('chunk_index', sa.Integer(), server_default='0', nullable=False),
    sa.Column('section_heading', sa.Text(), nullable=True),
    sa.Column('text', sa.Text(), nullable=False),
    sa.Column('embedding', pgvector.sqlalchemy.vector.VECTOR(dim=768), nullable=True),
    sa.Column('embed_model', sa.Text(), nullable=True),
    sa.Column('tsv', postgresql.TSVECTOR(), sa.Computed("to_tsvector('simple', text)", persisted=True), nullable=False),
    sa.Column('content_hash', sa.Text(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint("(kind = 'scraped' AND page_id IS NOT NULL AND custom_reply_id IS NULL) OR (kind = 'custom_reply' AND custom_reply_id IS NOT NULL AND page_id IS NULL)", name=op.f('ck_chunks_source')),
    sa.CheckConstraint("kind IN ('scraped', 'custom_reply')", name=op.f('ck_chunks_kind')),
    sa.ForeignKeyConstraint(['business_id', 'custom_reply_id'], ['custom_replies.business_id', 'custom_replies.id'], name=op.f('fk_chunks_business_id_custom_reply_id_custom_replies'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['business_id', 'page_id'], ['pages.business_id', 'pages.id'], name=op.f('fk_chunks_business_id_page_id_pages'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['business_id'], ['businesses.id'], name=op.f('fk_chunks_business_id_businesses'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_chunks'))
    )
    op.create_index(op.f('ix_chunks_business_id'), 'chunks', ['business_id'], unique=False)
    op.create_index(op.f('ix_chunks_page_id'), 'chunks', ['page_id'], unique=False)
    op.create_index('ix_chunks_tsv', 'chunks', ['tsv'], unique=False, postgresql_using='gin')
    op.create_table('messages',
    sa.Column('id', sa.UUID(), server_default=sa.text('gen_random_uuid()'), nullable=False),
    sa.Column('business_id', sa.UUID(), nullable=False),
    sa.Column('conversation_id', sa.UUID(), nullable=False),
    sa.Column('role', sa.Text(), nullable=False),
    sa.Column('content', sa.Text(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint("role IN ('user', 'assistant', 'tool', 'system')", name=op.f('ck_messages_role')),
    sa.ForeignKeyConstraint(['business_id', 'conversation_id'], ['conversations.business_id', 'conversations.id'], name=op.f('fk_messages_business_id_conversation_id_conversations'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['business_id'], ['businesses.id'], name=op.f('fk_messages_business_id_businesses'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_messages'))
    )
    op.create_index(op.f('ix_messages_business_id'), 'messages', ['business_id'], unique=False)
    op.create_index(op.f('ix_messages_conversation_id'), 'messages', ['conversation_id'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_messages_conversation_id'), table_name='messages')
    op.drop_index(op.f('ix_messages_business_id'), table_name='messages')
    op.drop_table('messages')
    op.drop_index('ix_chunks_tsv', table_name='chunks', postgresql_using='gin')
    op.drop_index(op.f('ix_chunks_page_id'), table_name='chunks')
    op.drop_index(op.f('ix_chunks_business_id'), table_name='chunks')
    op.drop_table('chunks')
    op.drop_index(op.f('ix_pages_business_id'), table_name='pages')
    op.drop_table('pages')
    op.drop_index(op.f('ix_custom_replies_business_id'), table_name='custom_replies')
    op.drop_table('custom_replies')
    op.drop_index(op.f('ix_conversations_business_id'), table_name='conversations')
    op.drop_table('conversations')
    op.drop_table('business_profile')
    op.drop_table('businesses')
    # the vector extension is left installed: other database objects may use it
