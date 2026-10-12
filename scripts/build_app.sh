#!/usr/bin/env bash
# Build WhisperN.app -- a Dock-launchable wrapper around scripts/whispern_app.py.
#
# The bundle is a launcher, not a copy: it runs the GUI out of this repo, so
# edits to the Python take effect on the next launch with no rebuild. Nothing
# is vendored, nothing is downloaded, and the build needs no network.
#
# WHY A BUNDLE AT ALL, beyond not keeping a terminal open. macOS attributes
# privacy permissions to the *application*, so running the GUI from a terminal
# grants Microphone and Automation to Terminal itself -- to
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
    <string>WhisperN pauses music players while you record and resumes them afterwards. It never sends text or keystrokes to other applications.</string>
    <key>NSDocumentsFolderUsageDescription</key>
    <string>WhisperN runs from a folder in Documents: its code, speech model, and temporary audio live there. Nothing is uploaded.</string>
    <key>NSSpeechRecognitionUsageDescription</key>
    <string>WhisperN does not use Apple's speech recognition. Transcription runs locally through whisper.cpp.</string>
</dict>
</plist>
PLIST
echo "  plist     ${BUNDLE_ID} ${VERSION}"

# ---------------------------------------------------------------- launcher
# A compiled launcher, not a shell script. macOS guards ~/Documents per app
# (TCC), and a bundle whose executable is a shell script is refused access
# silently -- no prompt, the app just exits -- so a repo under ~/Documents
# could never be granted. A native executable gets the normal "would like to
# access files in your Documents folder" prompt, scoped to Documents only.
#
# It execs Python rather than spawning it, so the window, Dock icon, and
# privacy grants all stay with WhisperN.app.
#
# stderr goes to a file because a bundled app has nowhere to print: without it
# a Python traceback vanishes and the icon just bounces. Python tracebacks
# carry file paths and exception text, never transcript content, and runtime/
# is excluded from git (CLAUDE.md 3.1). Truncated each launch so nothing
# accumulates.
LAUNCHER_SRC="$(mktemp -t whispern-launcher).swift"
trap 'rm -f "$LAUNCHER_SRC"' EXIT
cat > "$LAUNCHER_SRC" <<SWIFT
import Foundation

let repo = "${REPO_ROOT}"
let fm = FileManager.default

// First touch of the repo: this is where macOS asks for Documents access.
guard fm.changeCurrentDirectoryPath(repo) else {
    NSLog("WhisperN: cannot open %@ -- check Privacy & Security > Files and Folders", repo)
    exit(1)
}
try? fm.createDirectory(atPath: repo + "/runtime/tmp", withIntermediateDirectories: true)
let fd = open(repo + "/runtime/tmp/app-stderr.log", O_WRONLY | O_CREAT | O_TRUNC, 0o644)
if fd >= 0 { dup2(fd, 2); close(fd) }

// Apps launched from the Dock get PATH=/usr/bin:/bin:/usr/sbin:/sbin, which
// hides Homebrew. Recording shells out to ffmpeg, so without this the app
// finds no microphone at all.
setenv("PATH", "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin", 1)

let python = repo + "/.venv/bin/python"
var argv: [UnsafeMutablePointer<CChar>?] = [
    strdup(python), strdup(repo + "/scripts/whispern_app.py"), nil,
]
execv(python, &argv)
NSLog("WhisperN: exec %@ failed: %s", python, strerror(errno))
exit(1)
SWIFT
swiftc -O -o "$APP/Contents/MacOS/WhisperN" "$LAUNCHER_SRC"
echo "  launcher  -> $REPO_ROOT"

# ---------------------------------------------------------------- sign
# Signed with the local identity from scripts/setup_signing.sh when present.
# macOS privacy grants attach to the designated requirement: with that
# identity it is the bundle id plus the certificate hash, stable across
# rebuilds; ad hoc it is the build's cdhash, so every rebuild drops the
# grants. Ad hoc remains the fallback so a fresh checkout still builds.
SIGN_ID="WhisperN Local Signing"
if security find-certificate -c "$SIGN_ID" >/dev/null 2>&1; then
    codesign --force --deep --sign "$SIGN_ID" "$APP"
    echo "  signed    $SIGN_ID"
else
    codesign --force --deep --sign - "$APP" 2>/dev/null \
        && echo "  signed    ad-hoc (run scripts/setup_signing.sh so grants survive rebuilds)" \
        || echo "  WARNING: ad-hoc signing failed; permissions may reset on rebuild"
fi
echo "  requires  $(codesign -dr - "$APP" 2>&1 | sed -n 's/^designated => //p')"

if [ "$INSTALL" = "1" ]; then
    # Replacing a running app leaves it in a broken state, so refuse instead.
    # The launcher execs Python, so a running app shows up as the script.
    if pgrep -f "WhisperN.app/Contents/MacOS/WhisperN|$REPO_ROOT/scripts/whispern_app.py" >/dev/null 2>&1; then
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
