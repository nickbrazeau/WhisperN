#!/usr/bin/env bash
# Record a real-voice baseline by reading phrases aloud.
#
# For each phrase: press Enter to start recording, read it aloud, press Enter
# to stop. Review the result, then keep it or re-record.
#
# Produces <outdir>/phrase_NNN.wav and phrase_NNN.ref.txt, the same layout
# scripts/make_synthetic_audio.sh produces, so the same scorer works on both.
#
# Everything is local. The recording never leaves the machine. Output goes
# under runtime/ which is gitignored -- do not move these files into the repo,
# and do not dictate patient information into them.
#
# Usage: scripts/record_baseline.sh <phrases.txt> <output_dir> [device_index]

set -uo pipefail

PHRASES="${1:?usage: record_baseline.sh <phrases.txt> <output_dir> [device_index]}"
OUTDIR="${2:?usage: record_baseline.sh <phrases.txt> <output_dir> [device_index]}"
DEVICE="${3:-}"

command -v ffmpeg >/dev/null || { echo "error: ffmpeg not found" >&2; exit 1; }
[ -f "$PHRASES" ] || { echo "error: no such file: $PHRASES" >&2; exit 2; }

if [ -z "$DEVICE" ]; then
    echo "Available audio input devices:"
    echo
    ffmpeg -f avfoundation -list_devices true -i "" 2>&1 \
        | sed -n '/audio devices/,/^\[in#/p' \
        | grep -oE '\[[0-9]+\] .*' || true
    echo
    echo "Use the BUILT-IN microphone, not a Bluetooth headset."
    echo "Bluetooth input is band-limited and measurably degrades medical terms."
    echo
    printf 'Device index: '
    read -r DEVICE
fi

mkdir -p "$OUTDIR"

cat <<'BANNER'

--------------------------------------------------------------------
  Real-voice baseline recording

  For each phrase:
    Enter  -> start recording
    Enter  -> stop recording
    then   -> [k]eep, [r]etry, or [s]kip

  Speak at your natural dictation pace. Do NOT over-enunciate --
  the point is to measure how the system handles how you actually
  talk, not your best reading voice.

  Ctrl-C to abort at any time.
--------------------------------------------------------------------

BANNER

# ffmpeg must not consume the stdin we are reading prompts from.
FFMPEG_COMMON=(-nostdin -loglevel error -f avfoundation -ar 16000 -ac 1 -c:a pcm_s16le)

recorded=0
skipped=0
i=0

while IFS= read -r line || [ -n "$line" ]; do
    case "$line" in ''|\#*) continue ;; esac
    i=$((i + 1))
    id=$(printf "%03d" "$i")
    wav="$OUTDIR/phrase_${id}.wav"

    while true; do
        echo
        echo "[$id] Read aloud:"
        echo
        echo "    $line"
        echo
        printf 'Enter to start recording (or "s" to skip): '
        read -r key
        if [ "$key" = "s" ]; then
            skipped=$((skipped + 1))
            break
        fi

        # -y overwrites a previous take of the same phrase.
        ffmpeg "${FFMPEG_COMMON[@]}" -y -i ":${DEVICE}" "$wav" &
        ff_pid=$!

        # Give ffmpeg a moment to open the device before the user speaks.
        sleep 0.4
        if ! kill -0 "$ff_pid" 2>/dev/null; then
            echo
            echo "error: ffmpeg exited immediately. Two usual causes:" >&2
            echo "  - wrong device index" >&2
            echo "  - microphone permission not granted to this terminal" >&2
            echo "    (System Settings > Privacy & Security > Microphone)" >&2
            exit 3
        fi

        echo "  RECORDING... press Enter to stop."
        read -r _

        # SIGINT lets ffmpeg finalise the WAV header cleanly; SIGKILL would not.
        kill -INT "$ff_pid" 2>/dev/null
        wait "$ff_pid" 2>/dev/null

        if [ ! -s "$wav" ]; then
            echo "  no audio captured -- retrying."
            continue
        fi

        dur=$(ffprobe -v error -show_entries format=duration \
                      -of default=nw=1:nk=1 "$wav" 2>/dev/null || echo "?")
        echo "  captured ${dur}s -> $(basename "$wav")"

        printf '  [k]eep / [r]etry / [s]kip: '
        read -r choice
        case "$choice" in
            r|R) continue ;;
            s|S) rm -f "$wav"; skipped=$((skipped + 1)); break ;;
            *)   printf '%s\n' "$line" > "$OUTDIR/phrase_${id}.ref.txt"
                 recorded=$((recorded + 1)); break ;;
        esac
    done
done < "$PHRASES"

echo
echo "--------------------------------------------------------------------"
echo "  recorded: $recorded    skipped: $skipped"
echo "  output:   $OUTDIR"
echo
echo "  Next:"
echo "    scripts/transcribe_all.sh $OUTDIR"
echo "    .venv/bin/python scripts/score_transcripts.py $OUTDIR"
echo "--------------------------------------------------------------------"
