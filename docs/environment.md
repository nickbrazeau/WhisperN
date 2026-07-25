# Environment Report — Phase 0

Generated: 2026-07-25
Scope: machine inventory for the local medical dictation pipeline. No usernames,
hostnames, tokens, secrets, or patient data are recorded here.

---

## 1. Hardware and operating system

| Property | Value |
|---|---|
| Architecture | `arm64` (Apple Silicon) |
| Chip | Apple M1 Pro |
| Cores | 8 total (6 performance, 2 efficiency) |
| Unified memory | 16 GB |
| macOS version | 15.5 |
| Build | 24F74 |

### Disk

| Volume | Size | Used | Available |
|---|---|---|---|
| `/` (system, sealed) | 460 Gi | 10 Gi | 76 Gi |
| `/System/Volumes/Data` | 460 Gi | 348 Gi | 76 Gi |

**Assessment:** 76 GB free is sufficient. Planned local footprint is roughly
5–8 GB (whisper.cpp build + `large-v3-turbo` model ≈ 1.6 GB; an additional
polishing model if one beyond the existing `llama3.1:8b` is added).

**Memory note:** 16 GB unified memory is adequate but not generous. Holding
`large-v3-turbo` (~1.6 GB) and an 8B polishing model (~4.9 GB resident) at the
same time is feasible, but memory pressure should be measured in Phase 1 and
Phase 3. The `medium.en` / `small.en` fallback path in CLAUDE.md §2 is likely to
matter on this machine.

---

## 2. Audio input

Enumerated via `system_profiler SPAudioDataType`.

| Device | Transport | Channels | Current sample rate |
|---|---|---|---|
| Bluetooth headset (Bose QuietComfort 35) | Bluetooth | 1 | 16000 Hz |
| MacBook Pro Microphone | Built-in | 1 | 48000 Hz |

The Bluetooth headset is currently the **default input device**.

**Implications:**

- Whisper requires 16 kHz mono. The built-in microphone runs at 48 kHz and must
  be resampled; the recorder must not assume the device rate.
- Bluetooth HFP input at 16 kHz is low-bandwidth and will degrade medical-term
  recognition. Baseline measurements in Phase 1 should be taken on the **built-in
  microphone**, with the Bluetooth path measured separately as a known-worse case.
- Whichever device is active at capture time materially changes error rate, so
  the active input device should be recorded alongside every benchmark run.

---

## 3. Installed tooling

| Tool | Status | Version / path |
|---|---|---|
| Homebrew | Installed | 6.0.11 — `/opt/homebrew/bin/brew` |
| git | Installed | 2.49.0 |
| Xcode Command Line Tools | Installed | active developer dir: `/Applications/Xcode.app/Contents/Developer` |
| python3 | Installed | 3.13.12 (Homebrew miniconda base env) |
| node | Installed | v24.8.0 |
| ollama | Installed | 0.31.1 |
| **cmake** | **Not installed** | required to build whisper.cpp |
| **whisper-cli** | **Not installed** | not built yet |
| **ffmpeg** | **Installed but broken** | see below |
| sox | Not installed | optional |

### ffmpeg is currently non-functional

`ffmpeg -version` fails at dynamic-link time:

```
dyld: Library not loaded: /opt/homebrew/opt/x265/lib/libx265.215.dylib
  Referenced from: /opt/homebrew/Cellar/ffmpeg/8.0.1_3/bin/ffmpeg
```

The installed ffmpeg 8.0.1_3 links against `libx265.215`, but the installed
x265 is 4.2, which does not provide that library version. This is a broken
Homebrew dependency, not a missing package. ffmpeg is needed for the
`-ar 16000 -ac 1 -c:a pcm_s16le` conversion step in CLAUDE.md §6 Phase 1.

### Python environment note

`python3` resolves into the **miniconda base environment**, not a project venv.
Installing project dependencies there would pollute a shared environment and
breaks the dependency-pinning requirement in CLAUDE.md §10.7. A dedicated
project virtual environment is recommended.

---

## 4. Local model runtime

`ollama` is installed and running.

| Property | Value |
|---|---|
| Listening socket | `127.0.0.1:11434` (loopback only) |
| Bound to `0.0.0.0` or LAN | **No** |
| Installed models | `llama3.1:8b` (4.9 GB, ID `46e0c10c039e`) |

This satisfies the CLAUDE.md §3.1 / §7 requirement that the polishing endpoint
bind only to loopback. The binding must be **re-verified empirically** in Phase 7
rather than assumed, since `OLLAMA_HOST` can change it at any restart.

`llama3.1:8b` is a candidate polisher but has **not** been evaluated against the
constrained-editing prompt or the JSON output schema. No polishing model is
approved yet; `config/app.yaml` keeps `REPLACE_WITH_APPROVED_LOCAL_MODEL` until
Phase 3 evidence exists.

---

## 5. Cloud-provider environment variables

Checked for `OPENAI*`, `ANTHROPIC_API*`, `AZURE*`, `GOOGLE_APPLICATION*`,
`DEEPGRAM*`, `ASSEMBLYAI*`, `GROQ*`, `HF_TOKEN`.

**Result: none set in the current shell environment.**

Caveat: this reflects one interactive shell only. It does not cover launchd
agents, GUI-application environments, or credentials stored in files
(`~/.config`, `~/.netrc`, Keychain). Phase 7's `offline_smoke_test.sh` must
perform this check inside the actual runtime process, not in a login shell.

---

## 6. Items not yet determined

These require user input or a GUI check and are deliberately left open:

- **Microphone permission (TCC)** for the eventual recorder process. Not yet
  granted, because the recorder does not exist yet. macOS will prompt on first
  capture; the permission is per-binary, so it must be re-granted if the
  recorder is rebuilt or repackaged.
- **Intended dictation application.** CLAUDE.md §13 lists OpenWhispr and VoiceInk
  as references. Not yet chosen. CLAUDE.md §6 Phase 5 forbids modifying any
  upstream application until the standalone pipeline passes its tests, so this
  decision can be deferred.
- **Launch at login.** Undetermined; depends on the application choice above.
- **FN-key hotkey.** macOS binds a double-press of FN to system dictation by
  default. Whether that binding is currently active, and whether it can be
  reclaimed without disabling Apple's dictation, has not been tested.

---

## 7. Baseline conclusions

1. The machine is capable of running the intended fully-local stack.
2. Three blockers exist before Phase 1 can begin: `cmake` is missing, `ffmpeg`
   is broken, and `whisper.cpp` is not built.
3. The local model runtime already satisfies the loopback-only constraint.
4. No cloud credentials were found in the shell environment.
5. The two audio input devices differ enough in bandwidth that benchmarks must
   record which one was used.
