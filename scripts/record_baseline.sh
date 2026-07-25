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
# Usage:
#   scripts/record_baseline.sh <phrases.txt> <output_dir> [device]
#   scripts/record_baseline.sh --check [device]      # validate mic, no recording
#   scripts/record_baseline.sh --list                # list input devices
#
# <device> may be a name fragment ("MacBook Pro") or an index. Prefer a name:
# AVFoundation indices are NOT stable -- they renumber whenever an input is
# connected or removed, so a Bluetooth headset disconnecting silently changes
# which device index 2 refers to.

set -uo pipefail

DEFAULT_DEVICE_NAME="MacBook Pro Microphone"

# ---------------------------------------------------------------- diagnostics

# Guarantee this script never dies without saying why. Earlier versions could
# exit without explanation, which is far worse than failing loudly.
_expected_exit=0
on_exit() {
    local code=$?
    if [ "$code" -ne 0 ] && [ "$_expected_exit" -ne 1 ]; then
        echo >&2
        echo "error: aborted unexpectedly (exit $code) at line ${BASH_LINENO[0]}." >&2
        echo "Please report this output." >&2
    fi
}
trap on_exit EXIT

die() { _expected_exit=1; echo "error: $*" >&2; exit 2; }

# ------------------------------------------------------------------- devices

list_devices() {
    ffmpeg -f avfoundation -list_devices true -i "" 2>&1 \
        | sed -n '/AVFoundation audio devices/,$p' \
        | grep -oE '^\[AVFoundation indev @ [^]]*\] \[[0-9]+\] .*' \
        | sed -E 's/^\[AVFoundation indev @ [^]]*\] //'
}

# Resolve a name fragment or index to a current device index, printed on
# stdout. Returns non-zero on failure.
#
# This function MUST NOT call exit. It is invoked inside $(...), which runs in
# a subshell, so an exit here would terminate only that subshell and let the
# caller continue with an empty result -- reporting success while nothing was
# resolved. Callers must check the return status.
resolve_device() {
    local want="$1" devices index name
    devices="$(list_devices)"
    if [ -z "$devices" ]; then
        echo "error: no audio input devices found" >&2
        return 1
    fi

    if [[ "$want" =~ ^[0-9]+$ ]]; then
        # An index was given. Verify it currently exists rather than trusting it.
        if ! printf '%s\n' "$devices" | grep -q "^\[$want\]"; then
            echo "error: no audio input device at index $want." >&2
            echo "Current devices:" >&2
            printf '%s\n' "$devices" | sed 's/^/    /' >&2
            echo >&2
            echo "Indices change when devices connect or disconnect." >&2
            echo "Pass a name instead, e.g.: 'MacBook Pro'" >&2
            return 1
        fi
        printf '%s' "$want"
        return 0
    fi

    # Case-insensitive substring match on the device name.
    while IFS= read -r line; do
        index="$(printf '%s' "$line" | sed -E 's/^\[([0-9]+)\].*/\1/')"
        name="$(printf '%s' "$line" | sed -E 's/^\[[0-9]+\] //')"
        if printf '%s' "$name" | grep -qi -- "$want"; then
            printf '%s' "$index"
            return 0
        fi
    done <<< "$devices"

    echo "error: no audio input device matching '$want'." >&2
    echo "Current devices:" >&2
    printf '%s\n' "$devices" | sed 's/^/    /' >&2
    return 1
}

device_name_for() {
    list_devices | grep "^\[$1\]" | sed -E 's/^\[[0-9]+\] //'
}

# Argument order matters. -nostdin and -loglevel are global; -f avfoundation
# selects the input format and must precede -i. The rate/channel/codec flags
# are OUTPUT options and must follow -i -- placed before it they are read as
# input options, and avfoundation has no sample_rate option, failing with
# "Option sample_rate not found".
#
# -nostdin also stops ffmpeg consuming the stdin used for prompts.
FF_GLOBAL=(-nostdin -loglevel error -f avfoundation)
FF_OUTPUT=(-ar 16000 -ac 1 -c:a pcm_s16le)

duration_of() {
    ffprobe -v error -show_entries format=duration -of default=nw=1:nk=1 "$1" \
        2>/dev/null || echo 0
}

# Prove the device actually captures audio before asking anyone to read 33
# phrases at it. Cheap, and it converts a confusing mid-run failure into a
# clear one up front.
preflight() {
    local index="$1" tmp errlog dur
    tmp="$(mktemp -t miccheck).wav"
    errlog="$(mktemp -t micerr)"

    ffmpeg "${FF_GLOBAL[@]}" -i ":${index}" "${FF_OUTPUT[@]}" -t 1 -y "$tmp" \
        2>"$errlog"
    local rc=$?
    dur="$(duration_of "$tmp")"

    # Like resolve_device, this runs inside $(...) and must signal failure by
    # return status, never by exit.
    if [ $rc -ne 0 ] || ! awk "BEGIN{exit !($dur > 0.2)}"; then
        echo "error: could not capture from device $index." >&2
        echo >&2
        echo "ffmpeg said:" >&2
        sed 's/^/    /' "$errlog" >&2
        echo >&2
        echo "Check:" >&2
        echo "  - the device still exists (scripts/record_baseline.sh --list)" >&2
        echo "  - Terminal has microphone access" >&2
        echo "    (System Settings > Privacy & Security > Microphone)" >&2
        rm -f "$tmp" "$errlog"
        return 1
    fi

    rm -f "$tmp" "$errlog"
    printf '%s' "$dur"
    return 0
}

# ---------------------------------------------------------------- subcommands

case "${1:-}" in
    --list)
        command -v ffmpeg >/dev/null || die "ffmpeg not found"
        echo "Audio input devices:"
        list_devices | sed 's/^/    /'
        _expected_exit=1
        exit 0
        ;;
    --check)
        command -v ffmpeg >/dev/null || die "ffmpeg not found"
        want="${2:-$DEFAULT_DEVICE_NAME}"
        echo "requested : $want"
        if ! idx="$(resolve_device "$want")"; then _expected_exit=1; exit 2; fi
        echo "resolved  : index $idx  (\"$(device_name_for "$idx")\")"
        printf 'capturing 1s test... '
        if ! dur="$(preflight "$idx")"; then _expected_exit=1; exit 3; fi
        echo "OK (${dur}s, 16 kHz mono)"
        echo
        echo "Microphone is working. Ready to record."
        _expected_exit=1
        exit 0
        ;;
esac

# --------------------------------------------------------------------- setup

PHRASES="${1:?usage: record_baseline.sh <phrases.txt> <output_dir> [device]}"
OUTDIR="${2:?usage: record_baseline.sh <phrases.txt> <output_dir> [device]}"
DEVICE_WANT="${3:-$DEFAULT_DEVICE_NAME}"

command -v ffmpeg >/dev/null || die "ffmpeg not found"
command -v ffprobe >/dev/null || die "ffprobe not found"
[ -f "$PHRASES" ] || die "no such file: $PHRASES"

# Open the controlling terminal on fd 3 and read every prompt from it, never
# from stdin. Reading prompts from stdin while the phrase file was redirected
# into the loop is what previously made this script consume phrases as
# keystrokes instead of waiting for the user.
if ! exec 3< /dev/tty 2>/dev/null; then
    echo "error: no controlling terminal." >&2
    echo "This script is interactive -- run it directly in a Terminal window." >&2
    _expected_exit=1
    exit 2
fi

if ! DEVICE="$(resolve_device "$DEVICE_WANT")"; then _expected_exit=1; exit 2; fi
DEVICE_NAME="$(device_name_for "$DEVICE")"

# Load every phrase before the interactive loop, so stdin is never redirected
# across it. bash 3.2 (what macOS ships) has no mapfile, hence the read loop.
phrases=()
while IFS= read -r line || [ -n "$line" ]; do
    case "$line" in ''|\#*) continue ;; esac
    phrases+=("$line")
done < "$PHRASES"
[ ${#phrases[@]} -gt 0 ] || die "no phrases in $PHRASES"

mkdir -p "$OUTDIR"

printf 'Checking microphone... '
if ! preflight "$DEVICE" >/dev/null; then _expected_exit=1; exit 3; fi
echo "OK"

cat <<BANNER

--------------------------------------------------------------------
  Real-voice baseline recording

  device  : [$DEVICE] $DEVICE_NAME
  phrases : ${#phrases[@]}
  output  : $OUTDIR

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

# ---------------------------------------------------------------- record loop

recorded=0
skipped=0
i=0
errlog="$OUTDIR/.ffmpeg.err"

for line in "${phrases[@]}"; do
    i=$((i + 1))
    id=$(printf "%03d" "$i")
    wav="$OUTDIR/phrase_${id}.wav"

    while true; do
        echo
        echo "[$id/${#phrases[@]}] Read aloud:"
        echo
        echo "    $line"
        echo
        printf 'Enter to start recording (or "s" to skip): '
        if ! read -r key <&3; then
            echo
            die "terminal closed"
        fi
        if [ "$key" = "s" ]; then
            skipped=$((skipped + 1))
            break
        fi

        rm -f "$wav"
        ffmpeg "${FF_GLOBAL[@]}" -i ":${DEVICE}" "${FF_OUTPUT[@]}" -y "$wav" \
            2>"$errlog" &
        ff_pid=$!

        # Let the device open before the user speaks.
        sleep 0.5

        # Treat "no growing output file" as failure rather than probing the
        # process. A live ffmpeg that is not writing is just as useless, and
        # process-liveness checks have proven unreliable here.
        if [ ! -s "$wav" ]; then
            kill -INT "$ff_pid" 2>/dev/null
            wait "$ff_pid" 2>/dev/null
            echo
            echo "error: recording produced no data. ffmpeg said:" >&2
            echo >&2
            sed 's/^/    /' "$errlog" >&2
            echo >&2
            echo "Run: scripts/record_baseline.sh --check" >&2
            _expected_exit=1
            exit 3
        fi

        echo "  RECORDING... press Enter to stop."
        if ! read -r _ <&3; then
            kill -INT "$ff_pid" 2>/dev/null
            wait "$ff_pid" 2>/dev/null
            echo
            die "terminal closed"
        fi

        # SIGINT lets ffmpeg finalise the WAV header; SIGKILL would truncate it.
        kill -INT "$ff_pid" 2>/dev/null
        wait "$ff_pid" 2>/dev/null

        dur="$(duration_of "$wav")"
        echo "  captured ${dur}s -> $(basename "$wav")"

        # No corpus phrase can be spoken in under a second. A take this short
        # means the prompt was skipped, not that the speaker was quick.
        if awk "BEGIN{exit !($dur < 1.0)}"; then
            echo "  WARNING: ${dur}s is too short to contain this phrase."
            echo "           Retry, and check the prompt waited for your keypress."
        fi

        printf '  [k]eep / [r]etry / [s]kip: '
        if ! read -r choice <&3; then
            echo
            die "terminal closed"
        fi
        case "$choice" in
            r|R) continue ;;
            s|S) rm -f "$wav"; skipped=$((skipped + 1)); break ;;
            *)   printf '%s\n' "$line" > "$OUTDIR/phrase_${id}.ref.txt"
                 recorded=$((recorded + 1)); break ;;
        esac
    done
done

rm -f "$errlog"

echo
echo "--------------------------------------------------------------------"
echo "  recorded: $recorded    skipped: $skipped"
echo "  output:   $OUTDIR"
echo
echo "  Next:"
echo "    scripts/transcribe_all.sh $OUTDIR"
echo "    .venv/bin/python scripts/score_transcripts.py $OUTDIR"
echo "--------------------------------------------------------------------"

_expected_exit=1
