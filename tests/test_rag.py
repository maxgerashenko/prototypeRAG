"""RAG pipeline tests (plan/02-local-rag.md).

Unlike the crawler's fixture-only tests, these need LM Studio running (localhost:1234)
in addition to Postgres — embeddings are the whole point of this part, so there's no
network-free way to test index.py/retrieve.py's vector path meaningfully. Pure-logic
pieces (reciprocal_rank_fusion, prompt formatting) don't need either.
"""

import uuid
from types import SimpleNamespace

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


class FakeLLM:
    """OpenAI-shaped chat client that plays back scripted replies, for the tool loop.
    A reply is a text, or a list of (call_id, tool_name, json_arguments) tool calls."""

    def __init__(self, *replies):
        self.replies = list(replies)
        self.requests: list[dict] = []
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    def _create(self, **kw):
        self.requests.append({**kw, "messages": list(kw["messages"])})  # the loop keeps appending
        reply = self.replies.pop(0)
        calls = [] if isinstance(reply, str) else reply
        if kw.get("stream"):
            return iter(self._chunks(reply, calls))
        message = SimpleNamespace(
            content=reply if isinstance(reply, str) else None,
            tool_calls=[
                SimpleNamespace(id=i, function=SimpleNamespace(name=n, arguments=a)) for i, n, a in calls
            ] or None,
        )
        return SimpleNamespace(choices=[SimpleNamespace(message=message)])

    @staticmethod
    def _chunks(reply, calls):
        def chunk(**delta):
            return SimpleNamespace(choices=[SimpleNamespace(delta=SimpleNamespace(
                **{"content": None, "tool_calls": None, **delta}))])

        if isinstance(reply, str):
            mid = len(reply) // 2
            return [chunk(content=reply[:mid]), chunk(content=reply[mid:])]
        out = []
        for index, (i, n, a) in enumerate(calls):  # name and arguments arrive in pieces
            half = len(a) // 2
            out.append(chunk(tool_calls=[SimpleNamespace(
                index=index, id=i, function=SimpleNamespace(name=n, arguments=a[:half]))]))
            out.append(chunk(tool_calls=[SimpleNamespace(
                index=index, id=None, function=SimpleNamespace(name=None, arguments=a[half:]))]))
        return out


@pytest.fixture
def fake_llm(monkeypatch):
    """Prompt building and tools faked: no DB, no LM Studio. Returns (install_llm, tool_calls)."""
    from app.rag import answer

    monkeypatch.setattr(
        answer, "prepare_prompt",
        lambda business_id, question, history=None, top_n=5: (
            None, [], [{"role": "system", "content": "rules"}, *(history or []), {"role": "user", "content": question}]
        ),
    )
    tool_calls: list[tuple] = []

    def run_tool(business_id, name, arguments, conversation_id=None):
        tool_calls.append((business_id, name, arguments, conversation_id))
        return f"result of {name}"

    monkeypatch.setattr(answer, "run_tool", run_tool)

    def install(*replies):
        llm = FakeLLM(*replies)
        monkeypatch.setattr(answer, "get_chat_client", lambda: llm)
        return llm

    return install, tool_calls


def test_chat_answers_send_reasoning_effort_and_tools(fake_llm):
    """V24: /chat used to leave thinking on (~8 s before the first word on gemma-4-12b).
    Both modes also offer the registered tools."""
    from app.config import get_settings
    from app.rag import answer

    install, _ = fake_llm
    llm = install("ok", "ok")
    bid = uuid.uuid4()  # no rows needed: profile None, retrieval faked
    result = answer.answer_question(bid, "hours?")
    assert result.answer == "ok"
    assert [m["role"] for m in result.messages] == ["system", "user"]  # the prompt, for the debug view
    assert "".join(answer.stream_answer(bid, "hours?")) == "ok"
    effort = get_settings().chat_reasoning_effort  # "none" unless .env overrides it
    assert [r["reasoning_effort"] for r in llm.requests] == [effort, effort]
    from app.voice.tools import TOOLS

    assert all(r["tools"] is TOOLS for r in llm.requests)  # no optional tool enabled for this business


def test_answer_runs_tool_calls_in_a_loop(fake_llm):
    from app.rag import answer

    install, tool_calls = fake_llm
    llm = install(
        [("c1", "search_business_info", '{"query": "parking"}')],
        [("c2", "search_business_info", '{"query": "price"}'), ("c3", "other", "{}")],
        "Parking is free.",
    )
    bid, cid = uuid.uuid4(), uuid.uuid4()
    result = answer.answer_question(bid, "parking?", conversation_id=cid)

    assert result.answer == "Parking is free."
    # business and conversation come from the request, not from the model's arguments
    assert tool_calls == [
        (bid, "search_business_info", '{"query": "parking"}', cid),
        (bid, "search_business_info", '{"query": "price"}', cid),
        (bid, "other", "{}", cid),
    ]
    last = llm.requests[-1]["messages"]
    assert [m["role"] for m in last] == ["system", "user", "assistant", "tool", "assistant", "tool", "tool"]
    assert last[2]["tool_calls"][0]["id"] == "c1" and last[3]["tool_call_id"] == "c1"
    assert last[3]["content"] == "result of search_business_info"
    assert [m["role"] for m in result.messages] == ["system", "user"]  # debug prompt stays the first one


def test_answer_gives_up_after_max_tool_rounds(fake_llm):
    from app.rag import answer

    install, tool_calls = fake_llm
    llm = install(*[[(f"c{i}", "search_business_info", "{}")] for i in range(answer.MAX_TOOL_ROUNDS)])
    result = answer.answer_question(uuid.uuid4(), "loop?")
    assert result.answer == answer.GIVE_UP_ANSWER
    assert len(llm.requests) == len(tool_calls) == answer.MAX_TOOL_ROUNDS


def test_stream_answer_runs_tool_calls_in_a_loop(fake_llm):
    from app.rag import answer

    install, tool_calls = fake_llm
    llm = install(
        [("c1", "search_business_info", '{"query": "parking"}')],
        "Parking is free.",
    )
    bid, cid = uuid.uuid4(), uuid.uuid4()
    pieces = list(answer.stream_answer(bid, "parking?", conversation_id=cid))

    assert "".join(pieces) == "Parking is free."
    assert tool_calls == [(bid, "search_business_info", '{"query": "parking"}', cid)]  # pieces joined back
    messages = llm.requests[-1]["messages"]
    assert messages[-2]["tool_calls"][0]["function"]["arguments"] == '{"query": "parking"}'
    assert messages[-1] == {"role": "tool", "tool_call_id": "c1", "content": "result of search_business_info"}


def test_stream_answer_gives_up_after_max_tool_rounds(fake_llm):
    from app.rag import answer

    install, _ = fake_llm
    install(*[[(f"c{i}", "search_business_info", "{}")] for i in range(answer.MAX_TOOL_ROUNDS)])
    assert list(answer.stream_answer(uuid.uuid4(), "loop?")) == [answer.GIVE_UP_ANSWER]


def test_chat_and_debug_prompt_return_the_full_prompt(business_id, monkeypatch):
    """The debug view's prompt is the exact prompt sent to the LLM: system (rules +
    profile + chunks) + history + question. /debug/prompt builds it without the LLM
    and without creating a conversation."""
    from fastapi.testclient import TestClient

    from app.db.models import Conversation, Message
    from app.main import app
    from app.rag import answer

    chunk = RetrievedChunk(chunk_id=uuid.uuid4(), text="We open at 9.", section_heading="Hours",
                           kind="scraped", score=0.9, source="both")
    monkeypatch.setattr(answer, "retrieve", lambda *a, **kw: [chunk])
    llm = FakeLLM("At 9.")
    monkeypatch.setattr(answer, "get_chat_client", lambda: llm)
    with tenant_session(business_id) as s:
        s.add(BusinessProfile(business_id=business_id, name="Cafe Uno"))

    client = TestClient(app)
    r = client.post("/chat", json={"business_id": str(business_id), "question": "When do you open?"}).json()
    assert r["prompt"] == llm.requests[0]["messages"]
    system = r["prompt"][0]
    assert system["role"] == "system" and "Name: Cafe Uno" in system["content"] and "[1] Hours\nWe open at 9." in system["content"]
    assert r["prompt"][-1] == {"role": "user", "content": "When do you open?"}

    d = client.get("/debug/prompt", params={"business_id": str(business_id), "q": "And close?",
                                            "conversation_id": r["conversation_id"]}).json()
    assert [(m["role"], m["content"]) for m in d["messages"][1:]] == [
        ("user", "When do you open?"), ("assistant", "At 9."), ("user", "And close?")]
    assert d["messages"][0] == system
    assert len(llm.requests) == 1  # no LLM call

    client.get("/debug/prompt", params={"business_id": str(business_id), "q": "x"})
    with tenant_session(business_id) as s:
        assert len(s.scalars(select(Conversation.id).where(Conversation.business_id == business_id)).all()) == 1
    assert client.get("/debug/prompt", params={"business_id": str(business_id), "q": "x",
                                               "conversation_id": str(uuid.uuid4())}).status_code == 404


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
