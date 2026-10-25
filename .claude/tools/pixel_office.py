"""Back up and restore the Pixel Agents office (layout, areas, area mappings) in git.

Pixel Agents keeps its settings in ~/.pixel-agents/, outside the repo, so they exist only
on this Mac. `save` copies them into .claude/pixel-agents/; `restore` puts them back
(new machine, deleted folder) and restarts the LaunchAgent so the running server picks
them up -- it keeps the layout in memory and would otherwise overwrite the restored file.

    python3 .claude/tools/pixel_office.py save      # office -> .claude/pixel-agents/
    python3 .claude/tools/pixel_office.py restore   # .claude/pixel-agents/ -> office

What is saved:
- layout.json: the whole file (floor, furniture, pets, areas and which tiles they cover).
- config.json -> office.json: only the office-related keys of the "standalone" block
  (areaMappings, showAreas, alwaysShowLabels, watchAllSessions, showHostDirectories) and
  "directories". Sound, consent and hook settings stay machine-local; restore merges the
  saved keys into the existing config and leaves the rest alone.
Not saved: standalone-state.json (which characters exist and their seats) -- the
characters are re-created by `pixel.py sync` and the seats are assigned again.
"""
import json
import os
import shutil
import subprocess
import sys

HOME = os.path.expanduser(os.environ.get("PIXEL_AGENTS_HOME", "~/.pixel-agents"))
REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
SAVED = os.path.join(REPO, ".claude", "pixel-agents")
STANDALONE_KEYS = ["areaMappings", "showAreas", "alwaysShowLabels", "watchAllSessions", "showHostDirectories"]


def _read(path: str) -> dict:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _write(path: str, data: dict) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
        f.write("\n")


def save() -> None:
    config = _read(os.path.join(HOME, "config.json"))
    standalone = config.get("standalone", {})
    _write(os.path.join(SAVED, "office.json"), {
        "standalone": {k: standalone[k] for k in STANDALONE_KEYS if k in standalone},
        "directories": config.get("directories", []),
    })
    # re-serialised, not copied, so a re-save without changes is a no-op diff
    _write(os.path.join(SAVED, "layout.json"), _read(os.path.join(HOME, "layout.json")))
    print(f"saved to {SAVED}")


def restore() -> None:
    office = _read(os.path.join(SAVED, "office.json"))
    config_path = os.path.join(HOME, "config.json")
    config = _read(config_path) if os.path.exists(config_path) else {}
    config.setdefault("standalone", {}).update(office["standalone"])
    config["directories"] = office["directories"]
    os.makedirs(HOME, exist_ok=True)
    _write(config_path, config)
    shutil.copyfile(os.path.join(SAVED, "layout.json"), os.path.join(HOME, "layout.json"))
    print(f"restored into {HOME}")
    if HOME == os.path.expanduser("~/.pixel-agents"):
        subprocess.run(["launchctl", "kickstart", "-k", f"gui/{os.getuid()}/local.pixel-agents"],
                       capture_output=True)
        print("restarted local.pixel-agents (if installed); open the office again")


if __name__ == "__main__" and sys.argv[1:] in (["save"], ["restore"]):
    {"save": save, "restore": restore}[sys.argv[1]]()
elif __name__ == "__main__":
    print(__doc__)
    raise SystemExit(2)
