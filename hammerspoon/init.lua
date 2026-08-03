-- WhisperN — global hotkey shell.
--
-- This file is deliberately thin. It starts and stops a recording and hands
-- the audio to scripts/dictate.py, which owns every decision about clinical
-- text. Nothing here inspects, edits, or filters a transcript.
--
-- Install:
--   ln -s <repo>/hammerspoon/init.lua ~/.hammerspoon/init.lua
--
-- KEYS
--   ⌘ + FN     toggle recording — press to start, press again to stop
--   ⌘ ⌃ A      anchor the paste destination here / clear it if already here
--   ⌘ ⌃ ⇧ A    clear the anchor
--
-- WHERE THE TEXT GOES
--
--   no anchor   the application that was frontmost when recording started,
--               and only if it is still frontmost when the text is ready
--   anchored    the anchored application, window, and text field, whatever is
--               frontmost now
--
-- PASTING, and its limits. Only a `safe_to_paste` result is pasted without
-- being read. "Safe" means the validator found no protected content changed
-- (CLAUDE.md §4.2). It does NOT mean every word is right: named-entity
-- accuracy is 62.5%, so roughly one distinctive drug or organism name in three
-- is wrong (docs/validation-report.md §3.3). Read what lands.
--
-- `review_required` and `rejected` are never pasted automatically, whatever
-- the destination. They open a modal dialog, and pasting them takes a second,
-- explicit click after reading — which is the point of the dialog.
--
-- The text is put on the clipboard before any paste is attempted, so every
-- failure path below still leaves a working ⌘V.
--
-- Text is delivered as one ⌘V, never as synthesised characters. Typing a
-- transcript character by character -- `hs.eventtap.keyStrokes` -- mangles it
-- in any application with autocomplete or autocorrect, and there is no way to
-- tell afterwards that it did.

local M = {}

-- ---------------------------------------------------------------- config

local REPO = os.getenv("HOME") .. "/AgentWorkspaces/WhisperN"
local PYTHON = REPO .. "/.venv/bin/python"
local DICTATE = REPO .. "/scripts/dictate.py"
local FFMPEG = "/opt/homebrew/bin/ffmpeg"

-- Paste a `safe_to_paste` result automatically. Set false for clipboard-only
-- behaviour; the review dialogs and the explicit paste button still work.
local AUTO_PASTE = true

-- avfoundation input index. NOT stable: indices renumber when a device
-- connects or disconnects, which is how a Bluetooth headset silently changes
-- which microphone index 0 refers to. This was not hypothetical -- index 0 was
-- the Microsoft Teams virtual driver, which captures digital silence.
--
-- So the index is never hardcoded. It is resolved by name through
-- `dictate.py --print-device-index` (which reads recording.prefer_microphone
-- from config/app.yaml), cached because resolving costs 0.24-1.03 s and must
-- not sit between pressing the hotkey and capturing speech, and re-resolved
-- whenever macOS reports an audio device change.
--
-- Inspect with: scripts/dictate.py --list-devices
local AUDIO_DEVICE = nil

-- Minimum recording length, in seconds. With a toggle key a double-tap is the
-- accident to guard against, not a key tap: it produces a fragment of audio
-- that Whisper will still try to transcribe.
local MIN_RECORD = 0.35

-- Hard stop. A toggle cannot be forgotten in the "off" position but it can be
-- forgotten in the "on" position, and a recording nobody meant to start should
-- not run until the disk fills.
local MAX_RECORD = 300

-- How long to wait for macOS to actually front an application after
-- activate(). Activation is asynchronous; pasting before it lands types into
-- the previous application.
local ACTIVATE_TIMEOUT = 1.5

local RED   = { red = 0.75, green = 0.15, blue = 0.15, alpha = 0.92 }
local BLUE  = { red = 0.15, green = 0.35, blue = 0.75, alpha = 0.92 }
local SLATE = { red = 0.13, green = 0.15, blue = 0.20, alpha = 0.92 }

-- ---------------------------------------------------------------- state

local recording = nil     -- hs.task running ffmpeg
local recordPath = nil
local startedAt = nil
local indicator = nil
local busy = false
local tickTimer = nil
local limitTimer = nil

-- Holds the short timer between stopping ffmpeg and using the file.
--
-- Retained deliberately. An hs.timer that nothing references can be garbage
-- collected before it fires, which is not theoretical: the load receipt below
-- rewrote itself on one launch and not the next, from identical code. Here the
-- cost of losing the timer is the whole dictation -- the transcription never
-- starts and the recording just disappears -- or, on the short-take path, a
-- temporary WAV that is never deleted.
local pendingTimer = nil

-- Where a paste would land.
--
-- `cursorTarget` is the application that was frontmost when recording started.
-- Hammerspoon never takes focus, so that application still owns the caret when
-- the transcript arrives -- but only if the user has not switched away, which
-- is checked rather than assumed.
--
-- `anchor` overrides it: an explicitly marked application, window, and text
-- field, restored before pasting however focus has moved since.
--
-- PRIVACY (CLAUDE.md §3.1): these hold an application reference, a numeric
-- window id, an accessibility handle to a text field, and a caret offset. No
-- window title, document name, or field content is read, stored, or logged --
-- the window is identified by id precisely so no title is touched. In memory
-- only, never written to disk or to the audit trail, discarded on reload.
local cursorTarget = nil
local anchor = nil
local anchorBadge = nil

-- ---------------------------------------------------------------- ui

local function showIndicator(text, colour)
  if not indicator then
    local screen = hs.screen.mainScreen():frame()
    indicator = hs.canvas.new({
      x = screen.w / 2 - 190, y = screen.h - 130, w = 380, h = 46,
    })
    indicator[1] = {
      type = "rectangle", action = "fill",
      fillColor = colour, roundedRectRadii = { xRadius = 10, yRadius = 10 },
    }
    indicator[2] = {
      type = "text", text = text, textSize = 15, textAlignment = "center",
      textColor = { white = 1, alpha = 1 }, frame = { x = 0, y = 12, w = 380, h = 30 },
    }
    indicator:show()
    return
  end
  indicator:elementAttribute(1, "fillColor", colour)
  indicator:elementAttribute(2, "text", text)
end

local function hideIndicator()
  if indicator then indicator:delete(); indicator = nil end
end

-- The application a paste would go to right now, or nil if there is none.
-- Returns app, name, anchored.
local function destinationApp()
  if anchor then
    if anchor.app:isRunning() then return anchor.app, anchor.name, true end
    return nil
  end
  if cursorTarget and cursorTarget:isRunning() then
    return cursorTarget, cursorTarget:name(), false
  end
  return nil
end

-- A one-line description of where the next paste would go. Shown while
-- recording, so a wrong destination is caught before anything is spoken --
-- with an anchor set, the text lands somewhere the user is not looking.
local function destinationLabel()
  if not AUTO_PASTE then return "clipboard only" end
  local _, name, anchored = destinationApp()
  if not name then return "clipboard" end
  return (anchored and "⚓ " or "→ ") .. name
end

local function updateAnchorBadge()
  if anchorBadge then anchorBadge:delete(); anchorBadge = nil end
  if not anchor then return end
  local screen = hs.screen.mainScreen():frame()
  anchorBadge = hs.canvas.new({ x = screen.w - 232, y = 8, w = 224, h = 26 })
  anchorBadge[1] = {
    type = "rectangle", action = "fill", fillColor = SLATE,
    roundedRectRadii = { xRadius = 8, yRadius = 8 },
  }
  anchorBadge[2] = {
    type = "text", text = "⚓ " .. anchor.name, textSize = 12,
    textAlignment = "center", textColor = { white = 1, alpha = 0.9 },
    frame = { x = 0, y = 5, w = 224, h = 20 },
  }
  anchorBadge:show()
end

-- ---------------------------------------------------------------- anchor

-- Capture the current insertion point as the paste destination.
--
-- Three things are recorded, each a fallback for the one before it: the
-- application (always), its focused window (usually), and the focused text
-- field with its caret offset (only where the application exposes them
-- through the accessibility API). Restoring the field is what makes an anchor
-- land in the same box rather than merely the same app; where it is
-- unavailable, activating the window still puts the text where that window
-- last had its caret.
local function setAnchor()
  local app = hs.application.frontmostApplication()
  if not app or app:name() == "Hammerspoon" then
    hs.alert.show("Nothing to anchor to", 1.5)
    return
  end

  -- The window is stored as an id, not as an hs.window object: a stored object
  -- goes stale when its window closes and offers no way to ask whether it has.
  -- An id can simply be looked up again, and fail by returning nil.
  local win = hs.window.focusedWindow()
  local a = { app = app, name = app:name(), windowId = win and win:id() or nil }

  local ok, axapp = pcall(hs.axuielement.applicationElement, app)
  if ok and axapp then
    a.axapp = axapp
    local okEl, el = pcall(function() return axapp:attributeValue("AXFocusedUIElement") end)
    if okEl and el then
      a.element = el
      local okR, r = pcall(function() return el:attributeValue("AXSelectedTextRange") end)
      if okR then a.range = r end
    end
  end

  anchor = a
  updateAnchorBadge()
  hs.alert.show("Anchored → " .. a.name
    .. (a.element and "" or "  (app only — no text field found)"), 2)
end

local function clearAnchor(why)
  if not anchor then return end
  anchor = nil
  updateAnchorBadge()
  hs.alert.show(why or "Anchor cleared — pasting at the cursor again", 1.5)
end

-- ⌘⌃A in the anchored application clears the anchor; anywhere else it moves
-- it. Pressing the same key to undo what it just did is the behaviour that
-- needs no remembering.
local function toggleAnchor()
  local front = hs.application.frontmostApplication()
  if anchor and front and anchor.app:isRunning() and front:pid() == anchor.app:pid() then
    clearAnchor()
  else
    setAnchor()
  end
end

-- Best effort, and deliberately silent on failure: the window is already
-- focused by this point, so the worst case is pasting at that window's own
-- caret instead of the anchored field.
local function restoreAnchorCaret()
  local a = anchor
  if not (a and a.element) then return end
  pcall(function() a.element:setAttributeValue("AXFocused", true) end)
  if a.axapp then
    pcall(function() a.axapp:setAttributeValue("AXFocusedUIElement", a.element) end)
  end
  if a.range then
    pcall(function() a.element:setAttributeValue("AXSelectedTextRange", a.range) end)
  end
end

-- ---------------------------------------------------------------- paste

local function frontmostIs(app)
  local f = hs.application.frontmostApplication()
  local ok = (f ~= nil) and (app ~= nil) and (f:pid() == app:pid())
  return ok, (f and f:name()) or "an unknown application"
end

-- Confirm `app` really is frontmost, then send ⌘V.
--
-- The confirmation is the safety property, not a nicety: ⌘V goes wherever
-- macOS thinks the front application is, so a stale target sends clinical text
-- into whatever happens to be there. If the check fails nothing is typed
-- anywhere and the text is left on the clipboard, which is strictly better
-- than putting it in the wrong window.
local function sendPaste(app, prepare, done)
  local ok, who = frontmostIs(app)
  if not ok then
    return done("blocked", "focus is on " .. who)
  end
  if prepare then pcall(prepare) end
  hs.eventtap.keyStroke({ "cmd" }, "v")
  done("pasted", app:name())
end

-- Activate `app`, wait for macOS to make it frontmost, then paste. Activation
-- is asynchronous, so this polls rather than sleeping a fixed interval and
-- hoping.
local function activateAndPaste(app, prepare, done)
  app:activate()
  local deadline = hs.timer.secondsSinceEpoch() + ACTIVATE_TIMEOUT
  local poll
  poll = hs.timer.doEvery(0.05, function()
    local ok, who = frontmostIs(app)
    if ok then
      poll:stop()
      sendPaste(app, prepare, done)
    elseif hs.timer.secondsSinceEpoch() > deadline then
      poll:stop()
      done("blocked", "could not bring " .. app:name() .. " forward; focus is on " .. who)
    end
  end)
end

-- Deliver the clipboard to the current destination. `explicit` marks a paste
-- the user asked for by clicking, which may take focus back from a dialog;
-- an automatic paste may not, and must find its target already frontmost.
--
-- Calls done("pasted"|"blocked", detail). Never raises, never types anywhere
-- it has not confirmed.
local function paste(explicit, done)
  if anchor then
    if not anchor.app:isRunning() then
      clearAnchor("Anchor cleared — that application quit")
      return done("blocked", "the anchored application quit")
    end
    -- Focus the exact window first where we still have one; activate() alone
    -- brings forward whichever window the application last used. A window that
    -- has since closed looks up as nil, and activate() below is the fallback.
    if anchor.windowId then
      local win = hs.window.get(anchor.windowId)
      if win then pcall(function() win:focus() end) end
    end
    return activateAndPaste(anchor.app, restoreAnchorCaret, done)
  end

  local app = cursorTarget
  if not app then return done("blocked", "no target application was recorded") end
  if not app:isRunning() then return done("blocked", "the target application quit") end
  if explicit then return activateAndPaste(app, nil, done) end
  return sendPaste(app, nil, done)
end

-- ---------------------------------------------------------------- review

-- Review presentation. Deliberately modal and deliberately awkward: a result
-- that needs review should interrupt, not scroll past in a notification.
--
-- Read-only. An editable field was tried here and reverted: hs.dialog's text
-- input is a single-line NSTextField inside an alert, which renders a
-- multi-sentence transcript as one long unreadable line. Correcting text is
-- better done in the destination application, which has a real editor, so the
-- choice here is only whether to paste or to keep the clipboard.
--
-- The dialog takes focus, which is why its paste is an `explicit` one -- the
-- destination has to be re-activated afterwards.
local function showReview(title, result, style)
  local lines = { result.text or result.raw_text or "" }
  if result.warnings and #result.warnings > 0 then
    table.insert(lines, "")
    for _, w in ipairs(result.warnings) do table.insert(lines, "• " .. w) end
  end
  if result.protected_changes and #result.protected_changes > 0 then
    table.insert(lines, "")
    for _, c in ipairs(result.protected_changes) do table.insert(lines, "! " .. c) end
  end
  table.insert(lines, "")
  table.insert(lines, "On the clipboard. Nothing has been pasted.")
  local body = table.concat(lines, "\n")

  -- Offer a paste only where there is somewhere to paste to. A button that
  -- reports "no target application" after being clicked is worse than no
  -- button.
  local _, destName = destinationApp()
  if not destName then
    hs.dialog.blockAlert(title, body, "OK — copied", "", style)
    return
  end

  local pasteButton = "Paste into " .. destName
  if hs.dialog.blockAlert(title, body, "Copy only", pasteButton, style) == pasteButton then
    paste(true, function(status, detail)
      hs.alert.show(status == "pasted" and ("Pasted → " .. detail)
                    or ("Not pasted: " .. detail .. " — still on the clipboard"), 3)
    end)
  end
end

-- ---------------------------------------------------------------- pipeline

local function process(path)
  busy = true
  showIndicator("Transcribing…", BLUE)

  hs.task.new(PYTHON, function(code, stdout, stderr)
    busy = false
    hideIndicator()

    local ok, result = pcall(hs.json.decode, stdout or "")
    if not ok or type(result) ~= "table" then
      hs.alert.show("Dictation failed\n" .. (stderr or "no output"), 4)
      os.remove(path)
      return
    end

    if result.outcome == "no_speech" then
      hs.alert.show("No speech detected", 1.5)

    elseif result.outcome == "safe_to_paste" then
      -- dictate.py --copy has already put the text on the clipboard, so every
      -- branch below leaves the user a working ⌘V.
      if not AUTO_PASTE then
        hs.alert.show("Copied  ·  " .. (result.text or ""), 2.5)
      else
        paste(false, function(status, detail)
          if status == "pasted" then
            hs.alert.show("Pasted → " .. detail, 1.5)
          else
            hs.alert.show("Copied, not pasted: " .. detail .. "\n\n"
              .. (result.text or ""), 4)
          end
        end)
      end

    elseif result.outcome == "review_required" then
      showReview("Review required — not safe to use unchecked", result, "warning")

    elseif result.outcome == "rejected" then
      showReview("Rejected — this is the raw transcript", result, "critical")

    else
      hs.alert.show("Error: " .. (result.error or "unknown"), 4)
    end

    -- dictate.py deletes its own working copy; this is the recording itself.
    os.remove(path)
  end, { DICTATE, "--file", path, "--json", "--copy" }):start()
end

-- Resolve the microphone by name, off the hotkey path. Cached in
-- AUDIO_DEVICE; callers must tolerate it still being nil.
local function resolveDevice()
  hs.task.new(PYTHON, function(code, stdout, _)
    if code == 0 then
      AUDIO_DEVICE = (stdout or ""):gsub("%s+", "")
      if AUDIO_DEVICE == "" then AUDIO_DEVICE = nil end
    end
  end, { DICTATE, "--print-device-index" }):start()
end

-- Pause only players that are actually playing, and remember which, so resume
-- never starts music the user had stopped themselves.
local pausedPlayers = {}

local function pauseMusic()
  pausedPlayers = {}
  if hs.spotify.isRunning() and hs.spotify.isPlaying() then
    hs.spotify.pause()
    table.insert(pausedPlayers, "spotify")
  end
  if hs.itunes.isRunning() and hs.itunes.isPlaying() then
    hs.itunes.pause()
    table.insert(pausedPlayers, "itunes")
  end
end

local function resumeMusic()
  for _, p in ipairs(pausedPlayers) do
    if p == "spotify" then hs.spotify.play() else hs.itunes.play() end
  end
  pausedPlayers = {}
end

-- ---------------------------------------------------------------- record

local stopRecording  -- forward declaration; the time limit stops itself

local function tick()
  local secs = math.floor(hs.timer.secondsSinceEpoch() - (startedAt or 0))
  showIndicator(string.format("●  %d:%02d      %s", secs // 60, secs % 60,
                              destinationLabel()), RED)
end

local function startRecording()
  if recording or busy then return end

  if not AUDIO_DEVICE then
    -- Refuse rather than fall back to index 0, which is how a silent virtual
    -- driver got recorded in the first place.
    resolveDevice()
    hs.alert.show("No microphone resolved yet — try again", 1.5)
    return
  end

  -- Captured now, before anything else can take focus. Hammerspoon itself is
  -- never a paste target: it is frontmost only just after one of our own
  -- dialogs, and ⌘V there does nothing useful.
  local front = hs.application.frontmostApplication()
  cursorTarget = (front and front:name() ~= "Hammerspoon") and front or nil

  recordPath = os.tmpname() .. ".wav"
  startedAt = hs.timer.secondsSinceEpoch()

  pauseMusic()

  recording = hs.task.new(FFMPEG, nil, {
    "-nostdin", "-loglevel", "error", "-f", "avfoundation",
    "-i", ":" .. AUDIO_DEVICE, "-ar", "16000", "-ac", "1",
    "-c:a", "pcm_s16le", "-y", recordPath,
  })
  recording:start()

  tick()
  tickTimer = hs.timer.doEvery(1, tick)
  limitTimer = hs.timer.doAfter(MAX_RECORD, function()
    if recording then
      hs.alert.show("Stopped at the " .. MAX_RECORD .. " s limit", 2)
      stopRecording()
    end
  end)
end

stopRecording = function()
  if not recording then return end
  local held = hs.timer.secondsSinceEpoch() - (startedAt or 0)

  -- SIGINT, not terminate: ffmpeg finalises the WAV header on interrupt and
  -- truncates the file on kill.
  recording:interrupt()
  recording = nil

  if tickTimer then tickTimer:stop(); tickTimer = nil end
  if limitTimer then limitTimer:stop(); limitTimer = nil end
  hideIndicator()

  -- Resume as soon as capture ends, on every path below, including the
  -- too-short one: a discarded take must not leave music stopped.
  resumeMusic()

  local path = recordPath
  recordPath = nil

  if held < MIN_RECORD then
    hs.alert.show("Too short", 1)
    pendingTimer = hs.timer.doAfter(0.4, function() os.remove(path) end)
    return
  end

  -- Give ffmpeg a moment to flush; it buffers the WAV until close.
  pendingTimer = hs.timer.doAfter(0.4, function() process(path) end)
end

local function toggleRecording()
  if busy then
    hs.alert.show("Still transcribing…", 1)
    return
  end
  if recording then stopRecording() else startRecording() end
end

-- ---------------------------------------------------------------- hotkeys

-- ⌘+FN, as a toggle: press to start, press again to stop.
--
-- FN is not a normal key -- it arrives as a flagsChanged event, so it cannot
-- be bound with hs.hotkey.bind (which uses Carbon RegisterEventHotKey).
-- Watching flagsChanged requires an event tap, which is why Hammerspoon needs
-- Accessibility permission.
--
-- Requiring ⌘ alongside it is what makes the key safe to leave armed all day:
-- FN alone is pressed constantly for function keys and emoji, and a toggle
-- that fires on those would start recording without anyone noticing.
--
-- Reclaiming FN also requires Apple's own dictation to be off, or macOS will
-- act on the same key. See docs/hotkey-integration-options.md §3.
local comboDown = false

-- Only a press or release of ⌘ or FN itself may toggle. macOS sets the FN flag
-- on other events too -- the arrow keys carry it, so ⌘← and ⌘↑ arrive with
-- exactly the flags we are looking for. Checking which key actually changed
-- keeps "go to start of line" from starting a recording.
-- The literals are the fallback, not the answer: an unmapped name would make
-- the table index nil, which is a load-time error that would take the whole
-- config down.
local TOGGLE_KEYS = {
  [hs.keycodes.map.fn or 63] = true,
  [hs.keycodes.map.cmd or 55] = true,
  [hs.keycodes.map.rightcmd or 54] = true,
}

M.hotkeyWatcher = hs.eventtap.new({ hs.eventtap.event.types.flagsChanged }, function(e)
  if not TOGGLE_KEYS[e:getKeyCode()] then return false end
  local f = e:getFlags()
  local down = (f.fn == true) and (f.cmd == true)
  -- Edge-triggered. Holding the pair down must toggle once, not repeatedly,
  -- and every flagsChanged while it is held reports the same state.
  if down and not comboDown then
    comboDown = true
    toggleRecording()
  elseif not down then
    comboDown = false
  end
  return false      -- never swallow the event; other apps still see ⌘ and FN
end)

M.hotkeyWatcher:start()

-- macOS disables an event tap that blocks for too long, silently and without
-- warning -- after which the hotkey simply stops working. Re-arm it rather
-- than leave the user pressing a dead key.
M.tapWatchdog = hs.timer.doEvery(30, function()
  if not M.hotkeyWatcher:isEnabled() then M.hotkeyWatcher:start() end
end)

-- ⌘⌃A and ⌘⌃⇧A are ordinary chords, so hs.hotkey.bind is enough. They are
-- swallowed rather than passed through, unlike the recording key: an
-- application that also uses ⌘⌃A would otherwise act on it while we anchor.
M.anchorHotkey = hs.hotkey.bind({ "cmd", "ctrl" }, "a", toggleAnchor)
M.clearAnchorHotkey = hs.hotkey.bind({ "cmd", "ctrl", "shift" }, "a", function()
  if anchor then clearAnchor() else hs.alert.show("No anchor set", 1) end
end)

-- An anchored application that quits would otherwise leave a badge pointing at
-- nothing, and the failure would surface only when a paste was blocked.
M.appWatcher = hs.application.watcher.new(function(name, event, app)
  if event == hs.application.watcher.terminated and anchor
     and anchor.name == name and not anchor.app:isRunning() then
    clearAnchor("Anchor cleared — " .. name .. " quit")
  end
end)
M.appWatcher:start()

-- ---------------------------------------------------------------- startup

-- Resolve the microphone now, and again whenever macOS reports an audio
-- device change -- connecting a headset renumbers the avfoundation indices,
-- which is the whole reason the index is not hardcoded.
resolveDevice()
M.audioWatcher = hs.audiodevice.watcher
M.audioWatcher.setCallback(function() resolveDevice() end)
M.audioWatcher.start()

-- Write a load receipt.
--
-- Hammerspoon logs nothing to the unified log and its Console is in-memory
-- only, so from outside the process there is otherwise no way to tell a
-- config that loaded from one that died halfway and left a stale event tap.
-- After an edit, `cat` this file: if the timestamp is old, the reload did not
-- take.
--
-- Deliberately a file rather than hs.ipc. An IPC channel would answer the same
-- question by opening a local port that can execute arbitrary Lua inside this
-- process, which is a poor trade for a status line. This carries the load time
-- and the active configuration -- never a transcript, and nothing from any
-- dictation (CLAUDE.md §3.1).
local function writeReceipt()
  local dir = REPO .. "/runtime/tmp"
  hs.fs.mkdir(REPO .. "/runtime")
  hs.fs.mkdir(dir)
  local f = io.open(dir .. "/hammerspoon-status.txt", "w")
  if not f then return end
  f:write(string.format(
    "loaded      %s\nrecord key  cmd+fn (toggle)\nanchor key  cmd+ctrl+a\n"
    .. "auto_paste  %s\nmax_record  %ds\nmic         %s\n",
    os.date("%Y-%m-%dT%H:%M:%S"), tostring(AUTO_PASTE), MAX_RECORD,
    AUDIO_DEVICE or "resolving"))
  f:close()
end

writeReceipt()
-- Again once the microphone has resolved, so the receipt shows the device
-- actually in use rather than the "resolving" placeholder.
M.receiptTimer = hs.timer.doAfter(3, writeReceipt)

hs.alert.show("WhisperN ready — ⌘FN to record, ⌘⌃A to anchor", 2)

return M
