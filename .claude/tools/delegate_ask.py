#!/usr/bin/env python3
"""Ask the local LM Studio model a trivial question about some text: summarize it, find
something in it, or check a result. Review and planning stay with Claude.

Part of the project workflow in CLAUDE.md → "Delegating to the local model".

Usage:
    python3 .claude/tools/delegate_ask.py "<question>" [file ...] [--model ID]
    <command> 2>&1 | python3 .claude/tools/delegate_ask.py "<question>"

Input is the given files (each under a `=== path ===` header) and/or stdin. Prints the
model's short answer. Answers are hints, not proof: Claude verifies anything a commit,
delete, push or decision depends on.

Thinking is off (`reasoning_effort: none`): gemma-4-12b otherwise spends ~8 s thinking
before the first word (DECISIONS.md, knowledge table), and these tasks don't need it.
"""
import json
import sys
import urllib.request

BASE_URL = "http://localhost:1234/v1"
DEFAULT_MODEL = "google/gemma-4-12b"  # chat model, normally loaded (DEC-32); check `lms ps`
MAX_CHARS = 60_000  # keeps prompt processing to seconds; longer input is cut with a note

SYSTEM = """You help a coding agent with trivial tasks on the prototypeRAG repository:
summarize text, find something in it, or check whether a result is OK.

Rules:
- Answer only from the input. If it isn't there, say "NOT FOUND".
- Be brief: a few lines at most, no preamble.
- Finding: quote the exact matching line(s) with file name and line number if known.
- Checking a result (tests, command output): first line "PASS" or "FAIL", then one line
  why, quoting the decisive line (e.g. the pytest summary or the error).
- Summarizing: plain bullet points, keep numbers, names and error messages exact.
- If the input was cut off, say so when it could change the answer.
"""


def main() -> None:
    args = sys.argv[1:]
    model = DEFAULT_MODEL
    if "--model" in args:
        i = args.index("--model")
        model = args[i + 1]
        del args[i:i + 2]
    if not args:
        print('usage: delegate_ask.py "<question>" [file ...] [--model ID]', file=sys.stderr)
        raise SystemExit(2)
    question, paths = args[0], args[1:]

    parts = []
    for path in paths:
        with open(path, encoding="utf-8", errors="replace") as f:
            parts.append(f"=== {path} ===\n{f.read()}")
    if not sys.stdin.isatty():
        stdin = sys.stdin.read()
        if stdin.strip():
            parts.append(f"=== stdin ===\n{stdin}")
    text = "\n\n".join(parts)
    if len(text) > MAX_CHARS:
        text = text[:MAX_CHARS] + f"\n\n[input cut at {MAX_CHARS} of {len(text)} characters]"

    body = json.dumps({
        "model": model,
        "messages": [
            {"role": "system", "content": SYSTEM},
            {"role": "user", "content": f"Question: {question}\n\nInput:\n{text or '(none)'}"},
        ],
        "temperature": 0,
        "reasoning_effort": "none",
    }).encode()
    req = urllib.request.Request(
        f"{BASE_URL}/chat/completions", data=body,
        headers={"Content-Type": "application/json"}, method="POST",
    )
    with urllib.request.urlopen(req, timeout=120) as resp:
        data = json.load(resp)
    print((data["choices"][0]["message"]["content"] or "").strip())


if __name__ == "__main__":
    main()
