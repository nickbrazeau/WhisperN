# Global Hotkey and Dictation App — Options

Exploration for the remaining Phase 5 work. Not a decision; input to one.

Date: 2026-07-27.

---

## 1. The thing that actually decides this

**Both candidate apps are built on an assumption this project has measured to be
false: that transcribe → paste is immediate and safe.**

From `docs/phase6-results.md`, the fraction of utterances the validator marks
`review_required`:

| Corpus | Requires review |
|---|---|
| Real voice | **9.1 %** (3/33) |
| Held-out | **17.9 %** (7/39) |

So roughly **one dictation in six to eleven must stop and be read** before it is
used. Named-entity accuracy is 62.5 %: about one distinctive drug or organism
name in three is wrong.

A tool that silently types into the active application cannot express that. The
hotkey is the easy part — a few lines in any of the options below. The hard part
is a **blocking review step**, and neither reference app has one or has a
documented place to insert one.

That reframes the question from *"which app do we adopt?"* to *"what is the
smallest thing that adds a hotkey to the pipeline we already validated?"*

---

## 2. What the reference apps actually are

Verified against their repositories, 2026-07-27.

### VoiceInk — `github.com/Beingpax/VoiceInk`

| | |
|---|---|
| Stack | Swift, native macOS |
| Licence | **GPL-3.0** |
| Engine | whisper.cpp |
| Hotkeys | Yes, user-customisable (`KeyboardShortcuts`) |
| Post-transcription hook | **None documented** |
| Network | Sparkle auto-update |

Closest in spirit: native, offline-first, same engine. But no extension point,
so inserting the validator means forking Swift. GPL-3.0 means changes must be
GPL if distributed — irrelevant for personal use, relevant if shared. Sparkle
must be disabled for the offline claims in `docs/validation-report.md` to hold.

### OpenWhispr — `github.com/OpenWhispr/openwhispr`

| | |
|---|---|
| Stack | Electron 41, React 19, TypeScript |
| Licence | MIT |
| Engine | whisper.cpp + sherpa-onnx, optional local LLM |
| Hotkeys | Yes — global hotkey **with automatic pasting** |
| Post-transcription hook | "AI actions"; no documented plugin architecture |
| Network | **Cloud providers supported** — GPT-5, Claude, Gemini, Groq, OpenRouter |

Easier to modify (TypeScript), permissive licence, and already does
hotkey-plus-paste. But it ships **cloud model integrations**.

CLAUDE.md §2 requires disabling any existing automatic cloud fallback, and §3.1
forbids cloud language models outright. Adopting a codebase with compiled-in
cloud providers converts that from *"no such code path exists"* — the current,
easily-verified position — into *"a code path exists and is configured off"*,
which must be re-audited after every upstream merge. That is a permanent
verification burden, and `scripts/offline_smoke_test.sh` check 16 currently
passes precisely because no provider is referenced anywhere.

---

## 3. macOS permission reality

Tested on this machine.

| Capability | Needs Accessibility? |
|---|---|
| Register a global hotkey via Carbon `RegisterEventHotKey` | **No** (requires an app bundle) |
| Global key monitoring via `NSEvent` | Yes |
| Synthesise ⌘V to paste at cursor | **Yes** |

Confirmed empirically — `osascript` keystroke fails today:

```
System Events got an error: osascript is not allowed to send keystrokes. (1002)
```

**Any paste-at-cursor path requires granting Accessibility to whatever process
does the pasting.** That is unavoidable and is worth stating plainly: it grants
that process the ability to synthesise input system-wide.

### The FN key specifically

CLAUDE.md §1 mentions FN. On this machine Apple Dictation is **enabled**
(`com.apple.assistant.support "Dictation Enabled" = 1`) and `AppleFnUsageType`
is unset, i.e. system default. Reclaiming FN means either turning Apple
Dictation off or intercepting the key with Karabiner-Elements, which installs a
kernel-level driver. A plain chord such as ⌥Space avoids all of that and is the
pragmatic default.

---

## 4. Options

### A. Fork VoiceInk

- **For:** native, fast, offline-first, same engine, real product polish.
- **Against:** Swift fork with no extension point; re-merging upstream forever;
  must add a blocking review UI it was not designed for; GPL-3.0; disable
  Sparkle.
- **Effort:** high. **Audit burden:** medium.

### B. Fork OpenWhispr

- **For:** TypeScript, MIT, hotkey-and-paste already works.
- **Against:** Electron (heavy on a 16 GB machine already running whisper);
  cloud providers compiled in, permanently re-auditable; same missing review UI.
- **Effort:** medium. **Audit burden:** **high** — this is the deciding drawback.

### C. Hammerspoon as a thin shell over the existing pipeline

`brew install --cask hammerspoon`, then roughly:

```lua
hs.hotkey.bind({"alt"}, "space", function()
  hs.task.new("/path/to/scripts/dictate.py", function(code, stdout)
    -- exit 0 = safe, 1 = review_required, 2 = rejected
    if code == 0 then hs.eventtap.keyStrokes(stdout)
    else hs.alert.show("Review required — see terminal") end
  end):start()
end)
```

- **For:** the validated pipeline is unchanged and remains the only thing
  touching clinical text; no fork, no upstream merges; no cloud code anywhere;
  exit codes already encode the three outcomes; ~30 lines total.
- **Against:** Hammerspoon needs Accessibility; a third-party automation tool in
  the trusted path; review UI limited to alerts and choosers.
- **Effort:** **low.** **Audit burden:** low.

### D. Minimal native Swift menu-bar app

- **For:** smallest possible trusted surface; `RegisterEventHotKey` avoids
  Accessibility for the *hotkey* (still needed for paste); proper review window;
  no third-party dependency.
- **Against:** we own hotkey, menu bar, permissions, packaging, and signing.
- **Effort:** medium-high. **Audit burden:** lowest.

---

## 5. Recommendation

**Option C now, Option D if it proves worth it.**

Reasoning:

1. **The pipeline is the asset, not the shell.** Phases 1–7 produced a validated
   transcription-and-validation path with 43 tests, 16 privacy checks, and a
   documented error profile. Options A and B put a large, unfamiliar codebase
   *around* that asset and require re-verifying properties currently proven in
   one command.
2. **Option B's cloud integrations are disqualifying on current evidence.** Not
   because they would be used, but because their presence permanently weakens a
   claim that is currently trivial to verify.
3. **Option C is reversible.** If the ergonomics are wrong, nothing has been
   forked and nothing has been rewritten — delete 30 lines of Lua.
4. **It surfaces the real design question quickly**: what should happen on the
   ~1-in-8 dictation that needs review? That is a UX question best answered by
   using it, not by planning.

### Staging

1. Hotkey → record → transcribe → validate → **show result, copy to clipboard,
   never auto-paste**. Adds a hotkey to what already exists; Accessibility not
   required for the copy path.
2. Add auto-paste for `safe_to_paste` **only**, behind an explicit setting that
   defaults off. Requires Accessibility. This is the step CLAUDE.md §11 warns
   about, and §4.2 forbids auto-pasting a `review_required` result in
   `clinical_strict`.
3. A proper review window, if alerts prove insufficient. If that happens,
   revisit Option D.

**Do not skip stage 1.** Its whole purpose is to measure how often review
actually fires in daily use, on real dictation rather than an adversarial
corpus. The 9.1 % and 17.9 % figures come from corpora deliberately built from
hard terminology; the real rate is probably lower, and it determines whether
stage 2 is worth building at all.

---

## 6. Open questions for the user

1. **Hotkey:** ⌥Space, or reclaim FN? FN means disabling Apple Dictation or
   installing Karabiner-Elements.
2. **Accessibility:** willing to grant it to Hammerspoon? Stage 1 works without
   it; stage 2 does not.
3. **Scope:** clipboard-only first (recommended), or straight to auto-paste for
   safe results?
4. **Installing Hammerspoon** modifies the system and is the only step here that
   does. Approval needed before proceeding.
