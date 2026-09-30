# -*- coding: utf-8 -*-
import io

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

io.open('dglab/socket_v4.py', 'w', encoding='utf-8').write(src)
print("cadence + no-clear OK")
