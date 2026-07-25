#!/usr/bin/env bash
# Generate synthetic dictation audio from text phrases using the macOS
# offline speech synthesiser. No network, no human voice, no patient data.
#
# CLAUDE.md §3.1 requires synthetic dictation for all automated tests.
#
# IMPORTANT CAVEAT: synthesised speech is far cleaner than real dictation --
# no disfluency, no accent, no room noise, no Bluetooth codec loss. Results
# here are an optimistic bound and a regression harness, NOT a substitute for
# a baseline measured on the user's own voice. See docs/baseline-errors.md.
#
# Usage: scripts/make_synthetic_audio.sh <phrases.txt> <output_dir> [voice]

set -euo pipefail

PHRASES="${1:?usage: make_synthetic_audio.sh <phrases.txt> <output_dir> [voice]}"
OUTDIR="${2:?usage: make_synthetic_audio.sh <phrases.txt> <output_dir> [voice]}"
VOICE="${3:-Samantha}"

command -v say >/dev/null || { echo "error: 'say' not found (macOS only)" >&2; exit 1; }
command -v afconvert >/dev/null || { echo "error: 'afconvert' not found" >&2; exit 1; }

mkdir -p "$OUTDIR"

i=0
while IFS= read -r line || [ -n "$line" ]; do
    case "$line" in ''|\#*) continue ;; esac
    i=$((i + 1))
    id=$(printf "%03d" "$i")

    aiff="$OUTDIR/phrase_${id}.aiff"
    wav="$OUTDIR/phrase_${id}.wav"

    # Offline synthesis. -v selects a locally installed voice.
    say -v "$VOICE" -o "$aiff" "$line"

    # 16 kHz mono signed 16-bit little-endian PCM, as whisper.cpp expects.
    # afconvert ships with macOS, so this path does not depend on ffmpeg.
    afconvert -f WAVE -d LEI16@16000 -c 1 "$aiff" "$wav"
    rm -f "$aiff"

    # Reference text alongside the audio, for scoring.
    printf '%s\n' "$line" > "$OUTDIR/phrase_${id}.ref.txt"

    echo "generated $wav"
done < "$PHRASES"

echo "done: $i phrase(s) in $OUTDIR (voice: $VOICE)"
