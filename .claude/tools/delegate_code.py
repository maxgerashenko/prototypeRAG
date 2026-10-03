#!/usr/bin/env python3
"""Ask the local LM Studio model to draft one Python module from a precise spec.

Part of the project workflow in CLAUDE.md → "Delegating whole-file code drafts".
Default model is qwen/qwen3.6-35b-a3b with thinking left ON (don't pass
enable_thinking=False) — validated head-to-head against qwen3-coder-next (no
thinking) on app/ingest/discover.py: the thinking-enabled run caught a same_domain()
bug (str.lstrip() used as if it stripped a prefix) that the non-thinking coder model
missed. Claude checks which model is actually loaded first (`lms ps`) and may need to
unload others to fit this one (~27 GB resident) — see CLAUDE.md for the memory-budget
note (DEC-29).

Usage:
    python3 .claude/tools/delegate_code.py <spec_file> [model-id]

Prints the drafted module's source to stdout (code only, no fences — though the model
doesn't always honor that; Claude strips fences before writing the file). The
`reasoning_content` field (if present) is discarded here; read it directly via the
openai client if you want to inspect the model's reasoning trace for a specific draft.
Does not write any file itself — the caller (Claude) reviews and writes the output.
"""
import sys

from openai import OpenAI

BASE_URL = "http://localhost:1234/v1"
DEFAULT_MODEL = "qwen/qwen3.6-35b-a3b"

SYSTEM = """You write one Python module for the prototypeRAG project (a multi-business
RAG assistant: crawl -> Postgres+pgvector -> chat/voice -> actions).

Rules:
- Output ONLY the Python source code. No markdown fences, no commentary before or after.
- Match the project's existing style: plain functions (not classes unless asked),
  type hints, concise docstrings, no framework beyond what's named in the spec.
- No LangChain/LlamaIndex. Use only the libraries named in the spec.
- Every business-scoped DB query must filter by business_id (multi-tenant rule).
"""


def main() -> None:
    if len(sys.argv) < 2:
        print("usage: delegate_code.py <spec_file> [model-id]", file=sys.stderr)
        raise SystemExit(2)
    spec = open(sys.argv[1]).read()
    model = sys.argv[2] if len(sys.argv) > 2 else DEFAULT_MODEL

    client = OpenAI(base_url=BASE_URL, api_key="lm-studio")
    resp = client.chat.completions.create(
        model=model,
        messages=[{"role": "system", "content": SYSTEM}, {"role": "user", "content": spec}],
        temperature=0,
    )
    print(resp.choices[0].message.content)


if __name__ == "__main__":
    main()
