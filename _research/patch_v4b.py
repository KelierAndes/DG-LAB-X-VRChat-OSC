# -*- coding: utf-8 -*-
"""Part 2: V4 public ops rewrite (continuous waves, +/- intensity, estop, fire)."""
import io
import re

src = io.open('dglab/socket_v4.py', encoding='utf-8').read()

# --- replace public ops block --------------------------------------------
start = src.index('    # ------------------------------------------------------------ public ops')
end = src.index('def _apply_props(slot: Slot, props: dict) -> None:')
new_ops = '''    # ------------------------------------------------------------ public ops
    def select_slot(self, slot_id: str) -> None:
        if slot_id in self.state.slots:
            self.state.active_slot = slot_id
            self._publish()

    async def add_intensity(self, channel: str, value: float,
                            slot_id: str | None = None) -> None:
        cid, sid = self._require_peer(slot_id)
        await self._operate(
            {"s": sid, "c": CHANNELS.index(channel), "t": ACTION_ADD, "v": value,
             "im": True, "p": 2}
        )

    async def set_temp_intensity(self, channel: str, value: float, duration_ms: int,
                                 slot_id: str | None = None) -> None:
        cid, sid = self._require_peer(slot_id)
        await self._operate(
            {"s": sid, "c": CHANNELS.index(channel), "t": ACTION_TEMP, "v": value,
             "d": duration_ms, "p": 2}
        )

    async def reset_intensity(self, channel: str | None = None, slot_id: str | None = None) -> None:
        cid, sid = self._require_peer(slot_id)
        for ch in CHANNELS:
            if channel in (None, ch):
                await self._operate(
                    {"s": sid, "c": CHANNELS.index(ch), "t": ACTION_RESET, "v": 0,
                     "im": True, "p": 2}
                )

    async def set_wave(
        self,
        channel: str,
        waveform: "CoyoteWaveform | OvcWaveform | str | list[str]",
        duration_s: float = 10.0,
        slot_id: str | None = None,
    ) -> None:
        """Select a continuously-looping waveform (no one-shot task)."""
        cid, sid = self._require_peer(slot_id)
        device_type = "COYOTE_030"
        slot = self.state.slots.get(sid)
        if slot is not None and slot.type:
            device_type = slot.type
        frames = resolve_wave_frames(waveform, device_type)
        self.set_wave_frames(sid, channel, frames)
        self._log(f"{sid} 通道 {channel} 波形切换: {len(frames)} 帧 (持续循环)")

    async def clear_wave(self, channel: str | None = None, slot_id: str | None = None) -> None:
        """Silence a channel/device and flush the app's pulse queue."""
        cid, sid = self._require_peer(slot_id)
        if channel:
            self.set_wave_frames(sid, channel, None)
            await self._rpc_or_clear(cid, {"s": sid, "c": CHANNELS.index(channel)})
        elif slot_id:
            self.set_wave_frames(sid, "A", None)
            self.set_wave_frames(sid, "B", None)
            await self._rpc_or_clear(cid, {"s": sid})
        else:
            for (s, _c) in list(self._cycles):
                self._cycles[(s, _c)].reset([])
            await self._rpc_or_clear(cid, None)

    async def fire(self, slot_id: str | None = None, duration_ms: int = 1000,
                   value: float | None = None) -> None:
        """一键开火: temporary intensity burst on both channels."""
        cid, sid = self._require_peer(slot_id)
        slot = self.state.slots.get(sid)
        for ch in CHANNELS:
            cap = 200
            if slot is not None:
                cap = slot.strength_limit.get(ch, 200)
            v = min(value, cap) if value is not None else cap
            await self.set_temp_intensity(ch, v, duration_ms, slot_id=sid)
        self._log(f"{sid} 一键开火 {duration_ms}ms (强度 {value or '通道上限'})")

    async def emergency_stop(self) -> None:
        """Stop the wave sender, flush every queue, zero every channel."""
        await self.stop_wave_loop()
        for (s, _c) in list(self._cycles):
            self._cycles[(s, _c)].reset([])
        cid = self._active_client_id()
        if cid:
            try:
                await self._rpc_or_clear(cid, None)  # clear EVERYTHING
            except Exception as exc:
                self._log(f"急停清空队列失败: {exc!r}")
            for sid in list(self.state.slots):
                for ch in CHANNELS:
                    try:
                        await self._operate(
                            {"s": sid, "c": CHANNELS.index(ch), "t": ACTION_RESET,
                             "v": 0, "im": True, "p": 2}
                        )
                    except Exception as exc:
                        self._log(f"急停 {sid}/{ch} 清零失败: {exc!r}")
        await self.start_wave_loop()  # keep monitoring/sending zeros
        self._log(f"急停已执行 ({len(self.state.slots)} 台设备强度清零 + 队列清空)")

    async def _rpc_or_clear(self, cid: str, data: dict | None) -> Any:
        req_id = _request_id()
        req: dict[str, Any] = {"t": "req", "reqId": req_id, "requestId": req_id, "m": "device.op.clear"}
        if data:
            req["data"] = data
        fut: asyncio.Future = asyncio.get_running_loop().create_future()
        self._pending[req_id] = fut
        try:
            await self._send_raw({"type": "message", "clientId": cid, "data": req})
            return await asyncio.wait_for(fut, timeout=RESPONSE_TIMEOUT)
        except asyncio.TimeoutError:
            # The app stops sending wave data even without a response frame.
            self._log("清除指令等待响应超时 (队列清空指令已送达)")
            return None
        finally:
            self._pending.pop(req_id, None)


'''
src = src[:start] + new_ops + src[end:]

# --- remove leftover emergency_stop/_rpc_or_clear duplicates if any ------
# (the slice above already replaced everything between the markers)

# --- battery prop aliases + props debug log ------------------------------
old = '''def _apply_props(slot: Slot, props: dict) -> None:
    """Extract known device props into typed fields.

    Covers COYOTE_020/030 (e-stim), OVC_1 (负鼠振动, channel status is a
    bool) and BMTR_1 (灵猫气压传感器).
    """
    a = props.get("intensityA")
    b = props.get("intensityB")
    if isinstance(a, (int, float)):
        slot.strength["A"] = int(a)
    if isinstance(b, (int, float)):
        slot.strength["B"] = int(b)
    power = props.get("power")
    if isinstance(power, (int, float)):
        slot.battery = int(power)'''
new = '''def _apply_props(slot: Slot, props: dict) -> None:
    """Extract known device props into typed fields.

    Covers COYOTE_020/030 (e-stim), OVC_1 (负鼠振动, channel status is a
    bool) and BMTR_1 (灵猫气压传感器).
    """
    a = props.get("intensityA")
    b = props.get("intensityB")
    if isinstance(a, (int, float)):
        slot.strength["A"] = int(a)
    if isinstance(b, (int, float)):
        slot.strength["B"] = int(b)
    power = None
    for key in ("power", "battery", "batteryLevel", "battery_level"):
        value = props.get(key)
        if isinstance(value, (int, float)):
            power = value
            break
    if power is not None:
        slot.battery = int(power)'''
assert old in src
src = src.replace(old, new)

# debug log first props snapshot without battery
old = '''    def _sync_state(self, cid: str) -> None:'''
new = '''    def _debug_props_once(self, sid: str, slot: Slot) -> None:
        if sid in self._props_logged or slot.battery is not None:
            return
        self._props_logged.add(sid)
        self._log(f"调试 {sid} props={slot.props!r} slotState keys={list(slot.slot_state)}")

    def _sync_state(self, cid: str) -> None:'''
assert old in src
src = src.replace(old, new)

old = '''        self.state.slots = {sid: self._slot_from_device(d) for sid, d in entry["devices"].items()}
        if self.state.active_slot not in self.state.slots:
            self.state.active_slot = next(iter(self.state.slots), "")
        self._publish()'''
new = '''        self.state.slots = {sid: self._slot_from_device(d) for sid, d in entry["devices"].items()}
        if self.state.active_slot not in self.state.slots:
            self.state.active_slot = next(iter(self.state.slots), "")
        for sid, slot in self.state.slots.items():
            self._debug_props_once(sid, slot)
        self._publish()'''
assert old in src
src = src.replace(old, new)

io.open('dglab/socket_v4.py', 'w', encoding='utf-8').write(src)
print("part 2 OK")
