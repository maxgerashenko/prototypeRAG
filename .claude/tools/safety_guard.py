#!/usr/bin/env python3
"""PreToolUse safety hook (stdlib only, nothing to install).

Blocks, with exit code 2 and a reason on stderr (Claude sees it and must ask the user):
  - `alembic downgrade` in any Bash command;
  - writes to `.env` / `.env.*` (not `.env.example`): Write/Edit/MultiEdit/NotebookEdit on the file, and Bash
    that redirects into it or runs tee/sed -i/cp/mv/rm/truncate/dd/perl -i on it;
  - destructive SQL in a Bash command that talks to a database (psql, docker/compose exec): DROP,
    TRUNCATE, DELETE or UPDATE without WHERE, ALTER ... DROP, DROP OWNED.
Anything else passes. A real need goes through the user: they run it, or remove this hook entry.
"""

import json
import re
import sys

ENV_NAME = r"(?:^|[/\s'\"=])\.env(?!\.example)(?:\.[\w.-]+)?(?=$|[\s'\";|&)])"
ENV_WRITE = re.compile(
    r"(?:>>?\s*['\"]?[^\s;|&]*" + ENV_NAME[len(r"(?:^|[/\s'\"=])"):] + r")"          # echo x > .env
    r"|(?:\b(?:tee|sed\s+-[a-z]*i|perl\s+-[a-z]*i|cp|mv|rm|truncate|dd|install|ln)\b[^\n;|&]*" + ENV_NAME + r")",
    re.IGNORECASE,
)
DB_COMMAND = re.compile(r"\bpsql\b|\bpg_restore\b|\bdocker(?:-compose)?\b[^\n]*\bexec\b|\bdocker\s+compose\b[^\n]*\bexec\b", re.I)
DESTRUCTIVE_SQL = [
    (re.compile(r"\bdrop\s+(?:table|database|schema|index|view|type|extension|owned|role|user|policy|trigger|function)\b", re.I), "DROP"),
    (re.compile(r"\btruncate\b", re.I), "TRUNCATE"),
    (re.compile(r"\balter\s+table\b[^;]*\bdrop\b", re.I), "ALTER TABLE ... DROP"),
]
NO_WHERE = [
    (re.compile(r"\bdelete\s+from\s+[\w.\"]+(?![^;]*\bwhere\b)[^;]*(?:;|$|['\"])", re.I), "DELETE without WHERE"),
    (re.compile(r"\bupdate\s+[\w.\"]+\s+set\b(?![^;]*\bwhere\b)[^;]*(?:;|$|['\"])", re.I), "UPDATE without WHERE"),
]


def is_env_path(path: str) -> bool:
    name = path.replace("\\", "/").rsplit("/", 1)[-1]
    return (name == ".env" or name.startswith(".env.")) and name != ".env.example"


def check_bash(cmd: str) -> str | None:
    if re.search(r"\balembic\b[^\n;|&]*\bdowngrade\b", cmd, re.I):
        return "`alembic downgrade` is blocked: it can drop data. Ask the user to run it."
    if ENV_WRITE.search(cmd):
        return "Writing to .env is blocked: it holds secrets. Ask the user to change it (and never print it)."
    if DB_COMMAND.search(cmd):
        for pattern, label in DESTRUCTIVE_SQL + NO_WHERE:
            if pattern.search(cmd):
                return f"Destructive SQL is blocked ({label}). Ask the user, or use a WHERE clause / a test database."
    return None


def main() -> int:
    try:
        event = json.load(sys.stdin)
    except Exception:
        return 0  # never break the session because of unreadable input
    tool, args = event.get("tool_name", ""), event.get("tool_input") or {}
    reason = None
    if tool == "Bash":
        reason = check_bash(args.get("command", "") or "")
    elif tool in ("Write", "Edit", "MultiEdit", "NotebookEdit"):
        path = args.get("file_path") or args.get("notebook_path") or ""
        if is_env_path(path):
            reason = "Writing to .env is blocked: it holds secrets. Ask the user to change it (and never print it)."
    if reason:
        print(f"safety_guard: {reason}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
