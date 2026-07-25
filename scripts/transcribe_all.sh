#!/usr/bin/env bash
# Transcribe every phrase_NNN.wav in a directory, writing phrase_NNN.hyp.txt
# beside it, and report per-utterance latency.
#
# Optionally biases decoding with an ASR context prompt (Phase 2):
#   scripts/transcribe_all.sh <dir> --prompt-file runtime/tmp/asr_prompt.txt
#
# Local-only. No network.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

DIR="${1:?usage: transcribe_all.sh <dir> [--prompt-file <file>]}"
shift || true

PASSTHRU=()
while [ $# -gt 0 ]; do
    PASSTHRU+=("$1")
    shift
done

shopt -s nullglob
wavs=("$DIR"/phrase_*.wav)
shopt -u nullglob

[ ${#wavs[@]} -gt 0 ] || { echo "error: no phrase_*.wav in $DIR" >&2; exit 2; }

latencies=()
for w in "${wavs[@]}"; do
    start=$(date +%s.%N)
    # ${a[@]+"${a[@]}"} guards empty-array expansion under `set -u` on bash 3.2,
    # which is what macOS still ships as /bin/bash.
    "$REPO_ROOT/scripts/transcribe_local.sh" "$w" ${PASSTHRU[@]+"${PASSTHRU[@]}"} \
        > "${w%.wav}.hyp.txt" 2>/dev/null
    end=$(date +%s.%N)
    lat=$(echo "$end - $start" | bc)
    latencies+=("$lat")
    printf '%s  %6.2fs  %s\n' "$(basename "${w%.wav}")" "$lat" \
        "$(head -c 70 "${w%.wav}.hyp.txt")"
done

printf '\n%d utterance(s) transcribed.\n' "${#wavs[@]}"
printf '%s\n' "${latencies[@]}" | sort -n | awk '
    { a[NR] = $1 }
    END {
        med = (NR % 2) ? a[(NR+1)/2] : (a[NR/2] + a[NR/2+1]) / 2
        p95 = a[int(NR * 0.95) + (int(NR * 0.95) < NR ? 1 : 0)]
        printf "latency  median %.2fs   p95 %.2fs   max %.2fs\n", med, p95, a[NR]
    }'
