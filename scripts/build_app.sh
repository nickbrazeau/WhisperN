#!/usr/bin/env bash
# Build WhisperN.app -- a Dock-launchable wrapper around scripts/whispern_app.py.
#
# The bundle is a launcher, not a copy: it runs the GUI out of this repo, so
# edits to the Python take effect on the next launch with no rebuild. Nothing
# is vendored, nothing is downloaded, and the build needs no network.
#
# WHY A BUNDLE AT ALL, beyond not keeping a terminal open. macOS attributes
# privacy permissions to the *application*, so running the GUI from a terminal
# grants Microphone, Accessibility, and Automation to Terminal itself -- to
# every command anyone ever runs there. A bundle with a stable identifier owns
# those grants alone, which is both less alarming in System Settings and a
# tighter blast radius.
#
# Rebuild after moving the repo: the launcher hardcodes the path resolved here.
#
# Usage:
#   scripts/build_app.sh                 # build into dist/
#   scripts/build_app.sh --install       # also copy into /Applications

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

INSTALL=0
[ "${1:-}" = "--install" ] && INSTALL=1

APP="dist/WhisperN.app"
PYTHON="$REPO_ROOT/.venv/bin/python"
BUNDLE_ID="local.whispern"
VERSION="1.0.0"

# Fail early and loudly: a bundle that builds but cannot start is worse than a
# build error, because the failure surfaces as a Dock icon that bounces once.
[ -x "$PYTHON" ] || { echo "error: no interpreter at $PYTHON -- create the venv first" >&2; exit 1; }
[ -f "scripts/whispern_app.py" ] || { echo "error: scripts/whispern_app.py missing" >&2; exit 1; }
"$PYTHON" -c "import tkinter" 2>/dev/null || { echo "error: $PYTHON has no tkinter; the GUI cannot run" >&2; exit 1; }

echo "building $APP"
rm -rf "$APP" dist/WhisperN.iconset
mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources"

# ---------------------------------------------------------------- icon
swift scripts/make_icon.swift dist/WhisperN.iconset >/dev/null
iconutil -c icns dist/WhisperN.iconset -o "$APP/Contents/Resources/AppIcon.icns"
rm -rf dist/WhisperN.iconset
echo "  icon      AppIcon.icns"

# ---------------------------------------------------------------- Info.plist
# The three usage strings are load-bearing, not boilerplate. macOS kills the
# process outright when it requests one of these without a declared purpose,
# and the text shown is what the user is asked to consent to.
cat > "$APP/Contents/Info.plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>CFBundleName</key>                  <string>WhisperN</string>
    <key>CFBundleDisplayName</key>           <string>WhisperN</string>
    <key>CFBundleExecutable</key>            <string>WhisperN</string>
    <key>CFBundleIdentifier</key>            <string>${BUNDLE_ID}</string>
    <key>CFBundleIconFile</key>              <string>AppIcon</string>
    <key>CFBundlePackageType</key>           <string>APPL</string>
    <key>CFBundleShortVersionString</key>    <string>${VERSION}</string>
    <key>CFBundleVersion</key>               <string>${VERSION}</string>
    <key>CFBundleInfoDictionaryVersion</key> <string>6.0</string>
    <key>LSMinimumSystemVersion</key>        <string>13.0</string>
    <key>LSApplicationCategoryType</key>     <string>public.app-category.medical</string>
    <key>NSHighResolutionCapable</key>       <true/>
    <key>NSMicrophoneUsageDescription</key>
    <string>WhisperN records your dictation and transcribes it on this Mac. Audio is never uploaded and is deleted immediately after transcription.</string>
    <key>NSAppleEventsUsageDescription</key>
    <string>WhisperN needs to know which application you are typing into so it can paste your dictation there, and nowhere else.</string>
    <key>NSSpeechRecognitionUsageDescription</key>
    <string>WhisperN does not use Apple's speech recognition. Transcription runs locally through whisper.cpp.</string>
</dict>
</plist>
PLIST
echo "  plist     ${BUNDLE_ID} ${VERSION}"

# ---------------------------------------------------------------- launcher
# stderr goes to a file because a bundled app has nowhere to print: without it
# a Python traceback vanishes and the icon just bounces. Python tracebacks
# carry file paths and exception text, never transcript content, and runtime/
# is excluded from git (CLAUDE.md 3.1). Truncated each launch so nothing
# accumulates.
cat > "$APP/Contents/MacOS/WhisperN" <<LAUNCHER
#!/bin/sh
REPO="${REPO_ROOT}"
mkdir -p "\$REPO/runtime/tmp"
cd "\$REPO" || exit 1
exec "\$REPO/.venv/bin/python" "\$REPO/scripts/whispern_app.py" \\
     2> "\$REPO/runtime/tmp/app-stderr.log"
LAUNCHER
chmod +x "$APP/Contents/MacOS/WhisperN"
echo "  launcher  -> $REPO_ROOT"

# ---------------------------------------------------------------- sign
# Ad-hoc, because there is no Developer ID here and none is needed for a
# locally built tool. It still gives the bundle a stable code identity, which
# is what the privacy grants attach to -- without it, every rebuild can look
# like a different application and silently drop the permissions.
codesign --force --deep --sign - "$APP" 2>/dev/null \
    && echo "  signed    ad-hoc" \
    || echo "  WARNING: ad-hoc signing failed; permissions may reset on rebuild"

if [ "$INSTALL" = "1" ]; then
    # Replacing a running app leaves it in a broken state, so refuse instead.
    if pgrep -f "WhisperN.app/Contents/MacOS/WhisperN" >/dev/null 2>&1; then
        echo "error: WhisperN is running; quit it before --install" >&2
        exit 1
    fi
    rm -rf /Applications/WhisperN.app
    cp -R "$APP" /Applications/WhisperN.app
    echo "  installed /Applications/WhisperN.app"
fi

echo
echo "built: $APP"
echo "open it with:  open $APP"
echo "keep it in the Dock: launch it, then right-click the icon -> Options -> Keep in Dock"
