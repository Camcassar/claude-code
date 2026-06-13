#!/usr/bin/env bash
# Build CamFlow.app — a double-clickable menu bar app you can keep in the
# CamFlow folder, add to Login Items, or launch from Spotlight.
#
# Usage:  ./make_app.sh            (creates ./CamFlow.app)
#         ./make_app.sh --install  (also copies it to /Applications)
set -euo pipefail
cd "$(dirname "$0")"

APP="CamFlow.app"
rm -rf "$APP"
mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources"

# Generate the CC bubble icon (macOS only; falls back to the default icon).
if [ "$(uname)" = "Darwin" ] && [ ! -f CamFlow.icns ]; then
    if [ ! -d .venv ]; then
        python3 -m venv .venv
        .venv/bin/pip install --quiet --upgrade pip
        .venv/bin/pip install --quiet -r requirements.txt
        touch .venv/.deps-installed
    fi
    .venv/bin/python make_icon.py || echo "icon generation failed — using default icon"
fi
[ -f CamFlow.icns ] && cp CamFlow.icns "$APP/Contents/Resources/CamFlow.icns"

cat > "$APP/Contents/Info.plist" <<'PLIST'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>CFBundleName</key>               <string>CamFlow</string>
    <key>CFBundleDisplayName</key>        <string>CamFlow</string>
    <key>CFBundleIdentifier</key>         <string>com.camcassar.camflow</string>
    <key>CFBundleExecutable</key>         <string>CamFlow</string>
    <key>CFBundleIconFile</key>           <string>CamFlow</string>
    <key>CFBundlePackageType</key>        <string>APPL</string>
    <key>CFBundleShortVersionString</key> <string>0.2.0</string>
    <key>LSUIElement</key>                <true/>
    <key>NSHighResolutionCapable</key>    <true/>
    <key>NSMicrophoneUsageDescription</key>
    <string>CamFlow records your voice while you hold the dictation hotkey.</string>
</dict>
</plist>
PLIST

cat > "$APP/Contents/MacOS/CamFlow" <<'LAUNCHER'
#!/usr/bin/env bash
# Find the CamFlow folder: next to this app bundle, or at ~/CamFlow.
HERE="$(cd "$(dirname "$0")" && pwd)"
LOG="$HOME/.camflow/camflow.log"
mkdir -p "$HOME/.camflow"

# Finder launches apps with a stripped-down PATH that omits Homebrew and
# conda; add the common locations so python3 is found.
export PATH="/opt/homebrew/bin:/usr/local/bin:/Library/Frameworks/Python.framework/Versions/Current/bin:$HOME/anaconda3/bin:$HOME/miniconda3/bin:$PATH"

alert() {
    /usr/bin/osascript -e "display alert \"CamFlow couldn't start\" message \"$1\"" >/dev/null 2>&1
}

RUNDIR=""
for DIR in "$(cd "$HERE/../../.." 2>/dev/null && pwd)" "$HOME/CamFlow"; do
    if [ -x "$DIR/run.sh" ]; then RUNDIR="$DIR"; break; fi
done
if [ -z "$RUNDIR" ]; then
    alert "Could not find the CamFlow folder. Keep it at ~/CamFlow (or keep CamFlow.app inside it)."
    exit 1
fi

echo "=== CamFlow launched $(date) from $RUNDIR ===" >> "$LOG"
# Don't exec — so we can report failure with an alert.
"$RUNDIR/run.sh" >> "$LOG" 2>&1
CODE=$?
if [ "$CODE" -ne 0 ]; then
    TAIL="$(tail -n 8 "$LOG" 2>/dev/null | tr '"' "'" | tr '\n' ' ')"
    alert "Exited with code $CODE. Recent log: $TAIL  —  Full log: ~/.camflow/camflow.log"
    exit "$CODE"
fi
LAUNCHER
chmod +x "$APP/Contents/MacOS/CamFlow"

echo "Built $APP"

if [ "${1:-}" = "--install" ]; then
    rm -rf "/Applications/$APP"
    cp -R "$APP" /Applications/
    echo "Installed to /Applications/$APP"
fi

echo
echo "Next steps:"
echo "  1. Double-click CamFlow.app (first time: right-click → Open)"
echo "  2. Grant Microphone / Input Monitoring / Accessibility to 'CamFlow'"
echo "     when macOS asks (System Settings → Privacy & Security)"
echo "  3. Optional: System Settings → General → Login Items → add CamFlow"
echo
echo "Logs (when launched as an app): ~/.camflow/camflow.log"
