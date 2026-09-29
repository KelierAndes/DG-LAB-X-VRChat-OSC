"""Waveform helpers shared by all DG-Lab backends.

Three waveform encodings exist across the protocol family:

* **V3 frame (hex)** - 8 bytes ``[f1 f2 f3 f4][s1 s2 s3 s4]``: four 25 ms
  segments of frequency bytes (10-240) and strength bytes (0-100).  Used by
  Socket V3 ``pulse-A:["hex",...]``, Socket V4 ``AppendPulseData`` frames and
  directly mapped into the BLE V3 ``B0`` frame.
* **BLE V3 B0** - packs two such groups (A and B channels) plus strength into
  a 20 byte frame written every 100 ms.
* **BLE V2 XYZ** - X (pulses per burst, 5 bits) / Y (gap ms, 10 bits) /
  Z (pulse width *5 us, 5 bits), refreshed every 100 ms.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

from .official_waveforms import COYOTE_WAVEFORMS, CoyoteWaveform
from .official_waveforms_ovc import OVC_WAVEFORMS, OvcWaveform

__all__ = [
    "SILENT",
    "SILENT_FRAMES",
    "COYOTE_WAVEFORMS",
    "CoyoteWaveform",
    "OVC_WAVEFORMS",
    "OvcWaveform",
    "WIRE_FREQ_MIN",
    "WIRE_FREQ_MAX",
    "logical_to_wire_freq",
    "wire_to_logical_freq",
    "frequency_to_xy",
    "parse_frame",
    "build_frame",
    "frames_duration_ms",
    "cycle_frame",
    "FrameCycle",
    "ovc_channel_pattern",
    "waveform_dict_for",
]

WIRE_FREQ_MIN = 10
WIRE_FREQ_MAX = 240


def logical_to_wire_freq(logical: int) -> int:
    """Map the logical frequency 10-1000 onto the wire byte 10-240.

    Piecewise mapping from the official Bluetooth V3 protocol doc:
    10-100 as-is; 101-600 -> (v-100)/5+100; 601-1000 -> (v-600)/10+200.
    """
    logical = max(10, min(1000, int(logical)))
    if logical <= 100:
        return logical
    if logical <= 600:
        return (logical - 100) // 5 + 100
    return (logical - 600) // 10 + 200


def wire_to_logical_freq(wire: int) -> int:
    """Inverse of :func:`logical_to_wire_freq`."""
    wire = max(WIRE_FREQ_MIN, min(WIRE_FREQ_MAX, int(wire)))
    if wire <= 100:
        return wire
    if wire <= 200:
        return (wire - 100) * 5 + 100
    return (wire - 200) * 10 + 600


def frequency_to_xy(logical_freq: int) -> tuple[int, int]:
    """Best X/Y split for the BLE V2 protocol (X pulses + Y ms gap)."""
    freq = max(10, min(1000, int(logical_freq)))
    x = max(1, round(math.sqrt(freq / 1000) * 15))
    y = max(0, freq - x)
    return x, y


def parse_frame(frame: str) -> tuple[list[int], list[int]]:
    """Hex frame -> (freqs[4], strengths[4])."""
    frame = frame.strip().lower()
    if len(frame) != 16:
        raise ValueError(f"frame must be 16 hex chars, got {len(frame)}: {frame!r}")
    raw = bytes.fromhex(frame)
    return list(raw[:4]), list(raw[4:])


def build_frame(freqs, strengths) -> str:
    """(freqs[4], strengths[4]) -> hex frame; values are clamped."""
    out = bytearray()
    for f in freqs:
        out.append(max(0, min(255, int(f))))
    for s in strengths:
        out.append(max(0, min(255, int(s))))
    return out.hex()


def frames_duration_ms(frames: list[str]) -> int:
    """One frame per ~100 ms per the official docs."""
    return len(frames) * 100


def cycle_frame(frames: list[str], tick: int) -> str:
    """Frame for playback tick (each tick = 100 ms), looping."""
    if not frames:
        return build_frame([WIRE_FREQ_MIN] * 4, [0] * 4)
    return frames[tick % len(frames)]


@dataclass
class FrameCycle:
    """Looping playback position of a waveform per channel."""

    frames: list[str] = None  # type: ignore[assignment]
    tick: int = 0

    def __post_init__(self):
        if self.frames is None:
            self.frames = []

    def next_frame(self) -> str:
        frame = cycle_frame(self.frames, self.tick)
        self.tick = (self.tick + 1) % max(1, len(self.frames))
        return frame

    def reset(self, frames: list[str] | None = None) -> None:
        if frames is not None:
            self.frames = list(frames)
        self.tick = 0


def ovc_channel_pattern(frame: str) -> list[int]:
    """OVC frames carry the vibration pattern in the last 4 bytes (0-100)."""
    raw = bytes.fromhex(frame)
    return list(raw[4:8])


def waveform_dict_for(device_type: str) -> dict:
    """Official waveform set matching a V4 device type."""
    if device_type.upper().startswith("OVC"):
        return OVC_WAVEFORMS
    return COYOTE_WAVEFORMS


def resolve_wave_frames(waveform: "CoyoteWaveform | OvcWaveform | str | list[str]",
                        device_type: str = "COYOTE_030") -> list[str]:
    """Resolve a waveform name/enum into raw frames for the device type.

    Accepts a frame list verbatim, the CONTINUOUS preset key, or an official
    waveform name (looked up in the set matching ``device_type``).
    """
    if isinstance(waveform, list):
        return list(waveform)
    if waveform == SILENT:
        return list(SILENT_FRAMES)
    if waveform == CONTINUOUS:
        if device_type.upper().startswith("OVC"):
            return [build_frame([0x0A] * 4, [100] * 4)]
        return list(CONTINUOUS_FRAMES)
    table = waveform_dict_for(device_type)
    return list(table[waveform]["raw"])  # type: ignore[index]


# Extra preset keys for steady output / silence (not in the official sets).
CONTINUOUS = "__CONTINUOUS__"
SILENT = "__SILENT__"
# Coyote: minimal frequency, full strength; OVC variant built on demand.
CONTINUOUS_FRAMES = [build_frame([40, 40, 40, 40], [100, 100, 100, 100])]
# Silent: a real, continuously-fed waveform whose strength bytes are all
# zero - keeps the App's wave session alive (intensity state persists)
# while producing no output.
SILENT_FRAMES = [build_frame([10, 10, 10, 10], [0, 0, 0, 0])]
