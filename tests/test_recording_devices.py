"""Microphone selection and music pause/resume.

The bug these exist for: every entry point hardcoded avfoundation index "0",
and on this machine index 0 is the Microsoft Teams virtual driver, which
captures digital silence (-91 dB). Indices are positional and renumber
whenever a device connects, so a recorded index is never trustworthy.
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "src"))

from integrations import media  # noqa: E402
from integrations.audio import InputDevice, choose, parse_devices  # noqa: E402

# Real output from this machine, kept verbatim: the hex address in the prefix
# is the thing a naive index regex trips over.
FFMPEG_OUTPUT = """[AVFoundation indev @ 0x15c804580] AVFoundation video devices:
[AVFoundation indev @ 0x15c804580] [0] FaceTime HD Camera
[AVFoundation indev @ 0x15c804580] [1] Capture screen 0
[AVFoundation indev @ 0x15c804580] AVFoundation audio devices:
[AVFoundation indev @ 0x15c804580] [0] Microsoft Teams Audio
[AVFoundation indev @ 0x15c804580] [1] Bose QuietComfort 35
[AVFoundation indev @ 0x15c804580] [2] MacBook Pro Microphone
"""


def test_parses_audio_devices_only():
    devices = parse_devices(FFMPEG_OUTPUT)
    assert [d.name for d in devices] == [
        "Microsoft Teams Audio", "Bose QuietComfort 35", "MacBook Pro Microphone",
    ]
    assert all("FaceTime" not in d.name for d in devices), "video device leaked in"
    assert [d.index for d in devices] == [0, 1, 2], "hex address parsed as an index"


def test_virtual_driver_is_never_selected():
    """The actual defect. Index 0 is a virtual driver that records silence."""
    devices = parse_devices(FFMPEG_OUTPUT)
    assert devices[0].is_virtual
    for prefer in ("headset", "builtin"):
        assert choose(devices, prefer).index != 0, \
            f"{prefer} selected the silent virtual driver"


def test_headset_preference_picks_the_external_mic():
    devices = parse_devices(FFMPEG_OUTPUT)
    assert choose(devices, "headset").name == "Bose QuietComfort 35"


def test_builtin_preference_picks_the_builtin_mic():
    devices = parse_devices(FFMPEG_OUTPUT)
    assert choose(devices, "builtin").name == "MacBook Pro Microphone"


def test_falls_back_when_the_preferred_kind_is_absent():
    only_builtin = [InputDevice(0, "Microsoft Teams Audio"),
                    InputDevice(1, "MacBook Pro Microphone")]
    assert choose(only_builtin, "headset").name == "MacBook Pro Microphone"

    only_external = [InputDevice(0, "Bose QuietComfort 35")]
    assert choose(only_external, "builtin").name == "Bose QuietComfort 35"


def test_no_usable_device_returns_none():
    """Better to refuse than to record silence from a virtual driver."""
    assert choose([], "headset") is None
    assert choose([InputDevice(0, "BlackHole 2ch"),
                   InputDevice(1, "Microsoft Teams Audio")], "headset") is None


def test_pause_returns_only_players_that_were_playing(monkeypatch):
    """Resuming a player that was already paused would start music the user
    had deliberately stopped."""
    monkeypatch.setattr(media, "_osa",
                        lambda script, **kw: "paused" if "Spotify" in script else "idle")
    assert media.pause_playing() == ["Spotify"]


def test_pause_ignores_players_that_are_not_running(monkeypatch):
    monkeypatch.setattr(media, "_osa", lambda script, **kw: "absent")
    assert media.pause_playing() == []


def test_resume_touches_only_what_was_paused(monkeypatch):
    called: list[str] = []

    def fake(script, **kw):
        called.append("Spotify" if "Spotify" in script else "Music")
        return "resumed"

    monkeypatch.setattr(media, "_osa", fake)
    media.resume(["Spotify"])
    assert called == ["Spotify"], "resumed a player it never paused"


def test_resume_of_nothing_is_a_no_op(monkeypatch):
    monkeypatch.setattr(media, "_osa",
                        lambda *a, **k: pytest_fail("must not run any script"))
    media.resume([])


def pytest_fail(msg):  # tiny helper so the lambda above stays readable
    raise AssertionError(msg)
