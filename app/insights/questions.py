"""Top questions: pair each caller turn with its answer, group near-duplicate questions by
embedding similarity and rank the groups. Pure (numpy, no I/O): the API layer loads the
messages and calls `app.llm.embed` (app/api/business_settings.py)."""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime

import numpy as np

MIN_WORDS = 4  # a statement this short or longer counts as a question even without "?"
MAX_QUESTION_LEN = 300
# Guess for local nomic embeddings; tuned on pilot data (plan/feature-business-settings.md step 6)
DEFAULT_THRESHOLD = 0.80


@dataclass(frozen=True)
class Turn:
    question: str
    answer: str | None
    conversation_id: object
    at: datetime


def pair_turns(rows: Sequence[tuple]) -> list[Turn]:
    """`rows` = (conversation_id, role, content, created_at), ordered by conversation then
    time. Each `user` row becomes a Turn; its answer is the first `assistant` row after it
    in the same conversation, before the next `user` row. Other roles are ignored."""
    turns: list[Turn] = []
    open_idx: int | None = None  # index in `turns` still waiting for an answer
    current = None
    for conversation_id, role, content, at in rows:
        if conversation_id != current:
            current = conversation_id
            open_idx = None
        if role == "user":
            turns.append(Turn(question=content, answer=None, conversation_id=conversation_id, at=at))
            open_idx = len(turns) - 1
        elif role == "assistant" and open_idx is not None:
            t = turns[open_idx]
            turns[open_idx] = Turn(t.question, content, t.conversation_id, t.at)
            open_idx = None
    return turns


def is_question(text: str) -> bool:
    text = (text or "").strip()
    if not text or len(text) > MAX_QUESTION_LEN:
        return False
    return text.endswith("?") or len(text.split()) >= MIN_WORDS


def _normalise(vectors: Sequence[Sequence[float]]) -> np.ndarray:
    arr = np.asarray(vectors, dtype=float)
    norms = np.linalg.norm(arr, axis=1, keepdims=True)
    safe = np.where(norms == 0, 1.0, norms)
    return arr / safe  # a zero vector stays zero, so it is never similar to anything


def cluster(vectors: Sequence[Sequence[float]], threshold: float = DEFAULT_THRESHOLD) -> list[list[int]]:
    """Greedy: walk in order; join the group whose first member (leader) has the highest
    cosine >= threshold, else start a new group. Index lists in creation order."""
    if len(vectors) == 0:
        return []
    unit = _normalise(vectors)
    groups: list[list[int]] = []
    for i in range(len(unit)):
        best, best_sim = None, threshold
        if np.linalg.norm(unit[i]) > 0:
            for g, members in enumerate(groups):
                sim = float(unit[i] @ unit[members[0]])
                if sim >= best_sim and (best is None or sim > best_sim):
                    best, best_sim = g, sim
        if best is None:
            groups.append([i])
        else:
            groups[best].append(i)
    return groups


def medoid(vectors: Sequence[Sequence[float]], idxs: Sequence[int]) -> int:
    """The index in `idxs` with the highest summed cosine to the others; ties go to the earliest."""
    unit = _normalise(vectors)
    best, best_score = idxs[0], None
    for i in idxs:
        score = sum(float(unit[i] @ unit[j]) for j in idxs if j != i)
        if best_score is None or score > best_score + 1e-12:
            best, best_score = i, score
    return best


def _clean(question: str) -> str:
    return question.strip().rstrip("?.!").rstrip() + "?"


def top_questions(
    turns: Sequence[Turn], vectors: Sequence[Sequence[float]], limit: int = 5, threshold: float = DEFAULT_THRESHOLD
) -> list[dict]:
    if len(turns) != len(vectors):
        raise ValueError(f"{len(turns)} turns but {len(vectors)} vectors")
    if not turns:
        return []
    items = []
    for idxs in cluster(vectors, threshold):
        m = medoid(vectors, idxs)
        answer = turns[m].answer
        if answer is None:
            answered = [turns[i] for i in idxs if turns[i].answer is not None]
            if answered:
                answer = max(answered, key=lambda t: t.at).answer
        items.append({
            "question": _clean(turns[m].question),
            "count": len(idxs),
            "percent": round(100 * len(idxs) / len(turns)),
            "answer": answer,
            "_latest": max(turns[i].at for i in idxs),
        })
    items.sort(key=lambda d: (d["count"], d["_latest"]), reverse=True)
    for d in items:
        del d["_latest"]
    return items[:limit]
