"""Eval script: run test questions, check expected facts appear, report a score
(plan/02-local-rag.md). See tests/eval/bathhouse.yaml for the file format.
"""

import argparse
import uuid
from dataclasses import dataclass, field

import yaml

from app.rag.answer import answer_question
from app.rag.prompt import format_chunks

REFUSAL_PHRASES = [
    "don't know", "do not know", "not sure", "unable to find",
    "couldn't find", "not available", "doesn't mention", "no information",
    "don't offer", "do not offer", "don't have", "do not have",
    "not something we offer", "not something we provide",
]


@dataclass(frozen=True)
class EvalResult:
    question: str
    passed: bool
    manual: bool  # excluded from the pass rate (plan/07-knowledge-quality.md §7); still run and reported
    answer: str
    missing_facts: list[str]
    chunks_retrieved: int  # retrieval quality, reported alongside generation quality
    tool_result_chars: int  # size of the formatted context handed to the LLM (V16, §7 "Tool result size")
    context_hit: bool | None = None  # expected_in_context check; None when the entry has none
    missing_in_context: list[str] = field(default_factory=list)


def load_eval_file(path: str) -> list[dict]:
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f)


def check_answer(answer: str, entry: dict) -> tuple[bool, list[str]]:
    """Pure, no I/O. expected_facts: all must appear. in_knowledge_base=False: any refusal phrase must appear."""
    answer_lower = answer.lower()
    if "expected_facts" in entry:
        missing = [fact for fact in entry["expected_facts"] if fact.lower() not in answer_lower]
        return not missing, missing
    if entry.get("in_knowledge_base") is False:
        return any(phrase in answer_lower for phrase in REFUSAL_PHRASES), []
    return False, []


def check_context(context_text: str, entry: dict) -> tuple[bool, list[str]] | tuple[None, list]:
    """expected_in_context: substrings that must appear in the retrieved context (not the
    answer) — isolates a retrieval miss from a generation miss. None if the entry has no
    such field (not every question is checked this way, e.g. the out-of-scope ones)."""
    if "expected_in_context" not in entry:
        return None, []
    context_lower = context_text.lower()
    missing = [s for s in entry["expected_in_context"] if s.lower() not in context_lower]
    return not missing, missing


def run_eval(business_id: uuid.UUID, eval_path: str) -> list[EvalResult]:
    results = []
    for entry in load_eval_file(eval_path):
        question = entry["question"]
        manual = bool(entry.get("manual"))
        answer_result = answer_question(business_id, question)
        context_text = format_chunks(answer_result.chunks)
        passed, missing_facts = check_answer(answer_result.answer, entry)
        context_hit, missing_in_context = check_context(context_text, entry)
        results.append(EvalResult(
            question=question, passed=passed, manual=manual, answer=answer_result.answer,
            missing_facts=missing_facts, chunks_retrieved=len(answer_result.chunks),
            tool_result_chars=len(context_text),
            context_hit=context_hit, missing_in_context=missing_in_context,
        ))
        tag = "MANUAL" if manual else ("PASS" if passed else "FAIL")
        print(f"[{tag}] {question}  (context {len(context_text)} chars, {answer_result.chunks and len(answer_result.chunks)} chunks)")
    return results


def print_report(results: list[EvalResult]) -> None:
    graded = [r for r in results if not r.manual]
    total = len(graded)
    passed = sum(r.passed for r in graded)
    rate = (passed / total * 100) if total else 0.0
    context_checked = [r for r in results if r.context_hit is not None]
    context_hits = sum(1 for r in context_checked if r.context_hit)
    context_rate = (context_hits / len(context_checked) * 100) if context_checked else 0.0
    avg_chars = sum(r.tool_result_chars for r in results) / len(results) if results else 0.0
    print(
        f"\n--- Eval Report ---\n"
        f"Total (graded): {total}  Passed: {passed}  Pass rate: {rate:.1f}%\n"
        f"Retrieval hit@k (expected_in_context): {context_hits}/{len(context_checked)} "
        f"({context_rate:.1f}%)\n"
        f"Avg tool-result size: {avg_chars:.0f} chars\n"
        f"Manual (excluded from pass rate): {sum(r.manual for r in results)}\n"
        f"-------------------"
    )
    for r in results:
        if r.manual:
            print(f"\n[manual] {r.question}\n  answer: {r.answer}")
            continue
        if not r.passed:
            print(f"\nFailed: {r.question}")
            print(f"Missing facts: {r.missing_facts}" if r.missing_facts else "(expected a refusal, got a confident answer)")
        if r.context_hit is False:
            print(f"Context miss: {r.question} -- missing {r.missing_in_context} (retrieved {r.chunks_retrieved} chunks, {r.tool_result_chars} chars)")


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the RAG eval against a business")
    parser.add_argument("--business-id", required=True, type=uuid.UUID)
    parser.add_argument("--file", required=True, help="Path to the YAML eval file")
    args = parser.parse_args()
    print_report(run_eval(args.business_id, args.file))


if __name__ == "__main__":
    main()
