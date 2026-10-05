"""RAG pipeline tests (plan/02-local-rag.md).

Unlike the crawler's fixture-only tests, these need LM Studio running (localhost:1234)
in addition to Postgres — embeddings are the whole point of this part, so there's no
network-free way to test index.py/retrieve.py's vector path meaningfully. Pure-logic
pieces (reciprocal_rank_fusion, prompt formatting) don't need either.
"""

import uuid

import pytest
from sqlalchemy import delete, select

from app.api.chat import _sse
from app.db import tenant_session
from app.db.models import Business, BusinessProfile, Chunk, CustomReply, Page
from app.rag.index import chunks_needing_embedding, index_business
from app.rag.prompt import build_prompt, format_chunks, format_profile
from app.rag.retrieve import RetrievedChunk, keyword_search, reciprocal_rank_fusion, retrieve, vector_search


@pytest.fixture
def business_id():
    bid = uuid.uuid4()
    with tenant_session(bid) as s:
        s.add(Business(id=bid, name="RAG Test"))
    yield bid
    with tenant_session(bid) as s:
        s.execute(delete(Business).where(Business.id == bid))


def _add_chunk(session, business_id, text, page_id=None, kind="scraped", custom_reply_id=None, **kw):
    if page_id is None and custom_reply_id is None:
        page = Page(business_id=business_id, url=f"https://x/{uuid.uuid4()}", markdown="x", content_hash="h")
        session.add(page)
        session.flush()
        page_id = page.id
    chunk = Chunk(
        business_id=business_id, page_id=page_id, custom_reply_id=custom_reply_id,
        kind=kind, text=text, content_hash=text, **kw,
    )
    session.add(chunk)
    session.flush()
    return chunk


def test_chat_answers_send_reasoning_effort(monkeypatch):
    """V24: /chat used to leave thinking on (~8 s before the first word on gemma-4-12b)."""
    from types import SimpleNamespace

    from app.config import get_settings
    from app.rag import answer

    monkeypatch.setattr(answer, "retrieve", lambda *a: [])
    sent: list[dict] = []
    monkeypatch.setattr(answer, "chat", lambda messages, **kw: sent.append(kw) or "ok")

    def create(**kw):
        sent.append(kw)
        return iter([SimpleNamespace(choices=[SimpleNamespace(delta=SimpleNamespace(content="ok"))])])

    fake_client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
    monkeypatch.setattr(answer, "get_chat_client", lambda: fake_client)

    bid = uuid.uuid4()  # no rows needed: profile None, retrieval faked
    assert answer.answer_question(bid, "hours?").answer == "ok"
    assert list(answer.stream_answer(bid, "hours?")) == ["ok"]
    effort = get_settings().chat_reasoning_effort  # "none" unless .env overrides it
    assert [kw["reasoning_effort"] for kw in sent] == [effort, effort]


# --- reciprocal_rank_fusion (pure math, no DB/network) -------------------------------


def test_rrf_combines_scores_for_docs_in_both_rankings():
    a, b, c = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    vector_ranks = [(a, 1), (b, 2)]
    keyword_ranks = [(a, 3), (c, 1)]
    scores = reciprocal_rank_fusion(vector_ranks, keyword_ranks, k=60)
    assert scores[a] == pytest.approx(1 / 61 + 1 / 63)  # in both -> sum of both terms
    assert scores[b] == pytest.approx(1 / 62)  # vector only
    assert scores[c] == pytest.approx(1 / 61)  # keyword only
    assert scores[a] > scores[b] and scores[a] > scores[c]  # appearing in both wins


def test_rrf_empty_rankings_give_empty_result():
    assert reciprocal_rank_fusion([], []) == {}


# --- prompt.py (pure formatting, no DB/network) ---------------------------------------


def test_format_profile_skips_unset_fields_and_hides_internal_ids():
    profile = BusinessProfile(business_id=uuid.uuid4(), name="Acme", address=None, phone="555-0100")
    text = format_profile(profile)
    assert "Name: Acme" in text
    assert "Phone: 555-0100" in text
    assert "Address" not in text  # unset field skipped
    assert str(profile.business_id) not in text  # no internal ids


def test_format_profile_none_and_all_unset():
    assert format_profile(None) == "No business profile available."
    assert format_profile(BusinessProfile(business_id=uuid.uuid4())) == "No business profile available."


def test_format_chunks_numbers_and_handles_empty():
    chunks = [
        RetrievedChunk(chunk_id=uuid.uuid4(), text="first", section_heading="A", kind="scraped", score=1.0, source="vector"),
        RetrievedChunk(chunk_id=uuid.uuid4(), text="second", section_heading=None, kind="scraped", score=0.5, source="keyword"),
    ]
    formatted = format_chunks(chunks)
    assert "[1] A\nfirst" in formatted
    assert "[2] (no heading)\nsecond" in formatted
    assert format_chunks([]) == "No matching information found in the knowledge base."


def test_build_prompt_order_system_history_question():
    messages = build_prompt(None, [], [{"role": "user", "content": "hi"}, {"role": "assistant", "content": "hello"}], "q?")
    assert [m["role"] for m in messages] == ["system", "user", "assistant", "user"]
    assert messages[-1]["content"] == "q?"


def test_sse_frame_keeps_multiline_answers():
    """Regression: a raw newline inside `data:` ended the SSE field, so the client
    silently dropped the rest of a piece (lists, paragraphs)."""
    assert _sse("Hours:\n- Mon 9-5") == "data: Hours:\ndata: - Mon 9-5\n\n"
    assert _sse("x", event="meta") == "event: meta\ndata: x\n\n"


# --- retrieve.py's keyword_search: the AND-vs-OR fix ----------------------------------


def test_keyword_search_ors_question_words_not_ands_them(business_id):
    """Regression: plainto_tsquery ANDs every word incl. stopwords -- a natural
    question like "what do you have" would then require ALL those words present in
    one chunk, which real content almost never satisfies."""
    with tenant_session(business_id) as s:
        _add_chunk(s, business_id, "We have saunas and pools.")
        hits = keyword_search(s, business_id, "What saunas and pools do you have?", top_k=10)
    assert len(hits) >= 1, "keyword_search found nothing -- the AND-query bug may have regressed"


def test_keyword_search_empty_and_punctuation_only_dont_crash(business_id):
    with tenant_session(business_id) as s:
        assert keyword_search(s, business_id, "", top_k=10) == []
        assert keyword_search(s, business_id, "   ", top_k=10) == []
        assert keyword_search(s, business_id, "???", top_k=10) == []  # no lexemes after tokenizing


# --- index.py + retrieve.py's vector path: needs a real embedding model --------------


def test_index_and_vector_search_roundtrip(business_id):
    with tenant_session(business_id) as s:
        _add_chunk(s, business_id, "The sauna is open until 10pm every day.")
        _add_chunk(s, business_id, "Our rooftop pool has views of the city.")
        needing = chunks_needing_embedding(s, business_id)
    assert len(needing) == 2

    summary = index_business(business_id)
    assert summary == {"chunks_indexed": 2, "batches_failed": 0}

    # idempotent: a second run should find nothing left to do
    assert index_business(business_id) == {"chunks_indexed": 0, "batches_failed": 0}

    with tenant_session(business_id) as s:
        from app.llm import embed
        query_vec = embed(["search_query: when does the sauna close?"])[0]
        hits = vector_search(s, business_id, query_vec, top_k=5)
        texts = {c.id: c.text for c in s.scalars(select(Chunk).where(Chunk.business_id == business_id))}
    assert hits, "vector_search returned nothing after indexing"
    best_id, best_rank = hits[0]
    assert best_rank == 1
    assert "sauna" in texts[best_id].lower()


def test_custom_reply_boost_only_applies_to_already_retrieved_chunks(business_id):
    """A custom reply never retrieved by vector or keyword search must not appear just
    because of the boost -- the boost multiplies existing fused scores, it doesn't add
    custom replies to the result set."""
    with tenant_session(business_id) as s:
        _add_chunk(s, business_id, "General opening hours information for the spa.")
        reply = CustomReply(business_id=business_id, question="unrelated", answer="zzqx")
        s.add(reply)
        s.flush()
        _add_chunk(
            s, business_id, "Q: completely unrelated gibberish zzqx\nA: zzqx",
            kind="custom_reply", custom_reply_id=reply.id,
        )
        index_business(business_id)

    with tenant_session(business_id) as s:
        results = retrieve(s, business_id, "what are your opening hours?", top_n=5)
    kinds = [r.kind for r in results]
    assert "scraped" in kinds
    # the irrelevant custom reply shouldn't be pulled in just by existing / being boostable
    assert not any(r.text.startswith("Q: completely unrelated") for r in results)
