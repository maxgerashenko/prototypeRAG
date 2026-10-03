"""Eval script: run test questions, check expected facts appear, report a score
(plan/02-local-rag.md). See tests/eval/bathhouse.yaml for the file format.
"""

import argparse
import uuid
from dataclasses import dataclass

import yaml

from app.rag.answer import answer_question

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
    answer: str
    missing_facts: list[str]
    chunks_retrieved: int  # retrieval quality, reported alongside generation quality


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


def run_eval(business_id: uuid.UUID, eval_path: str) -> list[EvalResult]:
    results = []
    for entry in load_eval_file(eval_path):
        question = entry["question"]
        answer_result = answer_question(business_id, question)
        passed, missing_facts = check_answer(answer_result.answer, entry)
        results.append(EvalResult(
            question=question, passed=passed, answer=answer_result.answer,
            missing_facts=missing_facts, chunks_retrieved=len(answer_result.chunks),
        ))
        print(f"[{'PASS' if passed else 'FAIL'}] {question}")
    return results


def print_report(results: list[EvalResult]) -> None:
    total = len(results)
    passed = sum(r.passed for r in results)
    rate = (passed / total * 100) if total else 0.0
    print(f"\n--- Eval Report ---\nTotal: {total}  Passed: {passed}  Pass rate: {rate:.1f}%\n-------------------")
    for r in results:
        if not r.passed:
            print(f"\nFailed: {r.question}")
            print(f"Missing facts: {r.missing_facts}" if r.missing_facts else "(expected a refusal, got a confident answer)")


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the RAG eval against a business")
    parser.add_argument("--business-id", required=True, type=uuid.UUID)
    parser.add_argument("--file", required=True, help="Path to the YAML eval file")
    args = parser.parse_args()
    print_report(run_eval(args.business_id, args.file))


if __name__ == "__main__":
    main()
