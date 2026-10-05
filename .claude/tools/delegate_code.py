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
    uv run python .claude/tools/delegate_code.py <spec_file> [model-id] [--web]

`--web` drafts one self-contained HTML page (inline CSS/JS) instead of a Python module.

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


SYSTEM_WEB = """You write one self-contained web page for the prototypeRAG project (a
multi-business RAG voice/chat assistant), served as a static file by FastAPI.

Rules:
- Output ONLY the file content, starting with <!doctype html>. No markdown fences, no
  commentary before or after.
- Plain HTML + inline <style> + inline <script>; no frameworks, no build step, no
  external scripts, fonts or images.
- Implement every behaviour in the spec exactly; keep code readable with short comments
  where the logic isn't obvious.
"""


def main() -> None:
    args = [a for a in sys.argv[1:] if a != "--web"]
    web = "--web" in sys.argv
    if not args:
        print("usage: delegate_code.py <spec_file> [model-id] [--web]", file=sys.stderr)
        raise SystemExit(2)
    spec = open(args[0]).read()
    model = args[1] if len(args) > 1 else DEFAULT_MODEL

    client = OpenAI(base_url=BASE_URL, api_key="lm-studio")
    resp = client.chat.completions.create(
        model=model,
        messages=[{"role": "system", "content": SYSTEM_WEB if web else SYSTEM}, {"role": "user", "content": spec}],
        temperature=0,
    )
    print(resp.choices[0].message.content)


if __name__ == "__main__":
    main()
