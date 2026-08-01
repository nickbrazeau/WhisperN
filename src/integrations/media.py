"""Pause music while dictating, and resume exactly what was paused.

State-aware on purpose: each player is asked whether it is actually playing
before it is touched, and only those players are resumed afterwards. A
fire-and-forget play/pause toggle would start music that was never playing.

Never launches a player. `tell application "Spotify" to ...` will *launch*
Spotify if it is not running, so every script checks System Events for a live
process first.

Nothing here touches audio, transcripts, or clinical text. It reads one bit
per player -- playing or not -- and that bit is never logged or persisted.
"""

from __future__ import annotations

import subprocess

# AppleScript-controllable players. Browser audio is deliberately excluded:
# it can only be nudged with a media key, which cannot be read back, so resume
# would be a guess.
PLAYERS = ("Spotify", "Music")


def _osa(script: str, timeout: float = 5.0) -> str:
    try:
        r = subprocess.run(["osascript", "-e", script],
                           capture_output=True, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired):
        return ""
    if r.returncode != 0:
        return ""
    return r.stdout.decode(errors="replace").strip()


def _pause_script(app: str) -> str:
    # The `is running` guard is what keeps this from launching the app.
    return f'''
    tell application "System Events"
      if (count of (every process whose name is "{app}")) = 0 then return "absent"
    end tell
    tell application "{app}"
      if player state is playing then
        pause
        return "paused"
      end if
    end tell
    return "idle"
    '''


def _resume_script(app: str) -> str:
    return f'''
    tell application "System Events"
      if (count of (every process whose name is "{app}")) = 0 then return "absent"
    end tell
    tell application "{app}" to play
    return "resumed"
    '''


def pause_playing() -> list[str]:
    """Pause every player that is currently playing. Returns those paused.

    Hand the return value to resume(). Players that were already paused, or
    not running, are absent from it and will not be started later.
    """
    return [app for app in PLAYERS if _osa(_pause_script(app)) == "paused"]


def resume(apps: list[str]) -> None:
    """Resume only the players that pause_playing() actually paused."""
    for app in apps:
        _osa(_resume_script(app))
