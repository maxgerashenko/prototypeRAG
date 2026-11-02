"""Benchmark chat models on the RAG eval: per-question latency and accuracy.

Retrieval runs once per question (same prompt for every model); each model then gets the
same messages and tool loop as app.rag.answer. Times cover the LLM call(s) incl. tool runs,
not retrieval. Usage:
    uv run python scripts/bench_models.py --business-id <uuid> [--file tests/eval/bathhouse.yaml]
"""

import argparse
import statistics
import time
import uuid

from app.config import get_settings
from app.llm import get_anthropic_client, get_chat_client
from app.rag.answer import GIVE_UP_ANSWER, MAX_TOOL_ROUNDS, _run_calls, _tool_call_message, prepare_prompt
from app.rag.eval import check_answer, load_eval_file
from app.voice.tools import tools_for

MODELS = {
    "gemma-4-12b": (get_chat_client, "google/gemma-4-12b", {"reasoning_effort": "none"}),
    "qwen3.6-35b no-think": (get_chat_client, "qwen/qwen3.6-35b-a3b", {"reasoning_effort": "none"}),
    "claude-haiku-5-5": (get_anthropic_client, get_settings().facts_haiku_model, {}),
}


def generate(client, model, extra, business_id, messages, tools) -> str:
    messages = list(messages)
    for _ in range(MAX_TOOL_ROUNDS):
        r = client.chat.completions.create(model=model, messages=messages, tools=tools, **extra)
        m = r.choices[0].message
        if not m.tool_calls:
            return m.content or ""
        calls = [{"id": t.id, "name": t.function.name, "arguments": t.function.arguments} for t in m.tool_calls]
        messages.append(_tool_call_message(m.content, calls))
        messages.extend(_run_calls(business_id, None, calls))
    return GIVE_UP_ANSWER


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--business-id", required=True, type=uuid.UUID)
    ap.add_argument("--file", default="tests/eval/bathhouse.yaml")
    args = ap.parse_args()
    entries = [e for e in load_eval_file(args.file) if not e.get("manual")]
    prompts = {e["question"]: prepare_prompt(args.business_id, e["question"])[2] for e in entries}
    tools = tools_for(args.business_id)
    summary = {}
    for name, (get_client, model, extra) in MODELS.items():
        client = get_client()
        generate(client, model, extra, args.business_id, prompts[entries[0]["question"]], tools)  # load/warm up
        times, passed, rows = [], 0, []
        for e in entries:
            t0 = time.perf_counter()
            answer = generate(client, model, extra, args.business_id, prompts[e["question"]], tools)
            dt = time.perf_counter() - t0
            ok, _ = check_answer(answer, e)
            times.append(dt)
            passed += ok
            rows.append((e["question"], ok, dt, answer))
        print(f"\n=== {name} ({model}) ===")
        for q, ok, dt, a in rows:
            print(f"[{'PASS' if ok else 'FAIL'}] {dt:5.2f}s  {q}" + ("" if ok else f"\n        -> {a[:160]!r}"))
        summary[name] = (passed, len(entries), statistics.mean(times), statistics.median(times), max(times))
    print("\n| model | correct | mean s | median s | max s |\n|---|---|---|---|---|")
    for n, (p, t, mean, med, mx) in summary.items():
        print(f"| {n} | {p}/{t} ({p / t:.0%}) | {mean:.2f} | {med:.2f} | {mx:.2f} |")


if __name__ == "__main__":
    main()
