"""Resolve which microphone to record from, by name rather than by index.

avfoundation indices are positional and renumber whenever a device connects or
disconnects. Every entry point in this repo used to hardcode index "0", and on
a machine with the Microsoft Teams virtual driver installed, index 0 is that
driver -- which yields digital silence (-91 dB), not audio. A recording made
against it produces no speech at all.

Nothing here decides anything clinical. It picks an input device and reports
what it picked.
"""

from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass

# Virtual/loopback drivers that present as inputs but carry no microphone.
# Recording from one yields silence or, worse, whatever another application is
# playing. Matched case-insensitively as substrings.
VIRTUAL_MARKERS = (
    "teams", "zoom", "blackhole", "loopback", "soundflower", "aggregate",
    "multi-output", "obs", "krisp", "virtual", "ndi", "vb-cable", "existential",
)

# Built-in hardware. Anything that is neither built-in nor virtual is treated
# as an external microphone -- a headset, USB mic, or interface.
BUILTIN_MARKERS = (
    "macbook", "built-in", "imac", "mac mini", "mac studio", "mac pro",
)

_LINE = re.compile(r"\[(\d+)\]\s+(.+?)\s*$")


@dataclass(frozen=True)
class InputDevice:
    index: int
    name: str

    @property
    def is_virtual(self) -> bool:
        low = self.name.lower()
        return any(m in low for m in VIRTUAL_MARKERS)

    @property
    def is_builtin(self) -> bool:
        low = self.name.lower()
        return any(m in low for m in BUILTIN_MARKERS)

    @property
    def is_external(self) -> bool:
        return not self.is_virtual and not self.is_builtin

    def __str__(self) -> str:
        return f"[{self.index}] {self.name}"


def parse_devices(ffmpeg_stderr: str) -> list[InputDevice]:
    """Extract the audio input devices from `ffmpeg -list_devices` output.

    Split out from the subprocess call so it can be tested against captured
    output without a microphone.
    """
    devices: list[InputDevice] = []
    in_audio = False
    for line in ffmpeg_stderr.splitlines():
        if "AVFoundation video devices" in line:
            in_audio = False
            continue
        if "AVFoundation audio devices" in line:
            in_audio = True
            continue
        if not in_audio:
            continue
        # Strip the "[AVFoundation indev @ 0x...] " prefix before matching, so
        # the address does not read as a device index.
        stripped = re.sub(r"^\[AVFoundation indev @ [^\]]+\]\s*", "", line)
        m = _LINE.match(stripped)
        if m:
            devices.append(InputDevice(int(m.group(1)), m.group(2)))
        elif stripped.strip():
            in_audio = False  # left the device block
    return devices


def list_input_devices(timeout: float = 10.0) -> list[InputDevice]:
    """Ask ffmpeg what audio inputs exist. Returns [] if it cannot be asked."""
    try:
        r = subprocess.run(
            ["ffmpeg", "-nostdin", "-hide_banner", "-f", "avfoundation",
             "-list_devices", "true", "-i", ""],
            capture_output=True, timeout=timeout,
        )
    except (OSError, subprocess.TimeoutExpired):
        return []
    return parse_devices(r.stderr.decode(errors="replace"))


def choose(devices: list[InputDevice], prefer: str = "headset") -> InputDevice | None:
    """Pick an input device. Virtual drivers are never chosen.

    prefer="headset": an external microphone (headset, USB) wins, then
    built-in. This is the configured default -- a headset sits near the mouth
    and wins on signal-to-noise in a noisy room.

    prefer="builtin": the reverse. Relevant because a Bluetooth headset
    negotiates its microphone over HFP at 16 kHz narrowband, which is a
    compressed voice codec rather than a clean downsample, and may cost
    named-entity accuracy. See docs/deployment.md.
    """
    usable = [d for d in devices if not d.is_virtual]
    if not usable:
        return None
    external = [d for d in usable if d.is_external]
    builtin = [d for d in usable if d.is_builtin]
    order = (external + builtin) if prefer == "headset" else (builtin + external)
    return (order or usable)[0]


def resolve(prefer: str = "headset") -> InputDevice | None:
    return choose(list_input_devices(), prefer)
