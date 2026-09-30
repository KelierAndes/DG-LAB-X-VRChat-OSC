# -*- coding: utf-8 -*-
"""One-off patch: Socket V4 continuous-wave model (run from project root)."""
import io

src = io.open('dglab/socket_v4.py', encoding='utf-8').read()

# --- imports + new state -------------------------------------------------
old = '''from .state import EngineState, Slot, StateEvents
from .waves import CoyoteWaveform, OvcWaveform, resolve_wave_frames'''
new = '''from .monitor import WaveMonitor
from .state import EngineState, Slot, StateEvents
from .waves import FrameCycle, resolve_wave_frames'''
assert old in src
src = src.replace(old, new)

old = '''        self._clients: dict[str, dict] = {}  # app clientId -> {"devices": {slotId: dict}}
        self._pending: dict[str, asyncio.Future] = {}'''
new = '''        self._clients: dict[str, dict] = {}  # app clientId -> {"devices": {slotId: dict}}
        self._pending: dict[str, asyncio.Future] = {}

        # Continuous wave model: the client keeps appending ~1 s of frames
        # every WAVE_TICK seconds per device+channel (no one-shot tasks).
        self._cycles: dict[tuple[str, str], FrameCycle] = {}
        self.monitors: dict[str, WaveMonitor] = {}
        self._wave_task: asyncio.Task | None = None
        self._props_logged: set[str] = set()'''
assert old in src
src = src.replace(old, new)

# --- helpers -------------------------------------------------------------
old = '''    # ------------------------------------------------------------ connection
    async def connect(self) -> None:'''
new = '''    # --------------------------------------------------- continuous waves
    def _cycle(self, slot_id: str, channel: str) -> FrameCycle:
        return self._cycles.setdefault((slot_id, channel), FrameCycle())

    def _monitor(self, slot_id: str) -> WaveMonitor:
        return self.monitors.setdefault(slot_id, WaveMonitor())

    def set_wave_frames(self, slot_id: str, channel: str, frames: list[str] | None) -> None:
        """Select (frames) or silence (None) a channel; the sender loops it."""
        self._cycle(slot_id, channel).reset(frames)

    def _slot_wave_frames(self, slot_id: str, device_type: str,
                          channel: str) -> list[str] | None:
        cycle = self._cycles.get((slot_id, channel))
        if cycle is None or not cycle.frames:
            return None
        out = []
        for _ in range(WAVE_FRAMES_PER_TICK):
            out.append(cycle.next_frame())
        return out

    async def _wave_loop(self) -> None:
        """Append one second of frames per active channel every tick."""
        try:
            while not self._closing:
                await asyncio.sleep(WAVE_TICK_S)
                try:
                    await self._wave_tick()
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    self._log(f"波形发送异常: {exc!r}")
        except asyncio.CancelledError:
            pass

    async def _wave_tick(self) -> None:
        for sid in list(self.state.slots):
            slot = self.state.slots.get(sid)
            if slot is None:
                continue
            segs_a: list[int] = [0, 0, 0, 0]
            segs_b: list[int] = [0, 0, 0, 0]
            for ch in ("A", "B"):
                frames = self._slot_wave_frames(sid, slot.type or "COYOTE_030", ch)
                if not frames:
                    continue
                segs = list(bytes.fromhex(frames[0])[4:8])
                if ch == "A":
                    segs_a = segs
                else:
                    segs_b = segs
                cid, _sid = self._require_peer(sid)
                self._send_frame({
                    "type": "message", "clientId": cid,
                    "data": {"t": "req", "reqId": _request_id(),
                             "m": "device.op",
                             "data": {"s": sid, "c": CHANNELS.index(ch),
                                      "t": ACTION_PULSE, "d": WAVE_FRAMES_PER_TICK * 100,
                                      "v": frames}},
                })
            self._monitor(sid).record(segs_a, segs_b)

    async def start_wave_loop(self) -> None:
        if self._wave_task is None or self._wave_task.done():
            self._wave_task = asyncio.create_task(self._wave_loop())

    async def stop_wave_loop(self) -> None:
        if self._wave_task is not None:
            self._wave_task.cancel()
            self._wave_task = None

    # ------------------------------------------------------------ connection
    async def connect(self) -> None:'''
assert old in src
src = src.replace(old, new)

# start wave loop on connect; stop on disconnect
old = '''        self.state.qr_text = build_v4_qr(self.qr_base, self.state.client_id)
        self._publish("等待 App 扫码接入…")
        self._log(f"targetId={self.state.client_id}")'''
new = '''        self.state.qr_text = build_v4_qr(self.qr_base, self.state.client_id)
        self._publish("等待 App 扫码接入…")
        self._log(f"targetId={self.state.client_id}")
        await self.start_wave_loop()'''
assert old in src
src = src.replace(old, new)

old = '''    async def disconnect(self) -> None:
        self._closing = True
        for task in (self._reader_task, self._ping_task):'''
new = '''    async def disconnect(self) -> None:
        self._closing = True
        await self.stop_wave_loop()
        for task in (self._reader_task, self._ping_task):'''
assert old in src
src = src.replace(old, new)

# --- constants -----------------------------------------------------------
old = 'CHANNELS = ("A", "B")'
new = '''CHANNELS = ("A", "B")
# Continuous wave cadence: append 1 s of frames every 0.8 s (queue stays
# shallow, so intensity/estop operations are never stuck behind long tasks).
WAVE_TICK_S = 0.8
WAVE_FRAMES_PER_TICK = 10'''
assert old in src
src = src.replace(old, new)

io.open('dglab/socket_v4.py', 'w', encoding='utf-8').write(src)
print("part 1 OK")
