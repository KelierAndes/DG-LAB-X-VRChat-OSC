"""Real-time waveform sampling for the live charts.

Each output backend records, once per output tick (100 ms), the four 25 ms
strength segments actually being sent per channel.  The UI renders the last
~5 s as a scrolling time/strength bar chart (right edge = now).
"""
from __future__ import annotations

import time
from collections import deque


class WaveMonitor:
    """Ring buffer of per-tick channel strength segments."""

    def __init__(self, maxlen: int = 160):
        self.samples: deque[tuple[float, tuple, tuple]] = deque(maxlen=maxlen)

    def record(self, segs_a, segs_b) -> None:
        self.record_at(time.monotonic(), segs_a, segs_b)

    def record_at(self, t: float, segs_a, segs_b) -> None:
        """Record with an explicit timestamp (batch play-out projection)."""
        self.samples.append((t, tuple(segs_a), tuple(segs_b)))

    def window(self, seconds: float = 5.0) -> list[tuple[float, tuple, tuple]]:
        cutoff = time.monotonic() - seconds
        return [s for s in self.samples if s[0] >= cutoff]

    def last_strength(self) -> tuple[int, int]:
        """(A, B) strength of the most recent tick (max of its 4 segments)."""
        if not self.samples:
            return 0, 0
        _t, a, b = self.samples[-1]
        return (max(a) if a else 0, max(b) if b else 0)
