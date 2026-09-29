from __future__ import annotations

import time
from collections import deque


class WaveMonitor:
    def __init__(self, maxlen: int = 160):
        self.samples: deque[tuple[float, tuple, tuple]] = deque(maxlen=maxlen)

    def record(self, segs_a, segs_b) -> None:
        self.record_at(time.monotonic(), segs_a, segs_b)

    def record_at(self, t: float, segs_a, segs_b) -> None:
        self.samples.append((t, tuple(segs_a), tuple(segs_b)))

    def window(self, seconds: float = 5.0) -> list[tuple[float, tuple, tuple]]:
        cutoff = time.monotonic() - seconds
        return [s for s in self.samples if s[0] >= cutoff]

    def last_strength(self) -> tuple[int, int]:
        if not self.samples:
            return 0, 0
        _t, a, b = self.samples[-1]
        return (max(a) if a else 0, max(b) if b else 0)
