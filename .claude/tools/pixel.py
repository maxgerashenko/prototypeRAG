"""Show the models loaded in LM Studio as agents in Pixel Agents (if it's running).

Pixel Agents builds its characters from Claude Code hook events, and its hook script
forwards any event JSON on stdin to every live Pixel Agents server. Each loaded LLM gets
one fixed session id, so it sits in the office like a Claude session does:

- `sync()` (also `python3 pixel.py sync`, run by the SessionStart hook): every loaded
  LLM gets SessionStart + Stop (present, idle); every downloaded but unloaded model gets
  SessionEnd (leaves the office).
- `agent(model, task)` around a delegate call: PreToolUse ("<model>: <task>") on entry,
  so the character works; Stop on exit, so it goes idle again; then `sync()`, so a model
  loaded or unloaded meanwhile shows up or leaves. Parallel calls on one model share its
  character; it goes idle when the first of them ends.

Best effort only: if Pixel Agents or `lms` isn't installed or no server is up, nothing
happens and the delegate call works as before.
"""
import contextlib
import json
import os
import subprocess
import sys

HOOK = os.path.expanduser("~/.pixel-agents/hooks/claude-hook.js")
REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _session(model: str) -> dict:
    # cwd = repo root, so the character counts as part of this workspace
    return {"session_id": f"local-{model.split('/')[-1]}", "cwd": REPO}


def _send(model: str, event: str, **fields) -> None:
    try:
        subprocess.run(["node", HOOK], text=True, capture_output=True, timeout=3,
                       input=json.dumps({**_session(model), "hook_event_name": event, **fields}))
    except Exception:
        pass


def _lms(cmd: str) -> list[dict]:
    try:
        out = subprocess.run(["lms", cmd, "--json"], capture_output=True, text=True, timeout=10)
        return json.loads(out.stdout)
    except Exception:
        return []


def sync() -> None:
    if not os.path.exists(HOOK):
        return
    loaded = {m["identifier"] for m in _lms("ps") if m.get("type") == "llm"}
    for model in loaded:
        _send(model, "SessionStart", source="startup")
        _send(model, "Stop")
    for m in _lms("ls"):
        if m.get("modelKey") not in loaded:
            _send(m["modelKey"], "SessionEnd", reason="other")


@contextlib.contextmanager
def agent(model: str, task: str):
    if not os.path.exists(HOOK):
        yield
        return
    _send(model, "SessionStart", source="resume")
    _send(model, "PreToolUse", tool_name="Bash",
          tool_input={"command": f"{model.split('/')[-1]}: {task[:80]}"})
    try:
        yield
    finally:
        _send(model, "Stop")
        sync()


if __name__ == "__main__" and sys.argv[1:] == ["sync"]:
    sync()
