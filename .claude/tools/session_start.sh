#!/bin/bash
# SessionStart hook (.claude/settings.json): start the local helpers that aren't running yet.
# Runs async and always exits 0 — a helper that fails to start never blocks a session.
export PATH="/opt/homebrew/bin:$HOME/.lmstudio/bin:$PATH"
cd "$(dirname "$0")/../.." || exit 0
logs="${TMPDIR:-/tmp}"

# ngrok tunnel to the app on :8000 at PUBLIC_BASE_URL from .env (Twilio webhooks, iPhone).
# Public URL, and the app has no login — anyone with the URL can use it while this runs.
url=$(grep -E '^PUBLIC_BASE_URL=' .env 2>/dev/null | cut -d= -f2- | tr -d '"')
if [ -n "$url" ] && ! pgrep -x ngrok >/dev/null; then
  nohup ngrok http 8000 --url="$url" --log=stdout >"$logs/ngrok.log" 2>&1 </dev/null & disown
fi

# Pixel Agents office on :3100, then the loaded LM Studio models as agents in it
if ! lsof -nP -iTCP:3100 -sTCP:LISTEN >/dev/null 2>&1; then
  nohup pixel-agents --host 0.0.0.0 --port 3100 --no-terminal >"$logs/pixel-agents.log" 2>&1 </dev/null & disown
  sleep 3
fi
python3 .claude/tools/pixel.py sync >/dev/null 2>&1
exit 0
