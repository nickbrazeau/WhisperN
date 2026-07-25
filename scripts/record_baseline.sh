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

# Open the controlling terminal on fd 3 and read every prompt from it, never
# from stdin. This is what keeps the interactive prompts independent of any
# redirect: reading prompts from stdin while the phrase file was piped in is
# exactly the bug that made this script blow through phrases without waiting.
#
# Opening it here also fails fast. Testing `[ -r /dev/tty ]` is not enough --
# the device node can exist but not be readable under a non-interactive runner.
if ! exec 3< /dev/tty 2>/dev/null; then
    echo "error: no controlling terminal." >&2
    echo "This script is interactive -- run it directly in a Terminal window." >&2
    exit 2
fi

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
    read -r DEVICE <&3
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

# Argument order matters. -nostdin and -loglevel are global; -f avfoundation
# selects the input format and must precede -i. The rate/channel/codec flags
# are OUTPUT options and must come AFTER -i -- placed before it they are read
# as input options, and avfoundation has no sample_rate option, which fails
# with "Option sample_rate not found".
#
# -nostdin is essential: without it ffmpeg consumes the stdin this script is
# reading its prompts from.
FF_GLOBAL=(-nostdin -loglevel error -f avfoundation)
FF_OUTPUT=(-ar 16000 -ac 1 -c:a pcm_s16le)

# Load every phrase into memory BEFORE the interactive loop.
#
# Do not stream the phrase file into the loop with `done < "$PHRASES"`. That
# redirects stdin for the whole loop body, so the interactive `read` calls
# consume phrase lines instead of keystrokes: the script never waits for the
# user and silently eats several phrases per iteration.
#
# bash 3.2 (what macOS ships) has no mapfile/readarray, hence the read loop.
phrases=()
while IFS= read -r line || [ -n "$line" ]; do
    case "$line" in ''|\#*) continue ;; esac
    phrases+=("$line")
done < "$PHRASES"

[ ${#phrases[@]} -gt 0 ] || { echo "error: no phrases in $PHRASES" >&2; exit 2; }

echo "  ${#phrases[@]} phrases to record."

recorded=0
skipped=0
i=0

for line in "${phrases[@]}"; do
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
        read -r key <&3
        if [ "$key" = "s" ]; then
            skipped=$((skipped + 1))
            break
        fi

        # -y overwrites a previous take of the same phrase.
        errlog="$OUTDIR/.ffmpeg.err"
        ffmpeg "${FF_GLOBAL[@]}" -i ":${DEVICE}" "${FF_OUTPUT[@]}" -y "$wav" \
            2>"$errlog" &
        ff_pid=$!

        # Give ffmpeg a moment to open the device before the user speaks.
        sleep 0.4
        if ! kill -0 "$ff_pid" 2>/dev/null; then
            echo
            echo "error: ffmpeg exited immediately. Its output was:" >&2
            echo >&2
            sed 's/^/    /' "$errlog" >&2
            echo >&2
            echo "Common causes:" >&2
            echo "  - wrong device index (re-run without the index to list devices)" >&2
            echo "  - microphone permission not granted to this terminal" >&2
            echo "    (System Settings > Privacy & Security > Microphone)" >&2
            exit 3
        fi

        echo "  RECORDING... press Enter to stop."
        read -r _ <&3

        # SIGINT lets ffmpeg finalise the WAV header cleanly; SIGKILL would not.
        kill -INT "$ff_pid" 2>/dev/null
        wait "$ff_pid" 2>/dev/null

        if [ ! -s "$wav" ]; then
            echo "  no audio captured -- retrying."
            continue
        fi

        dur=$(ffprobe -v error -show_entries format=duration \
                      -of default=nw=1:nk=1 "$wav" 2>/dev/null || echo "0")
        echo "  captured ${dur}s -> $(basename "$wav")"

        # A take far shorter than the phrase means something swallowed the
        # prompt rather than that the user spoke quickly. Say so rather than
        # letting an unusable file be kept.
        if awk "BEGIN{exit !($dur < 1.0)}" 2>/dev/null; then
            echo "  WARNING: only ${dur}s captured -- too short to contain this phrase."
            echo "           Retry, and check the prompt waited for your keypress."
        fi

        printf '  [k]eep / [r]etry / [s]kip: '
        read -r choice <&3
        case "$choice" in
            r|R) continue ;;
            s|S) rm -f "$wav"; skipped=$((skipped + 1)); break ;;
            *)   printf '%s\n' "$line" > "$OUTDIR/phrase_${id}.ref.txt"
                 recorded=$((recorded + 1)); break ;;
        esac
    done
done

rm -f "$OUTDIR/.ffmpeg.err"

echo
echo "--------------------------------------------------------------------"
echo "  recorded: $recorded    skipped: $skipped"
echo "  output:   $OUTDIR"
echo
echo "  Next:"
echo "    scripts/transcribe_all.sh $OUTDIR"
echo "    .venv/bin/python scripts/score_transcripts.py $OUTDIR"
echo "--------------------------------------------------------------------"
