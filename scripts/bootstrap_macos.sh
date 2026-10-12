#!/usr/bin/env bash
# Set up WhisperN on a new Mac. Run once from the repo root:
#
#   scripts/bootstrap_macos.sh                 # transcription only
#   scripts/bootstrap_macos.sh --with-rewrite  # also the local rewrite model
#
# NOT FOR CLINICAL USE OR CLINICAL TEXT. Email and research dictation only.
#
# This is the only step that needs the network: it installs Homebrew packages,
# clones whisper.cpp at a pinned commit, and downloads the speech models. After
# it finishes, dictation runs with the network off and sends nothing anywhere.
#
# Safe to re-run: every step skips work that is already done.
#
# It does not install Homebrew itself (that is a curl-to-shell install you
# should run knowingly), does not touch the keychain, and does not install the
# app. The steps for those are printed at the end.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO_ROOT"

WITH_REWRITE=0
[ "${1:-}" = "--with-rewrite" ] && WITH_REWRITE=1

# whisper.cpp commit and model checksums this repo was validated against.
WHISPER_REPO="https://github.com/ggml-org/whisper.cpp.git"
WHISPER_COMMIT="080bbbe85230f624f0b52127f1ae1218247989f9"
ASR_MODEL="large-v3-turbo"
ASR_SHA256="1fc70f774d38eb169993ac391eea357ef47c88757ef72ee5943879b7e8e2bc69"
VAD_MODEL="silero-v5.1.2"
VAD_SHA256="29940d98d42b91fbd05ce489f3ecf7c72f0a42f027e4875919a28fb4c04ea2cf"
REWRITE_MODEL="qwen2.5:14b"
PY_VERSION="3.13"

step() { printf '\n\033[1m==> %s\033[0m\n' "$*"; }
die()  { printf '\033[31merror:\033[0m %s\n' "$*" >&2; exit 1; }

check_sha() {  # file expected
    local got
    got="$(shasum -a 256 "$1" | awk '{print $1}')"
    [ "$got" = "$2" ] || die "$1: checksum $got does not match the validated $2"
}

# ---------------------------------------------------------------- machine
step "Checking this Mac"
[ "$(uname -s)" = "Darwin" ] || die "WhisperN runs on macOS only."
if [ "$(uname -m)" != "arm64" ]; then
    echo "warning: only tested on Apple Silicon; an Intel Mac will be slow."
fi
command -v brew >/dev/null || die "Homebrew is required. Install it from https://brew.sh, then re-run."
command -v git >/dev/null  || die "git is required: xcode-select --install"

# ---------------------------------------------------------------- packages
step "Installing cmake, ffmpeg, and Python $PY_VERSION with Tk"
brew install cmake ffmpeg "python@$PY_VERSION" "python-tk@$PY_VERSION"
PY="$(brew --prefix "python@$PY_VERSION")/bin/python$PY_VERSION"
"$PY" -c "import tkinter" 2>/dev/null || die "$PY has no working tkinter."

# ---------------------------------------------------------------- python
step "Creating .venv"
[ -x .venv/bin/python ] || "$PY" -m venv .venv
.venv/bin/python -m pip install --quiet --upgrade pip
.venv/bin/python -m pip install --quiet -r requirements-dev.txt

# ---------------------------------------------------------------- whisper.cpp
step "Building whisper.cpp at ${WHISPER_COMMIT:0:12}"
if [ ! -d whisper.cpp/.git ]; then
    git clone "$WHISPER_REPO" whisper.cpp
fi
git -C whisper.cpp fetch --quiet origin "$WHISPER_COMMIT" 2>/dev/null || true
git -C whisper.cpp checkout --quiet "$WHISPER_COMMIT"
if [ ! -x whisper.cpp/build/bin/whisper-cli ] || [ ! -x whisper.cpp/build/bin/whisper-vad-speech-segments ]; then
    cmake -S whisper.cpp -B whisper.cpp/build -DCMAKE_BUILD_TYPE=Release
    cmake --build whisper.cpp/build -j --config Release \
        --target whisper-cli whisper-vad-speech-segments
fi

# ---------------------------------------------------------------- models
step "Downloading the speech model ($ASR_MODEL, ~1.5 GB) and the speech detector"
ASR_FILE="whisper.cpp/models/ggml-$ASR_MODEL.bin"
VAD_FILE="whisper.cpp/models/ggml-$VAD_MODEL.bin"
[ -f "$ASR_FILE" ] || sh whisper.cpp/models/download-ggml-model.sh "$ASR_MODEL"
[ -f "$VAD_FILE" ] || sh whisper.cpp/models/download-vad-model.sh "$VAD_MODEL"
check_sha "$ASR_FILE" "$ASR_SHA256"
check_sha "$VAD_FILE" "$VAD_SHA256"

# ---------------------------------------------------------------- prompt
step "Building the vocabulary prompt"
mkdir -p runtime/tmp runtime/audit
.venv/bin/python scripts/build_asr_prompt.py \
    --mode infectious_diseases --out runtime/tmp/asr_prompt_id.txt

# ---------------------------------------------------------------- rewrite
if [ "$WITH_REWRITE" = 1 ]; then
    step "Installing the local rewrite model ($REWRITE_MODEL, ~9 GB)"
    brew install ollama
    echo "This repo was validated on Ollama 0.40.0; installed: $(ollama --version 2>&1 | tail -1)"
    scripts/setup_ollama.sh          # loopback-only service, cloud features off
    ollama pull "$REWRITE_MODEL"
fi

# ---------------------------------------------------------------- verify
step "Running the test suite"
.venv/bin/python -m pytest tests/ -q

cat <<EOF

Done. Next steps:

  1. scripts/setup_signing.sh      # optional: keeps macOS permissions across rebuilds
  2. scripts/build_app.sh --install
  3. open -a WhisperN              # allow Microphone and Documents when asked

Optional check, with the network off:  scripts/offline_smoke_test.sh

NOT FOR CLINICAL USE OR CLINICAL TEXT. Read everything before you copy it:
drug and organism names are wrong roughly one time in three.
EOF
[ "$WITH_REWRITE" = 1 ] || echo "Rewrite mode is off. To add it later: scripts/bootstrap_macos.sh --with-rewrite"
