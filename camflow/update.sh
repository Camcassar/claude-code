#!/usr/bin/env bash
# One-command CamFlow update: stop running copies, pull the latest code,
# rebuild the app, and relaunch. Run from the CamFlow folder:  ./update.sh
set -euo pipefail
cd "$(dirname "$0")"

echo "Stopping any running CamFlow…"
pkill -f "python -m camflow" 2>/dev/null || true
sleep 1

# Pull the latest code from the source checkout (~/claude-bot-6) if present,
# then copy it into this folder. If there's no source checkout, just rebuild
# from whatever is already here.
SRC="$HOME/claude-bot-6/camflow"
if [ -d "$SRC" ] && [ "$SRC" != "$(pwd)" ]; then
    echo "Pulling latest code…"
    git -C "$HOME/claude-bot-6" pull --ff-only || echo "(could not pull — using current code)"
    cp -R "$SRC/." .
fi

echo "Rebuilding app…"
./make_app.sh --install >/dev/null

echo "Launching…"
open /Applications/CamFlow.app
echo "Done — CamFlow updated and relaunched. Look for 🎤 in the menu bar."
