#!/bin/bash
# Install a macOS LaunchAgent that starts Pixel Agents on :3100 at login and restarts it
# if it exits. Re-run after changing the flags below; `--uninstall` removes it.
# session_start.sh still starts it when it isn't running (other machines, agent removed).
set -euo pipefail
label=local.pixel-agents
plist="$HOME/Library/LaunchAgents/$label.plist"
domain="gui/$(id -u)"

launchctl bootout "$domain/$label" 2>/dev/null || true
if [ "${1:-}" = "--uninstall" ]; then
  rm -f "$plist"
  echo "removed $label"
  exit 0
fi

bin=$(command -v pixel-agents)
mkdir -p "$(dirname "$plist")"
cat >"$plist" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>$label</string>
  <key>ProgramArguments</key>
  <array>
    <string>$bin</string>
    <string>--host</string><string>0.0.0.0</string>
    <string>--port</string><string>3100</string>
    <string>--no-terminal</string>
  </array>
  <key>EnvironmentVariables</key>
  <dict><key>PATH</key><string>$(dirname "$bin"):/usr/bin:/bin</string></dict>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
  <key>StandardOutPath</key><string>$HOME/Library/Logs/pixel-agents.log</string>
  <key>StandardErrorPath</key><string>$HOME/Library/Logs/pixel-agents.log</string>
</dict>
</plist>
EOF

# a copy started by hand or by session_start.sh holds :3100 and would make launchd's copy
# crash-loop on the busy port
pkill -f "$bin" 2>/dev/null && sleep 1 || true
launchctl bootstrap "$domain" "$plist"
echo "installed $label -> $plist"
