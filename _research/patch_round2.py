# -*- coding: utf-8 -*-
import io

src = io.open('dglab/socket_v4.py', encoding='utf-8').read()
old = '''        frames = resolve_wave_frames(waveform, device_type)
        self.set_wave_frames(sid, channel, frames)
        # Flush the channel's queued batch so the new waveform starts now
        # instead of after the in-flight 5 s batch plays out.
        try:
            await self._rpc_or_clear(cid, {"s": sid, "c": CHANNELS.index(channel)})
        except Exception as exc:
            self._log(f"{sid} 波形切换清空失败: {exc!r}")
        await self._wave_tick()  # append the new batch immediately
        self._log(f"{sid} 通道 {channel} 波形切换: {len(frames)} 帧 (持续循环)")'''
new = '''        frames = resolve_wave_frames(waveform, device_type)
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
assert old in src, "set_wave fire-forget"
src = src.replace(old, new)
io.open('dglab/socket_v4.py', 'w', encoding='utf-8').write(src)
print("set_wave fixed")

# --- tests ------------------------------------------------------------------
src = io.open('tests/test_features.py', encoding='utf-8').read()
old = '''    async def test_adaptive_strength_falls_back_to_set(self):
        client = SocketV4Client(events=StateEvents())
        client._handle_frame({"type": "hello", "clientId": "ctrl"})
        client._handle_frame({"type": "client_attached", "clientId": "app"})
        client._replace_devices("app", [{"slotId": "s1", "type": "OVC_1",
                                         "props": {"intensityA": 0}}])
        sent: list[dict] = []

        async def fake_send(frame):
            sent.append(frame)

        client._send_raw = fake_send  # type: ignore[method-assign]
        # No slots.patch arrives -> the add is judged ineffective and the
        # client falls back to the absolute SetIntensity (t=7).
        await client.set_strength("A", 5, slot_id="s1")
        ops = [f["data"]["data"] for f in sent if f["data"].get("m") == "device.op"]
        self.assertEqual(ops[0]["t"], 3)   # relative add first
        self.assertEqual(ops[-1]["t"], 7)  # absolute fallback
        self.assertEqual(ops[-1]["v"], 5)
        self.assertEqual(client._strength_mode["s1"], "set")'''
new = '''    async def test_set_strength_plain_relative_add(self):
        client = SocketV4Client(events=StateEvents())
        client._handle_frame({"type": "hello", "clientId": "ctrl"})
        client._handle_frame({"type": "client_attached", "clientId": "app"})
        client._replace_devices("app", [{"slotId": "s1", "type": "OVC_1",
                                         "props": {"intensityA": 0}}])
        sent: list[dict] = []

        async def fake_send(frame):
            sent.append(frame)

        client._send_raw = fake_send  # type: ignore[method-assign]
        # t=7 v>0 is rejected by the App (invalid_operate) - strength MUST go
        # through relative AddIntensity (t=3) for every device type.
        await client.set_strength("A", 5, slot_id="s1")
        ops = [f["data"]["data"] for f in sent if f["data"].get("m") == "device.op"]
        self.assertEqual(len(ops), 1)
        self.assertEqual(ops[0]["t"], 3)
        self.assertEqual(ops[0]["v"], 5)
        self.assertNotIn("im", ops[0])
        self.assertNotIn("p", ops[0])'''
assert old in src, "adaptive test replace"
src = src.replace(old, new)

old = '''        from dglab.official_waveforms import COYOTE_WAVEFORMS, CoyoteWaveform
        await client.set_wave("A", CoyoteWaveform.BUBBLE, slot_id="s1")
        # one tick = 0.8 s of the sender loop
        await client._wave_tick()

        pulses = [f["data"]["data"] for f in sent if f["data"].get("m") == "device.op"]
        self.assertEqual(len(pulses), 1)
        self.assertEqual(pulses[0]["t"], 0)  # AppendPulseData
        self.assertEqual(pulses[0]["s"], "s1")
        self.assertEqual(pulses[0]["c"], 0)  # channel A
        self.assertEqual(len(pulses[0]["v"]), 10)  # 1 s of frames
        self.assertEqual(pulses[0]["v"][0], COYOTE_WAVEFORMS[CoyoteWaveform.BUBBLE]["raw"][0])

        # channel B idle -> monitor records zeros for B
        _t, a, b = client.monitors["s1"].samples[-1]
        self.assertEqual(len(a), 4)
        self.assertEqual(b, (0, 0, 0, 0))

        # switching to 静默 stops the channel
        await client.clear_wave("A", slot_id="s1")
        sent.clear()
        await client._wave_tick()
        self.assertFalse([f for f in sent if f["data"].get("m") == "device.op"])'''
new = '''        from dglab.official_waveforms import COYOTE_WAVEFORMS, CoyoteWaveform
        await client.set_wave("A", CoyoteWaveform.BUBBLE, slot_id="s1")

        pulses = [f["data"]["data"] for f in sent if f["data"].get("m") == "device.op"]
        clears = [f for f in sent if f["data"].get("m") == "device.op.clear"]
        # switch sends: clear first, then the fresh batch immediately
        self.assertEqual(len(clears), 1)
        self.assertEqual(len(pulses), 1)
        self.assertEqual(pulses[0]["t"], 0)  # AppendPulseData
        self.assertEqual(pulses[0]["s"], "s1")
        self.assertEqual(pulses[0]["c"], 0)  # channel A
        self.assertEqual(len(pulses[0]["v"]), 50)  # 5 s batch
        self.assertEqual(pulses[0]["v"][0], COYOTE_WAVEFORMS[CoyoteWaveform.BUBBLE]["raw"][0])
        # the clear must precede the batch (app queue order matters)
        all_ops = [f["data"].get("m") for f in sent
                   if str(f["data"].get("m", "")).startswith("device.op")]
        self.assertEqual(all_ops, ["device.op.clear", "device.op"])

        # projected monitor samples for the whole batch
        self.assertGreaterEqual(len(client.monitors["s1"].samples), 50)
        _t, a, b = client.monitors["s1"].samples[0]
        self.assertEqual(len(a), 4)
        self.assertEqual(b, (0, 0, 0, 0))  # channel B idle

        # switching to 静默 stops the channel
        await client.clear_wave("A", slot_id="s1")
        sent.clear()
        await client._wave_tick()
        self.assertFalse([f for f in sent if f["data"].get("m") == "device.op"])'''
assert old in src, "wave loop test"
src = src.replace(old, new)
io.open('tests/test_features.py', 'w', encoding='utf-8').write(src)
print("tests updated")
