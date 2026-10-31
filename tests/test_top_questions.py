"""app/insights/questions.py: pure functions, hand-made 3-D vectors, no database."""

from datetime import UTC, datetime, timedelta

import pytest

from app.insights.questions import Turn, cluster, is_question, medoid, pair_turns, top_questions

T0 = datetime(2026, 10, 1, 12, 0, tzinfo=UTC)


def at(s):
    return T0 + timedelta(seconds=s)


def turn(q, a=None, s=0, conv="c1"):
    return Turn(q, a, conv, at(s))


def test_pair_turns_matches_answers():
    rows = [
        ("c1", "assistant", "Hi", at(0)),
        ("c1", "user", "Open?", at(1)),
        ("c1", "assistant", "Yes", at(2)),
        ("c1", "assistant", "Anything else?", at(3)),
        ("c1", "user", "Price?", at(4)),
        ("c1", "assistant", "10", at(5)),
    ]
    turns = pair_turns(rows)
    assert [(t.question, t.answer) for t in turns] == [("Open?", "Yes"), ("Price?", "10")]


def test_pair_turns_two_user_rows_in_a_row():
    rows = [("c1", "user", "A?", at(1)), ("c1", "user", "B?", at(2)), ("c1", "assistant", "b", at(3))]
    assert [(t.question, t.answer) for t in pair_turns(rows)] == [("A?", None), ("B?", "b")]


def test_pair_turns_no_leak_across_conversations_and_ignores_other_roles():
    rows = [
        ("c1", "user", "A?", at(1)),
        ("c2", "assistant", "not for A", at(2)),
        ("c2", "user", "B?", at(3)),
        ("c2", "tool", "{}", at(4)),
        ("c2", "system", "x", at(5)),
        ("c2", "assistant", "b", at(6)),
    ]
    assert [(t.question, t.answer) for t in pair_turns(rows)] == [("A?", None), ("B?", "b")]


@pytest.mark.parametrize("text,expected", [
    ("yes", False), ("ok thanks", False), ("do you take walk ins", True), ("Open?", True),
    ("", False), ("   ", False), ("x" * 300, False), ("x" * 299 + "?", True), ("x" * 301, False),
    ("x" * 300 + "?", False),
])
def test_is_question(text, expected):
    assert is_question(text) is expected


def test_cluster_merges_splits_and_threshold_is_inclusive():
    assert cluster([[1, 0, 0], [2, 0, 0], [0, 1, 0]], 0.8) == [[0, 1], [2]]
    assert cluster([[1, 0, 0], [0.8, 0.6, 0]], 0.8) == [[0, 1]]  # cosine exactly 0.8
    assert cluster([[1, 0, 0], [0.8, 0.6, 0]], 0.81) == [[0], [1]]


def test_cluster_zero_vector_and_empty():
    assert cluster([[0, 0, 0], [0, 0, 0], [1, 0, 0]], 0.5) == [[0], [1], [2]]
    assert cluster([]) == []


def test_cluster_picks_best_leader():
    vecs = [[1, 0, 0], [0.6, 0.8, 0], [0.7, 0.7, 0]]
    # 2 matches leader 1 (0.99) better than leader 0 (0.71)
    assert cluster(vecs, 0.7) == [[0], [1, 2]]


def test_medoid():
    vecs = [[1, 0, 0], [0.9, 0.1, 0], [0.8, 0.2, 0]]
    assert medoid(vecs, [0, 1, 2]) == 1
    assert medoid([[1, 0, 0], [1, 0, 0]], [0, 1]) == 0  # tie -> earliest
    assert medoid(vecs, [2]) == 2


def test_top_questions_percent_order_and_cleaning():
    turns = [turn("Are you open Sunday.", "Yes", 1), turn("Are you open Sunday?", None, 2),
             turn("Are you open Sunday?!", "Yes!", 3), turn("Price?", "10", 4)]
    vecs = [[1, 0, 0], [1, 0, 0], [1, 0, 0], [0, 1, 0]]
    out = top_questions(turns, vecs)
    assert [(d["count"], d["percent"]) for d in out] == [(3, 75), (1, 25)]
    assert out[0]["question"] == "Are you open Sunday?"
    assert out[0]["answer"] == "Yes"
    assert set(out[0]) == {"question", "count", "percent", "answer"}


def test_top_questions_recency_tie_break_and_answer_fallback():
    turns = [turn("Old?", "o", 1), turn("New?", "n", 5)]
    out = top_questions(turns, [[1, 0, 0], [0, 1, 0]])
    assert [d["question"] for d in out] == ["New?", "Old?"]
    # medoid (earliest on a tie) has no answer -> most recent answered turn's answer
    turns = [turn("Hours?", None, 1), turn("Hours??", "late", 3), turn("Hours?!", "early", 2)]
    out = top_questions(turns, [[1, 0, 0]] * 3)
    assert out[0]["answer"] == "late"
    assert top_questions([turn("Nope?", None)], [[1, 0, 0]])[0]["answer"] is None


def test_top_questions_limit_mismatch_empty():
    turns = [turn(f"Q{i}?", None, i) for i in range(3)]
    vecs = [[1, 0, 0], [0, 1, 0], [0, 0, 1]]
    assert len(top_questions(turns, vecs, limit=2)) == 2
    with pytest.raises(ValueError):
        top_questions(turns, vecs[:2])
    assert top_questions([], []) == []
