#!/usr/bin/env python3
"""Ask the local LM Studio model to draft one git or psql command for this repo.

Part of the project workflow in CLAUDE.md → "Delegating commands to the local model".
Claude checks which model is actually loaded (`lms ps`) before using this, since
`/v1/models` lists available-but-unloaded models too, and loading a new one can fail
with "insufficient system resources" if others already fill memory.

Usage:
    python3 .claude/tools/delegate.py "<task in plain English>" [model-id]

Prints exactly one command to stdout (or a line starting with REFUSE: and why).
Does not execute anything — the caller (Claude) runs the returned command.
"""
import json
import sys
import urllib.request

BASE_URL = "http://localhost:1234/v1"
DEFAULT_MODEL = "qwen3-30b-a3b"  # project's primary local model (DEC-29); override with argv[2]
                                  # if a different one is actually loaded (`lms ps`)

SYSTEM = """You draft exactly one command for the prototypeRAG git repository.

Rules:
- Output ONLY the command itself. No explanation, no markdown fences, no commentary.
- Database commands must be runnable as:
  docker compose exec -T postgres psql -U app -d app -c "<your SQL>"
  The schema: businesses, business_profile, pages, chunks, custom_replies,
  conversations, messages. Every tenant table has business_id.
- Git commands are plain `git ...` invocations, run from the repo root.
- If the task is destructive or ambiguous (e.g. deletes data/history without a clear
  target), output a line starting with "REFUSE:" followed by the reason, instead of a
  command.
- Never include `git push`, `git reset --hard`, `DROP`, or `TRUNCATE` unless the task
  explicitly names that exact operation.
"""


def main() -> None:
    if len(sys.argv) < 2:
        print("usage: delegate.py \"<task>\" [model-id]", file=sys.stderr)
        raise SystemExit(2)
    task = sys.argv[1]
    model = sys.argv[2] if len(sys.argv) > 2 else DEFAULT_MODEL

    body = json.dumps({
        "model": model,
        "messages": [
            {"role": "system", "content": SYSTEM},
            {"role": "user", "content": task},
        ],
        "temperature": 0,
    }).encode()
    req = urllib.request.Request(
        f"{BASE_URL}/chat/completions", data=body,
        headers={"Content-Type": "application/json"}, method="POST",
    )
    with urllib.request.urlopen(req, timeout=60) as resp:
        data = json.load(resp)
    print(data["choices"][0]["message"]["content"].strip())


if __name__ == "__main__":
    main()
