"""Show the models loaded in LM Studio as agents in Pixel Agents (if it's running).

Pixel Agents builds its characters from Claude Code hook events, and its hook script
forwards any event JSON on stdin to every live Pixel Agents server. Each loaded LLM gets
one fixed session id and a label (Local_QWEN, Local_GEMMA), so it sits in the office like
a Claude session does:

- `sync()` (also `python3 pixel.py sync`, run by the SessionStart hook): every loaded
  LLM gets SessionStart + Stop (present, idle); every downloaded but unloaded model gets
  SessionEnd (leaves the office).
- `agent(model, task)` around a delegate call: PreToolUse ("<model>: <task>") on entry,
  so the character works; Stop on exit, so it goes idle again; then `sync()`, so a model
  loaded or unloaded meanwhile shows up or leaves. Parallel calls on one model share its
  character; it goes idle only when the last of them ends. Each running call leaves a
  file named after its pid in the model's `.calls/` folder, which is how a call in another
  process (or a `sync()`) knows the character is still working.

Best effort only: if Pixel Agents or `lms` isn't installed or no server is up, nothing
happens and the delegate call works as before.
"""
import contextlib
import json
import os
import re
import subprocess
import sys

HOOK = os.path.expanduser("~/.pixel-agents/hooks/claude-hook.js")
# Pixel Agents labels a hooks-only character with the basename of its cwd, so each model
# reports a folder of its own: "Local_QWEN", "Local_GEMMA". The folder just has to exist.
# Only adopted while Settings -> Watch All Sessions is on (folders outside the project
# are otherwise ignored).
OFFICE = os.path.expanduser("~/.pixel-agents/office")


def _label(model: str) -> str:
    family = re.match(r"[a-z]+", model.split("/")[-1].lower())
    return "Local_" + (family.group(0) if family else model.split("/")[-1]).upper()


def _session(model: str) -> dict:
    label = _label(model)
    return {"session_id": f"local-{label}", "cwd": os.path.join(OFFICE, label)}


def _send(model: str, event: str, **fields) -> None:
    try:
        if event != "SessionEnd":  # models that are only leaving don't need a folder
            os.makedirs(_session(model)["cwd"], exist_ok=True)
        subprocess.run(["node", HOOK], text=True, capture_output=True, timeout=3,
                       input=json.dumps({**_session(model), "hook_event_name": event, **fields}))
    except Exception:
        pass


def _busy(model: str) -> bool:
    """True while any delegate call (in any process) is running on this model's character."""
    calls = os.path.join(_session(model)["cwd"], ".calls")
    try:
        names = os.listdir(calls)
    except OSError:
        return False
    busy = False
    for name in names:
        try:
            os.kill(int(name), 0)
            busy = True
        except ProcessLookupError:
            os.remove(os.path.join(calls, name))  # that call died without cleaning up
        except Exception:
            busy = busy or name.isdigit()
    return busy


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
        if _busy(model):  # a Stop now would show a working model as idle
            continue
        _send(model, "SessionStart", source="startup")
        _send(model, "Stop")
    # several downloaded models share a label (qwen3.6, qwen3-coder, ...): only end the
    # labels with nothing loaded, or an unloaded sibling would remove the loaded one
    loaded_labels = {_label(m) for m in loaded}
    for m in _lms("ls"):
        if _label(m["modelKey"]) not in loaded_labels:
            _send(m["modelKey"], "SessionEnd", reason="other")


@contextlib.contextmanager
def agent(model: str, task: str):
    if not os.path.exists(HOOK):
        yield
        return
    _send(model, "SessionStart", source="resume")
    _send(model, "PreToolUse", tool_name="Bash",
          tool_input={"command": f"{model.split('/')[-1]}: {task[:80]}"})
    mine = os.path.join(_session(model)["cwd"], ".calls", str(os.getpid()))
    try:
        os.makedirs(os.path.dirname(mine), exist_ok=True)
        open(mine, "w").close()
    except OSError:
        pass
    try:
        yield
    finally:
        try:
            os.remove(mine)
        except OSError:
            pass
        if not _busy(model):  # a parallel call on this model keeps it working
            _send(model, "Stop")
        sync()


if __name__ == "__main__" and sys.argv[1:] == ["sync"]:
    sync()
