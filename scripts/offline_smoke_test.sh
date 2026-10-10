#!/usr/bin/env bash
# Phase 7 — offline and privacy verification.
#
# CLAUDE.md §6 Phase 7: "Do not claim the system is offline-safe solely because
# the application settings say 'local.' Verify behavior empirically."
#
# Every check here therefore observes behaviour rather than reading config.
# Network denial is enforced by the kernel via sandbox-exec, not by unplugging
# a cable or trusting a flag, and the denial is itself proven before it is
# relied upon.
#
# Produces a redacted report: counts, statuses, and paths only. No transcript
# text is written to the report or to any log.
#
# Usage: scripts/offline_smoke_test.sh [--report FILE]

set -uo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

REPORT="runtime/audit/offline-test-$(date +%Y%m%d-%H%M%S).md"
while [ $# -gt 0 ]; do
    case "$1" in
        --report) REPORT="$2"; shift 2 ;;
        -h|--help) sed -n '2,16p' "$0"; exit 0 ;;
        *) echo "unknown argument: $1" >&2; exit 2 ;;
    esac
done

mkdir -p "$(dirname "$REPORT")" runtime/tmp

PASS=0; FAIL=0; WARN=0
SANDBOX="$(mktemp -t no-network).sb"
trap 'rm -f "$SANDBOX"' EXIT

RED=$'\033[31m'; GRN=$'\033[32m'; YEL=$'\033[33m'; DIM=$'\033[2m'; RST=$'\033[0m'

say()  { printf '%s\n' "$*"; printf '%s\n' "$*" >> "$REPORT"; }
ok()   { PASS=$((PASS+1)); printf '  %sPASS%s %s\n' "$GRN" "$RST" "$1"; echo "- **PASS** $1" >> "$REPORT"; }
bad()  { FAIL=$((FAIL+1)); printf '  %sFAIL%s %s\n' "$RED" "$RST" "$1"; echo "- **FAIL** $1" >> "$REPORT"; }
warn() { WARN=$((WARN+1)); printf '  %sWARN%s %s\n' "$YEL" "$RST" "$1"; echo "- **WARN** $1" >> "$REPORT"; }

{
  echo "# Offline and Privacy Verification"
  echo
  echo "- Generated: $(date -u '+%Y-%m-%dT%H:%M:%SZ')"
  echo "- Host architecture: $(uname -m), macOS $(sw_vers -productVersion)"
  echo
  echo "Redacted by construction: this report records check outcomes, counts,"
  echo "and paths. It contains no transcript text and no audio."
  echo
} > "$REPORT"

# --------------------------------------------------------------------------
say ""; say "## 1. Cloud-provider credentials"; say ""
# --------------------------------------------------------------------------
# Checked in the environment the pipeline actually inherits.
LEAKED=$(env | grep -Ei '^(OPENAI|ANTHROPIC|AZURE|GOOGLE_APPLICATION|GEMINI|DEEPGRAM|ASSEMBLYAI|GROQ|HF_TOKEN|REPLICATE|COHERE|MISTRAL|TOGETHER|FIREWORKS)[A-Z_]*=' | cut -d= -f1 || true)
if [ -z "$LEAKED" ]; then
    ok "no cloud-provider environment variables set"
else
    bad "cloud credentials present in environment: $(echo "$LEAKED" | tr '\n' ' ')"
fi

# Credential files are a separate leakage path from the environment.
CREDFILES=""
for f in ~/.netrc ~/.config/openai ~/.openai ~/.anthropic; do
    [ -e "$f" ] && CREDFILES="$CREDFILES $(basename "$f")"
done
if [ -z "$CREDFILES" ]; then
    ok "no provider credential files in the usual locations"
else
    warn "credential files present:$CREDFILES (not proof of use; review)"
fi

# --------------------------------------------------------------------------
say ""; say "## 2. Configured endpoints"; say ""
# --------------------------------------------------------------------------
ENDPOINTS=$(grep -hoE 'https?://[^ "]+' config/*.yaml 2>/dev/null | sort -u || true)
NONLOCAL=$(printf '%s\n' "$ENDPOINTS" | grep -vE '://(127\.|localhost|\[::1\])' | grep -v '^$' || true)
if [ -z "$NONLOCAL" ]; then
    ok "all configured endpoints are loopback ($(printf '%s' "$ENDPOINTS" | grep -c . ) found)"
else
    bad "non-loopback endpoint configured: $(echo "$NONLOCAL" | tr '\n' ' ')"
fi

# The code must refuse a non-loopback endpoint, not merely not have one.
if .venv/bin/python - <<'PY' >/dev/null 2>&1
import sys; sys.path.insert(0, "src")
from pipeline.polisher import LocalPolisher, LocalOnlyError
for bad_url in ("https://api.openai.com/v1", "http://192.168.1.50:11434",
                "http://0.0.0.0:11434", "http://example.com"):
    try:
        LocalPolisher(endpoint=bad_url)
    except LocalOnlyError:
        continue
    raise SystemExit(1)
raise SystemExit(0)
PY
then
    ok "polisher refuses cloud, LAN, 0.0.0.0, and hostname endpoints"
else
    bad "polisher accepted a non-loopback endpoint"
fi

# --------------------------------------------------------------------------
say ""; say "## 3. Network denial is real"; say ""
# --------------------------------------------------------------------------
cat > "$SANDBOX" <<'EOF'
(version 1)
(allow default)
(deny network*)
EOF

if sandbox-exec -f "$SANDBOX" /usr/bin/curl -s -m 5 https://example.com >/dev/null 2>&1; then
    bad "sandbox did NOT block network; all offline results below are void"
    SANDBOX_OK=0
else
    ok "sandbox profile verified: outbound network blocked inside it"
    SANDBOX_OK=1
fi

# --------------------------------------------------------------------------
say ""; say "## 4. Transcription with network denied"; say ""
# --------------------------------------------------------------------------
SAMPLE=runtime/tmp/offline_sample.wav
if [ ! -f "$SAMPLE" ]; then
    say "Generating a synthetic sample (offline TTS, no human voice, no patient data)."
    say ""
    /usr/bin/say -v Samantha -o runtime/tmp/offline_sample.aiff \
        "There is no evidence of endocarditis on transthoracic echocardiography." 2>/dev/null
    /usr/bin/afconvert -f WAVE -d LEI16@16000 -c 1 \
        runtime/tmp/offline_sample.aiff "$SAMPLE" 2>/dev/null
    rm -f runtime/tmp/offline_sample.aiff
fi

if [ "$SANDBOX_OK" -eq 1 ] && [ -f "$SAMPLE" ]; then
    OUT=$(sandbox-exec -f "$SANDBOX" /bin/bash ./scripts/transcribe_local.sh "$SAMPLE" 2>/dev/null)
    if [ -n "$OUT" ]; then
        # Word count only. The transcript itself never reaches the report.
        ok "transcription succeeded with network denied ($(printf '%s' "$OUT" | wc -w | tr -d ' ') words)"
    else
        bad "transcription produced no output with network denied"
    fi
else
    warn "skipped: sandbox unavailable or sample missing"
fi

# --------------------------------------------------------------------------
say ""; say "## 5. Validation with network denied"; say ""
# --------------------------------------------------------------------------
if [ "$SANDBOX_OK" -eq 1 ]; then
    if sandbox-exec -f "$SANDBOX" .venv/bin/python scripts/validate_changes.py \
         --raw-text "Continue cefazolin 2 grams intravenously every 8 hours" \
         --polished-text "Continue cefazolin 3 grams intravenously every 8 hours." \
         >/dev/null 2>&1; then
        bad "validator accepted a changed dose"
    else
        ok "validator ran offline and rejected a changed dose"
    fi
else
    warn "skipped: sandbox unavailable"
fi

# --------------------------------------------------------------------------
say ""; say "## 6. Live connections during a transcription"; say ""
# --------------------------------------------------------------------------
# Observed rather than assumed: run unsandboxed and watch for sockets.
#
# NOTE: lsof ORs its selection criteria unless -a is given. `lsof -p PID -i`
# therefore lists every internet socket on the machine, not the process's own,
# and reported 120 unrelated sockets from rapportd and identityservicesd here
# before -a was added. A privacy check that cannot distinguish this process
# from the rest of the system is worse than no check, because it produces
# alarming numbers nobody can act on.
if [ -f "$SAMPLE" ]; then
    ./scripts/transcribe_local.sh "$SAMPLE" >/dev/null 2>&1 &
    RUNPID=$!
    sleep 1.5
    # The shell plus its descendants: whisper-cli runs as a child.
    PIDS="$RUNPID"
    for c in $(pgrep -P "$RUNPID" 2>/dev/null); do
        PIDS="$PIDS $c"
        for g in $(pgrep -P "$c" 2>/dev/null); do PIDS="$PIDS $g"; done
    done
    CONNS=0
    for p in $PIDS; do
        n=$(lsof -nP -a -p "$p" -i 2>/dev/null \
            | grep -vE '^COMMAND|127\.0\.0\.1|\[::1\]|localhost' | wc -l | tr -d ' ')
        CONNS=$((CONNS + n))
    done
    wait "$RUNPID" 2>/dev/null
    NPIDS=$(printf '%s' "$PIDS" | wc -w | tr -d ' ')
    if [ "$CONNS" -eq 0 ]; then
        ok "no non-loopback sockets opened by the transcription process tree ($NPIDS pids)"
    else
        bad "transcription process tree opened $CONNS non-loopback socket(s)"
    fi
else
    warn "skipped: no sample"
fi

# --------------------------------------------------------------------------
say ""; say "## 7. Temporary audio disposal"; say ""
# --------------------------------------------------------------------------
LEFTOVER=$(find runtime/tmp -maxdepth 1 -name 'asr.*' -o -maxdepth 1 -name 'rec.*' 2>/dev/null | wc -l | tr -d ' ')
if [ "$LEFTOVER" -eq 0 ]; then
    ok "no temporary audio directories survive in runtime/tmp"
else
    bad "$LEFTOVER temporary audio artefact(s) left behind"
fi

# Cleanup must survive abnormal termination, not just success.
BEFORE=$(find runtime/tmp -maxdepth 1 -name 'asr.*' 2>/dev/null | wc -l | tr -d ' ')
if [ -f "$SAMPLE" ]; then
    ./scripts/transcribe_local.sh "$SAMPLE" >/dev/null 2>&1 &
    KILLPID=$!
    sleep 1
    kill -TERM "$KILLPID" 2>/dev/null
    wait "$KILLPID" 2>/dev/null
    sleep 0.5
    AFTER=$(find runtime/tmp -maxdepth 1 -name 'asr.*' 2>/dev/null | wc -l | tr -d ' ')
    if [ "$AFTER" -le "$BEFORE" ]; then
        ok "temporary audio removed after SIGTERM mid-transcription"
    else
        bad "temporary audio survived SIGTERM (trap does not cover signals)"
    fi
fi

# --------------------------------------------------------------------------
say ""; say "## 8. Logs and persisted state"; say ""
# --------------------------------------------------------------------------
# Any transcript text on disk outside the deliberate fixtures is a leak.
STRAY=$(find runtime -maxdepth 2 -name '*.transcript.txt' -o -name '*.raw.txt' \
        -o -name '*.polished.txt' 2>/dev/null | wc -l | tr -d ' ')
if [ "$STRAY" -eq 0 ]; then
    ok "no transcript files persisted under runtime/"
else
    warn "$STRAY transcript-like file(s) under runtime/ (fixtures are expected; review)"
fi

if grep -rqE '\.info\(.*(raw_text|polished_text|transcript)' src/ 2>/dev/null; then
    bad "source logs transcript content"
else
    ok "no source path logs transcript content"
fi

# Committed history would be permanent; this is the check that matters most.
if git ls-files 2>/dev/null | grep -qE '\.(wav|m4a|mp3|aiff|flac)$'; then
    bad "audio files are tracked in git"
else
    ok "no audio tracked in git"
fi
if git ls-files 2>/dev/null | grep -qE '(transcript|\.raw\.txt|\.polished\.txt)$'; then
    bad "transcript files are tracked in git"
else
    ok "no transcript files tracked in git"
fi

# --------------------------------------------------------------------------
say ""; say "## 9. Fail-closed behaviour"; say ""
# --------------------------------------------------------------------------
# Point the polisher at a closed loopback port: the local model being absent
# must degrade to the raw transcript, never to a cloud call or a guess.
if .venv/bin/python - <<'PY' >/dev/null 2>&1
import sys; sys.path.insert(0, "src")
from pipeline.polisher import LocalPolisher
p = LocalPolisher(endpoint="http://127.0.0.1:9", timeout_seconds=3, retries=0)
r = p.polish("there is no evidence of endocarditis", [], "clinical_strict")
# Must fail, must return the raw text unchanged, must not raise.
raise SystemExit(0 if (not r.ok and r.polished_text == r.raw_text) else 1)
PY
then
    ok "polisher fails closed when the local model is unreachable"
else
    bad "polisher did not fail closed"
fi

if grep -rqiE '(openai|anthropic|api\.|fallback.*cloud)' src/pipeline/polisher.py 2>/dev/null; then
    warn "polisher source mentions a cloud provider; review for a fallback path"
else
    ok "no cloud provider referenced in the polisher"
fi

# --------------------------------------------------------------------------
say ""; say "## 9a. Rewrite runtime (ADR 0002)"; say ""
# --------------------------------------------------------------------------
# The network sandbox above denies loopback too, so it cannot host a call to
# Ollama. These checks observe the real server instead.
OLLAMA_PLIST="$HOME/Library/LaunchAgents/local.whispern.ollama.plist"
OLLAMA_LOG="$HOME/Library/Logs/whispern-ollama.log"
LISTEN=$(lsof -nP -iTCP:11434 -sTCP:LISTEN 2>/dev/null | awk 'NR>1 {print $9}' | sort -u)
if [ -z "$LISTEN" ]; then
    warn "ollama not running; rewrite runtime checks skipped"
else
    if printf '%s\n' "$LISTEN" | grep -qv '^127\.0\.0\.1:11434$'; then
        bad "ollama listening beyond loopback: $(echo "$LISTEN" | tr '\n' ' ')"
    else
        ok "ollama listens on 127.0.0.1:11434 only"
    fi

    if [ -f "$OLLAMA_PLIST" ] && grep -A1 OLLAMA_NO_CLOUD "$OLLAMA_PLIST" | grep -q '<string>1</string>' \
       && ! grep -q OLLAMA_DEBUG "$OLLAMA_PLIST"; then
        ok "ollama service has cloud features disabled and debug logging off"
    else
        bad "ollama service lacks OLLAMA_NO_CLOUD=1 or enables debug logging (run scripts/setup_ollama.sh)"
    fi

    if .venv/bin/python - <<'PY' >/dev/null 2>&1
import sys; sys.path.insert(0, "src")
from pipeline.polisher import LocalPolisher, LocalOnlyError
for name in ("gpt-oss:120b-cloud", "some-model:cloud"):
    try:
        LocalPolisher(model=name, prompt_path="prompts/eloquent_rewriter.md")
    except LocalOnlyError:
        continue
    raise SystemExit(1)
PY
    then
        ok "rewriter refuses Ollama cloud model names"
    else
        bad "rewriter accepted an Ollama cloud model name"
    fi

    # A synthetic canary sentence through the real rewriter, watching the
    # server's sockets while it runs, then searching its log for the canary.
    # The first `  model:` inside the top-level `rewrite:` block, however long
    # its comments grow (a fixed -A20 window once missed it).
    MODEL=$(awk '/^[a-z_]+:/{inrw=($1=="rewrite:")} inrw && /^  model:/{print $2; exit}' config/app.yaml)
    NUM_CTX=$(awk '/^[a-z_]+:/{inrw=($1=="rewrite:")} inrw && /^  num_ctx:/{print $2; exit}' config/app.yaml)
    CANARY="zanzibarquokka"
    OPIDS=$(pgrep -f 'ollama' | tr '\n' ' ')
    .venv/bin/python - "$MODEL" "$CANARY" "${NUM_CTX:-0}" <<'PY' >/dev/null 2>&1 &
import sys; sys.path.insert(0, "src")
from pipeline.polisher import LocalPolisher
p = LocalPolisher(model=sys.argv[1], prompt_path="prompts/eloquent_rewriter.md",
                  timeout_seconds=120, num_ctx=int(sys.argv[3]) or None)
r = p.polish(f"um so the {sys.argv[2]} report is not ready yet probably Friday", [], "rewrite")
raise SystemExit(0 if r.ok else 1)
PY
    RWPID=$!
    CONNS=0
    while kill -0 "$RWPID" 2>/dev/null; do
        for p in $OPIDS; do
            n=$(lsof -nP -a -p "$p" -i 2>/dev/null \
                | grep -vE '^COMMAND|127\.0\.0\.1|\[::1\]|localhost' | wc -l | tr -d ' ')
            CONNS=$((CONNS + n))
        done
        sleep 0.5
    done
    if wait "$RWPID"; then
        ok "rewrite completed through the local model ($MODEL)"
    else
        warn "rewrite did not complete (model not pulled?); socket check is partial"
    fi
    if [ "$CONNS" -eq 0 ]; then
        ok "ollama opened no non-loopback sockets during a rewrite"
    else
        bad "ollama opened non-loopback socket(s) during a rewrite ($CONNS observations)"
    fi
    if [ -f "$OLLAMA_LOG" ] && grep -q "$CANARY" "$OLLAMA_LOG"; then
        bad "ollama log contains dictated text (canary found in $OLLAMA_LOG)"
    else
        ok "ollama log does not contain dictated text (canary absent)"
    fi
fi

# --------------------------------------------------------------------------
say ""; say "## 10. Summary"; say ""
# --------------------------------------------------------------------------
say ""
say "| Result | Count |"
say "|---|---|"
say "| PASS | $PASS |"
say "| FAIL | $FAIL |"
say "| WARN | $WARN |"
say ""

printf '\n%s%d passed, %d failed, %d warnings%s\n' "$DIM" "$PASS" "$FAIL" "$WARN" "$RST"
printf 'report: %s\n' "$REPORT"

if [ "$FAIL" -gt 0 ]; then
    say "**Result: FAILED.** Do not treat this build as offline-safe."
    exit 1
fi
say "**Result: passed.** Verified empirically, not from configuration."
exit 0
