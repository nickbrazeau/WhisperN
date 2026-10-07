#!/usr/bin/env bash
# Run Ollama as a per-user login service, locked to this machine.
#
# Used by rewrite mode (ADR 0002). Installs a LaunchAgent rather than using
# `brew services`, because the Homebrew service cannot carry the environment
# below, and these settings are the privacy controls, not tuning:
#
#   OLLAMA_HOST=127.0.0.1:11434   loopback only; never 0.0.0.0 or a LAN address
#   OLLAMA_NO_CLOUD=1             disables Ollama's remote inference and web
#                                 search, so a "-cloud" model name cannot route
#                                 a transcript off the machine (the polisher
#                                 refuses such names as well)
#   OLLAMA_KEEP_ALIVE=30m         keeps the model loaded between dictations
#
# The server log goes to ~/Library/Logs/whispern-ollama.log. At Ollama's
# default level it records request lines and model loads, not prompt text;
# scripts/offline_smoke_test.sh checks that empirically. Never set
# OLLAMA_DEBUG here: debug logging can include prompts (CLAUDE.md 3.1).
#
# Needs no network. Pulling a model (`ollama pull qwen2.5:14b`) does, once.
#
# Usage:
#   scripts/setup_ollama.sh            # install and start
#   scripts/setup_ollama.sh --remove   # stop and uninstall the service

set -euo pipefail

LABEL="local.whispern.ollama"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
LOG="$HOME/Library/Logs/whispern-ollama.log"
OLLAMA="$(command -v ollama || true)"

if [ "${1:-}" = "--remove" ]; then
    launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
    rm -f "$PLIST"
    echo "removed $LABEL"
    exit 0
fi

[ -n "$OLLAMA" ] || { echo "error: ollama not installed (brew install ollama)" >&2; exit 1; }

# A second server on the same port would leave it unclear which one, with
# which settings, is answering.
if brew services list 2>/dev/null | grep -q '^ollama .*started'; then
    echo "error: the Homebrew ollama service is running; brew services stop ollama" >&2
    exit 1
fi

mkdir -p "$HOME/Library/LaunchAgents" "$HOME/Library/Logs"
cat > "$PLIST" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>             <string>${LABEL}</string>
    <key>ProgramArguments</key>
    <array>
        <string>${OLLAMA}</string>
        <string>serve</string>
    </array>
    <key>EnvironmentVariables</key>
    <dict>
        <key>OLLAMA_HOST</key>       <string>127.0.0.1:11434</string>
        <key>OLLAMA_NO_CLOUD</key>   <string>1</string>
        <key>OLLAMA_KEEP_ALIVE</key> <string>30m</string>
    </dict>
    <key>RunAtLoad</key>         <true/>
    <key>KeepAlive</key>         <true/>
    <key>StandardOutPath</key>   <string>${LOG}</string>
    <key>StandardErrorPath</key> <string>${LOG}</string>
</dict>
</plist>
PLIST

launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
launchctl bootstrap "gui/$(id -u)" "$PLIST"

# Verify the binding rather than trusting the setting.
for _ in $(seq 1 20); do
    lsof -nP -iTCP:11434 -sTCP:LISTEN >/dev/null 2>&1 && break
    sleep 0.5
done
listen="$(lsof -nP -iTCP:11434 -sTCP:LISTEN 2>/dev/null | awk 'NR>1 {print $9}' | sort -u)"
if [ -z "$listen" ]; then
    echo "error: ollama is not listening on 11434; see $LOG" >&2
    exit 1
fi
if echo "$listen" | grep -qv '^127\.0\.0\.1:11434$'; then
    echo "error: ollama is listening beyond loopback: $listen" >&2
    exit 1
fi
echo "ollama listening on $listen only (cloud features disabled)"
