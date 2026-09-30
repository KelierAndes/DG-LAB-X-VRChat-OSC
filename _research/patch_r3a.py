# -*- coding: utf-8 -*-
"""Round-3 fixes based on device logs."""
import io

# ============ 1) socket_v4: revert cadence, no clear on switch ============
src = io.open('dglab/socket_v4.py', encoding='utf-8').read()

old = '''CHANNELS = ("A", "B")
# Continuous wave cadence: the App processes device.op tasks serially, so
# per-second tasks flood its queue (observed: strength ops delayed 53 s).
# Append 5 s of frames every 4.6 s instead - one task per 5 s per device.
WAVE_TICK_S = 4.6
WAVE_FRAMES_PER_TICK = 50'''
new = '''CHANNELS = ("A", "B")
# Continuous wave cadence (device-log verified): the App streams frames to
# the device at ~100 ms/frame; 10-frame (1 s) batches every 0.8 s kept the
# device output continuous (channelAStatus=2), while 50-frame batches
# stalled it (channelAStatus=0).  Keep the proven cadence.
WAVE_TICK_S = 0.8
WAVE_FRAMES_PER_TICK = 10'''
assert old in src, "cadence"
src = src.replace(old, new)

old = '''        frames = resolve_wave_frames(waveform, device_type)
        self.set_wave_frames(sid, channel, frames)
        # Flush the channel's queued batch so the new waveform starts now.
        # Fire-and-forget: the fresh batch must FOLLOW the clear in the
        # app's serial queue, not wait for the clear's response.
        try:
            await self._send_raw({
                "type": "message", "clientId": cid,
                "data": {"t": "req", "reqId": _request_id(),
                         "m": "device.op.clear",
                         "data": {"s": sid, "c": CHANNELS.index(channel)}},
            })
        except Exception as exc:
            self._log(f"{sid} 波形切换清空失败: {exc!r}")
        await self._wave_tick()  # append the new batch immediately
        self._log(f"{sid} 通道 {channel} 波形切换: {len(frames)} 帧 (持续循环)")'''
new = '''        frames = resolve_wave_frames(waveform, device_type)
        self.set_wave_frames(sid, channel, frames)
        # Switch WITHOUT device.op.clear - the clear wipes the channel's
        # intensity state on the App side.  im=true replaces the in-flight
        # wave batch instead, so the new waveform starts immediately.
        try:
            await self._send_batch(sid, channel, frames, immediate=True)
        except Exception as exc:
            self._log(f"{sid} 波形切换失败: {exc!r}")
        self._log(f"{sid} 通道 {channel} 波形切换: {len(frames)} 帧 (持续循环)")'''
assert old in src, "set_wave no-clear"
src = src.replace(old, new)

# _send_batch helper + tick uses it
old = '''    async def _wave_tick(self) -> None:
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
                segs_a = list(bytes.fromhex(batches["A"][i])[4:8]) if "A" in batches \\
                    else [0, 0, 0, 0]
                segs_b = list(bytes.fromhex(batches["B"][i])[4:8]) if "B" in batches \\
                    else [0, 0, 0, 0]
                monitor.record_at(now + i * 0.1, segs_a, segs_b)'''
new = '''    async def _send_batch(self, sid: str, channel: str, frames: list[str],
                          immediate: bool = False) -> None:
        cid, _sid = self._require_peer(sid)
        payload = {"s": sid, "c": CHANNELS.index(channel), "t": ACTION_PULSE,
                   "d": WAVE_FRAMES_PER_TICK * 100, "v": frames}
        if immediate:
            payload["im"] = True  # replace the in-flight batch
        await self._send_raw({
            "type": "message", "clientId": cid,
            "data": {"t": "req", "reqId": _request_id(), "m": "device.op",
                     "data": payload},
        })

    async def _wave_tick(self) -> None:
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
                    await self._send_batch(sid, ch, frames)
            # Record projected samples so the live chart shows the batch as
            # it will actually be played out over the next seconds.
            monitor = self._monitor(sid)
            now = _time.monotonic()
            for i in range(WAVE_FRAMES_PER_TICK):
                segs_a = list(bytes.fromhex(batches["A"][i])[4:8]) if "A" in batches \\
                    else [0, 0, 0, 0]
                segs_b = list(bytes.fromhex(batches["B"][i])[4:8]) if "B" in batches \\
                    else [0, 0, 0, 0]
                monitor.record_at(now + i * 0.1, segs_a, segs_b)'''
assert old in src, "send batch"
src = src.replace(old, new)

io.open('dglab/socket_v4.py', 'w', encoding='utf-8').write(src)
print("v4 round3 OK")
