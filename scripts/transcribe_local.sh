#!/usr/bin/env bash
# Local-only transcription via whisper.cpp.
#
# Accepts any audio file, converts it to 16 kHz mono PCM in a temporary
# directory, transcribes it, prints the transcript to stdout, and deletes the
# temporary audio (CLAUDE.md §3.1).
#
# No network access is used or required. There is no cloud fallback.
#
# Usage:
#   scripts/transcribe_local.sh <audio-file> [--prompt-file <file>] [--model <path>]

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
WHISPER_BIN="${WHISPER_BIN:-$REPO_ROOT/whisper.cpp/build/bin/whisper-cli}"
MODEL="${WHISPER_MODEL:-$REPO_ROOT/whisper.cpp/models/ggml-large-v3-turbo.bin}"
TMPROOT="${TMPROOT:-$REPO_ROOT/runtime/tmp}"

AUDIO=""
PROMPT_FILE=""

while [ $# -gt 0 ]; do
    case "$1" in
        --prompt-file) PROMPT_FILE="$2"; shift 2 ;;
        --model)       MODEL="$2";       shift 2 ;;
        -h|--help)     sed -n '2,12p' "$0"; exit 0 ;;
        *)             AUDIO="$1";       shift ;;
    esac
done

[ -n "$AUDIO" ]        || { echo "error: no audio file given" >&2; exit 2; }
[ -f "$AUDIO" ]        || { echo "error: no such file: $AUDIO" >&2; exit 2; }
[ -x "$WHISPER_BIN" ]  || { echo "error: whisper-cli not built at $WHISPER_BIN" >&2; exit 3; }
[ -f "$MODEL" ]        || { echo "error: model not found at $MODEL" >&2; exit 3; }

mkdir -p "$TMPROOT"
WORKDIR="$(mktemp -d "$TMPROOT/asr.XXXXXX")"

# Delete the working directory on every exit path, including error and signal.
# Temporary audio must not survive the process (CLAUDE.md §3.1).
cleanup() { rm -rf "$WORKDIR"; }
trap cleanup EXIT INT TERM HUP

WAV="$WORKDIR/input.wav"

# afconvert ships with macOS and avoids a dependency on a working ffmpeg.
# It is a no-op re-encode when the input is already 16 kHz mono.
if ! afconvert -f WAVE -d LEI16@16000 -c 1 "$AUDIO" "$WAV" 2>"$WORKDIR/convert.err"; then
    echo "error: audio conversion failed" >&2
    cat "$WORKDIR/convert.err" >&2
    exit 4
fi

ARGS=(-m "$MODEL" -f "$WAV" -l en -nt -np --output-txt --output-file "$WORKDIR/out")

# Whisper's initial prompt biases decoding toward supplied terminology.
# The prompt must contain vocabulary only -- never patient information.
if [ -n "$PROMPT_FILE" ]; then
    [ -f "$PROMPT_FILE" ] || { echo "error: no such prompt file: $PROMPT_FILE" >&2; exit 2; }
    ARGS+=(--prompt "$(cat "$PROMPT_FILE")")
fi

# stderr carries whisper's diagnostics; keep it off stdout so stdout is the
# transcript alone. Diagnostics are timing/config only and contain no transcript.
"$WHISPER_BIN" "${ARGS[@]}" >/dev/null 2>"$WORKDIR/whisper.err" || {
    echo "error: transcription failed" >&2
    tail -20 "$WORKDIR/whisper.err" >&2
    exit 5
}

if [ ! -f "$WORKDIR/out.txt" ]; then
    echo "error: transcription produced no output" >&2
    exit 5
fi

# Trim leading/trailing whitespace that whisper.cpp emits around segments.
sed -e 's/^[[:space:]]*//' -e 's/[[:space:]]*$//' "$WORKDIR/out.txt" | sed '/^$/d'
