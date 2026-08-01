-- WhisperN — global hotkey shell.
--
-- This file is deliberately thin. It starts and stops a recording and hands
-- the audio to scripts/dictate.py, which owns every decision about clinical
-- text. Nothing here inspects, edits, or filters a transcript.
--
-- STAGE 1: CLIPBOARD ONLY. Nothing is ever pasted. `hs.eventtap.keyStrokes`
-- does not appear in this file, and must not be added without reading
-- docs/hotkey-integration-options.md §5 first: roughly one dictation in eight
-- is flagged for review, and named-entity accuracy is 62.5%, so auto-pasting
-- would type a mis-transcribed drug name about one time in three.
--
-- Install:
--   ln -s <repo>/hammerspoon/init.lua ~/.hammerspoon/init.lua
--
-- Hold FN to record, release to transcribe.

local M = {}

-- ---------------------------------------------------------------- config

local REPO = os.getenv("HOME") .. "/AgentWorkspaces/WhisperN"
local PYTHON = REPO .. "/.venv/bin/python"
local DICTATE = REPO .. "/scripts/dictate.py"
local FFMPEG = "/opt/homebrew/bin/ffmpeg"

-- avfoundation input index. NOT stable: indices renumber when a device
-- connects or disconnects, which is how a Bluetooth headset silently changes
-- which microphone index 0 refers to. This was not hypothetical -- index 0 was
-- the Microsoft Teams virtual driver, which captures digital silence.
--
-- So the index is never hardcoded. It is resolved by name through
-- `dictate.py --print-device-index` (which reads recording.prefer_microphone
-- from config/app.yaml), cached because resolving costs 0.24-1.03 s and must
-- not sit between pressing FN and capturing speech, and re-resolved whenever
-- macOS reports an audio device change.
--
-- Inspect with: scripts/dictate.py --list-devices
local AUDIO_DEVICE = nil

-- Minimum hold before a recording is treated as real, in seconds. A tap of the
-- key produces a fragment of audio that Whisper will still try to transcribe.
local MIN_HOLD = 0.35

-- ---------------------------------------------------------------- state

local recording = nil     -- hs.task running ffmpeg
local recordPath = nil
local startedAt = nil
local indicator = nil
local busy = false

-- ---------------------------------------------------------------- ui

local function showIndicator(text, colour)
  if indicator then indicator:delete() end
  local screen = hs.screen.mainScreen():frame()
  indicator = hs.canvas.new({
    x = screen.w / 2 - 130, y = screen.h - 130, w = 260, h = 46,
  })
  indicator[1] = {
    type = "rectangle", action = "fill",
    fillColor = colour, roundedRectRadii = { xRadius = 10, yRadius = 10 },
  }
  indicator[2] = {
    type = "text", text = text, textSize = 15, textAlignment = "center",
    textColor = { white = 1, alpha = 1 }, frame = { x = 0, y = 12, w = 260, h = 30 },
  }
  indicator:show()
end

local function hideIndicator()
  if indicator then indicator:delete(); indicator = nil end
end

-- Review presentation. Deliberately modal and deliberately awkward: a result
-- that needs review should interrupt, not scroll past in a notification.
local function showReview(result)
  local lines = { result.raw_text or "" }
  if result.warnings and #result.warnings > 0 then
    table.insert(lines, "")
    for _, w in ipairs(result.warnings) do table.insert(lines, "• " .. w) end
  end
  if result.protected_changes and #result.protected_changes > 0 then
    table.insert(lines, "")
    for _, c in ipairs(result.protected_changes) do table.insert(lines, "! " .. c) end
  end
  hs.dialog.blockAlert(
    "Review required — not safe to use unchecked",
    table.concat(lines, "\n"),
    "OK (copied to clipboard)", "", "warning"
  )
end

-- ---------------------------------------------------------------- pipeline

local function process(path)
  busy = true
  showIndicator("Transcribing…", { red = 0.15, green = 0.35, blue = 0.75, alpha = 0.92 })

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
      -- "safe" means no protected content changed. It does NOT mean every
      -- word is right: 62.5% named-entity accuracy. Hence "copied", not
      -- "done", and never an automatic paste.
      hs.alert.show("Copied  ·  " .. (result.text or ""), 2.5)
    elseif result.outcome == "review_required" then
      showReview(result)
    elseif result.outcome == "rejected" then
      hs.dialog.blockAlert("Rejected", "Using the raw transcript.\n\n"
        .. (result.raw_text or ""), "OK", "", "critical")
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

local function startRecording()
  if recording or busy then return end

  if not AUDIO_DEVICE then
    -- Refuse rather than fall back to index 0, which is how a silent virtual
    -- driver got recorded in the first place.
    resolveDevice()
    hs.alert.show("No microphone resolved yet — try again", 1.5)
    return
  end

  recordPath = os.tmpname() .. ".wav"
  startedAt = hs.timer.secondsSinceEpoch()

  pauseMusic()

  recording = hs.task.new(FFMPEG, nil, {
    "-nostdin", "-loglevel", "error", "-f", "avfoundation",
    "-i", ":" .. AUDIO_DEVICE, "-ar", "16000", "-ac", "1",
    "-c:a", "pcm_s16le", "-y", recordPath,
  })
  recording:start()
  showIndicator("● Recording", { red = 0.75, green = 0.15, blue = 0.15, alpha = 0.92 })
end

local function stopRecording()
  if not recording then return end
  local held = hs.timer.secondsSinceEpoch() - (startedAt or 0)

  -- SIGINT, not terminate: ffmpeg finalises the WAV header on interrupt and
  -- truncates the file on kill.
  recording:interrupt()
  recording = nil
  hideIndicator()

  -- Resume as soon as capture ends, on every path below, including the
  -- too-short one: a discarded take must not leave music stopped.
  resumeMusic()

  local path = recordPath
  recordPath = nil

  if held < MIN_HOLD then
    hs.alert.show("Too short", 1)
    hs.timer.doAfter(0.4, function() os.remove(path) end)
    return
  end

  -- Give ffmpeg a moment to flush; it buffers the WAV until close.
  hs.timer.doAfter(0.4, function() process(path) end)
end

-- ---------------------------------------------------------------- hotkey

-- FN is not a normal key: it arrives as a flagsChanged event, so it cannot be
-- bound with hs.hotkey.bind (which uses Carbon RegisterEventHotKey). Watching
-- flagsChanged requires an event tap, which is why Hammerspoon needs
-- Accessibility permission even in this clipboard-only stage.
--
-- Reclaiming FN also requires Apple's own dictation to be off, or macOS will
-- act on the same key. See docs/hotkey-integration-options.md §3.
M.fnWatcher = hs.eventtap.new({ hs.eventtap.event.types.flagsChanged }, function(e)
  local fnDown = e:getFlags()["fn"] == true
  if fnDown and not recording then
    startRecording()
  elseif not fnDown and recording then
    stopRecording()
  end
  return false      -- never swallow the event; other apps still see FN
end)

M.fnWatcher:start()

-- Resolve the microphone now, and again whenever macOS reports an audio
-- device change -- connecting a headset renumbers the avfoundation indices,
-- which is the whole reason the index is not hardcoded.
resolveDevice()
M.audioWatcher = hs.audiodevice.watcher
M.audioWatcher.setCallback(function() resolveDevice() end)
M.audioWatcher.start()

hs.alert.show("WhisperN ready — hold FN to dictate", 2)

return M
