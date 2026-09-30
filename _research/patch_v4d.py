# -*- coding: utf-8 -*-
"""V4 rework: drop the adaptive/nightmare watchdog, plain adds, 5s batches."""
import io

src = io.open('dglab/socket_v4.py', encoding='utf-8').read()

# --- remove adaptive set_strength + mode tracking --------------------------
start = src.index('    async def set_strength(self, channel: str, value: int, slot_id: str | None = None) -> None:')
end = src.index('    # ------------------------------------------------------------ public ops')
src = src[:start] + src[end:]

old = '''        self._wave_task: asyncio.Task | None = None
        self._props_logged: set[str] = set()
        self._strength_mode: dict[str, str] = {}  # slotId -> "add" | "set"'''
new = '''        self._wave_task: asyncio.Task | None = None
        self._props_logged: set[str] = set()'''
assert old in src, "mode field"
src = src.replace(old, new)

# --- set_strength: plain relative add (proven to work for both devices) ----
old = '''    # ------------------------------------------------------------ public ops
    def select_slot(self, slot_id: str) -> None:'''
new = '''    async def set_strength(self, channel: str, value: int, slot_id: str | None = None) -> None:
        """Absolute strength via relative AddIntensity (t=3) - the only op
        the App accepts for strength (t=7 v>0 returns invalid_operate)."""
        cid, sid = self._require_peer(slot_id)
        slot = self.state.slots.get(sid)
        if slot is None:
            raise RuntimeError(f"设备不存在: {sid}")
        value = max(0, min(200, int(value)))
        delta = value - slot.strength.get(channel, 0)
        if delta:
            await self.add_intensity(channel, delta, slot_id=sid)

    # ------------------------------------------------------------ public ops
    def select_slot(self, slot_id: str) -> None:'''
assert old in src, "set_strength"
src = src.replace(old, new)

# --- wave batching: 5 s every 4.6 s ----------------------------------------
old = '''CHANNELS = ("A", "B")
# Continuous wave cadence: append 1 s of frames every 0.8 s (queue stays
# shallow, so intensity/estop operations are never stuck behind long tasks).
WAVE_TICK_S = 0.8
WAVE_FRAMES_PER_TICK = 10'''
new = '''CHANNELS = ("A", "B")
# Continuous wave cadence: the App processes device.op tasks serially, so
# per-second tasks flood its queue (observed: strength ops delayed 53 s).
# Append 5 s of frames every 4.6 s instead - one task per 5 s per device.
WAVE_TICK_S = 4.6
WAVE_FRAMES_PER_TICK = 50'''
assert old in src, "batch consts"
src = src.replace(old, new)

# --- wave tick: projected-timestamp sampling --------------------------------
old = '''    async def _wave_tick(self) -> None:
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
                await self._send_raw({
                    "type": "message", "clientId": cid,
                    "data": {"t": "req", "reqId": _request_id(),
                             "m": "device.op",
                             "data": {"s": sid, "c": CHANNELS.index(ch),
                                      "t": ACTION_PULSE, "d": WAVE_FRAMES_PER_TICK * 100,
                                      "v": frames}},
                })
            self._monitor(sid).record(segs_a, segs_b)'''
new = '''    async def _wave_tick(self) -> None:
        import time as _time

        for sid in list(self.state.slots):
            slot = self.state.slots.get(sid)
            if slot is None:
                continue
            batches: dict[str, list[str]] = {}
            for ch in ("A", "B"):
                frames = self._slot_wave_frames(sid, slot.type or "COYOTE_030", ch)
                if frames:
                    batches[ch] = frames
                    cid, _sid = self._require_peer(sid)
                    await self._send_raw({
                        "type": "message", "clientId": cid,
                        "data": {"t": "req", "reqId": _request_id(),
                                 "m": "device.op",
                                 "data": {"s": sid, "c": CHANNELS.index(ch),
                                          "t": ACTION_PULSE,
                                          "d": WAVE_FRAMES_PER_TICK * 100,
                                          "v": frames}},
                    })
            # Record projected samples so the live chart shows the batch as
            # it will actually be played out over the next 5 seconds.
            monitor = self._monitor(sid)
            now = _time.monotonic()
            for i in range(WAVE_FRAMES_PER_TICK):
                segs_a = list(bytes.fromhex(batches["A"][i])[4:8]) if "A" in batches \
                    else [0, 0, 0, 0]
                segs_b = list(bytes.fromhex(batches["B"][i])[4:8]) if "B" in batches \
                    else [0, 0, 0, 0]
                monitor.record_at(now + i * 0.1, segs_a, segs_b)'''
assert old in src, "wave tick"
src = src.replace(old, new)

# --- set_wave: immediate switch (clear channel queue + fresh batch) --------
old = '''        frames = resolve_wave_frames(waveform, device_type)
        self.set_wave_frames(sid, channel, frames)
        self._log(f"{sid} 通道 {channel} 波形切换: {len(frames)} 帧 (持续循环)")'''
new = '''        frames = resolve_wave_frames(waveform, device_type)
        self.set_wave_frames(sid, channel, frames)
        # Flush the channel's queued batch so the new waveform starts now
        # instead of after the in-flight 5 s batch plays out.
        try:
            await self._rpc_or_clear(cid, {"s": sid, "c": CHANNELS.index(channel)})
        except Exception as exc:
            self._log(f"{sid} 波形切换清空失败: {exc!r}")
        await self._wave_tick()  # append the new batch immediately
        self._log(f"{sid} 通道 {channel} 波形切换: {len(frames)} 帧 (持续循环)")'''
assert old in src, "set_wave switch"
src = src.replace(old, new)

io.open('dglab/socket_v4.py', 'w', encoding='utf-8').write(src)
print("v4 rework OK")
