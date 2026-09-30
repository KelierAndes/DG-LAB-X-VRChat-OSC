# -*- coding: utf-8 -*-
"""BLE strength rework: desired-target model with resend-until-confirmed."""
import io

src = io.open('dglab/ble.py', encoding='utf-8').read()

# session fields
old = '''        self._actual = {"A": 0, "B": 0}
        self._pending: dict[str, int] = {}
        self._awaiting_seq = 0
        self._await_since: int | None = None  # writer tick when seq was set'''
new = '''        # Desired strength per channel; the writer resends the absolute-set
        # command every tick until the device's report confirms the value -
        # dropped writes or missed echoes self-heal instead of dead-locking.
        self._targets = {"A": 0, "B": 0}
        self._sent: dict[str, int | None] = {"A": None, "B": None}
        self._actual = {"A": 0, "B": 0}  # last device-confirmed value
        self._awaiting_seq = 0
        self._await_since: int | None = None  # writer tick when seq was set'''
assert old in src, "session fields"
src = src.replace(old, new)

# coyote notify: device report authoritative when it differs from targets
old = '''        # Coyote V3
        if head == 0xB1 and len(data) >= 4:
            seq = data[1]
            session._actual = {"A": int(data[2]), "B": int(data[3])}
            self._slot(session).strength = dict(session._actual)
            if seq == session._awaiting_seq and seq != 0:
                session._awaiting_seq = 0
                session._await_since = None
            self._publish()'''
new = '''        # Coyote V3
        if head == 0xB1 and len(data) >= 4:
            seq = data[1]
            session._actual = {"A": int(data[2]), "B": int(data[3])}
            # Device report authoritative (physical knob changes included):
            # adopt it when it diverges from what we commanded.
            if session._actual != session._targets:
                session._targets = dict(session._actual)
            self._slot(session).strength = dict(session._actual)
            if seq == session._awaiting_seq and seq != 0:
                session._awaiting_seq = 0
                session._await_since = None
            self._publish()'''
assert old in src, "B1 notify"
src = src.replace(old, new)

# OVC notify
old = '''        if session.kind == "ovc":
            if head == 0xB3 and len(data) >= 3:
                session._actual = {"A": int(data[1]), "B": int(data[2])}
                self._slot(session).strength = dict(session._actual)
                self._publish()'''
new = '''        if session.kind == "ovc":
            if head == 0xB3 and len(data) >= 3:
                session._actual = {"A": int(data[1]), "B": int(data[2])}
                if session._actual != session._targets:
                    session._targets = dict(session._actual)
                self._slot(session).strength = dict(session._actual)
                self._publish()'''
assert old in src, "OVC notify"
src = src.replace(old, new)

# V2 notify
old = '''        value = int.from_bytes(data[:3], "big")
        session._actual = {"A": (value >> 11) // 7, "B": (value & 0x7FF) // 7}
        self._slot(session).strength = dict(session._actual)
        self._publish()'''
new = '''        value = int.from_bytes(data[:3], "big")
        session._actual = {"A": (value >> 11) // 7, "B": (value & 0x7FF) // 7}
        if session._actual != session._targets:
            session._targets = dict(session._actual)
        self._slot(session).strength = dict(session._actual)
        self._publish()'''
assert old in src, "V2 notify"
src = src.replace(old, new)

# coyote channel fields: resend until confirmed
old = '''    def _v3_channel_fields(self, session: BleSession, ch: str) -> tuple[list[int], list[int], int, int]:
        freqs = [10, 10, 10, 10]
        strengths = [0, 0, 0, 0]
        cycle = session._cycles[ch]
        if cycle.frames:
            frame = cycle.next_frame()
            raw = bytes.fromhex(frame)
            freqs, strengths = list(raw[:4]), list(raw[4:])
        method_ch = 0b00
        strength_byte = session._actual[ch]
        # B1-ack timeout: if the device never echoes the sequence (e.g. the
        # strength did not actually change), stop blocking after ~1 s so
        # later slider moves still apply.
        if (
            session._awaiting_seq != 0
            and session._await_since is not None
            and session.ticks - session._await_since > 10
        ):
            self._log(f"{session.slot_id} B1 应答超时，解除强度修改锁定")
            session._awaiting_seq = 0
            session._await_since = None
        if ch in session._pending and session._awaiting_seq == 0:
            target = max(0, min(self.soft_limits[ch], session._pending.pop(ch)))
            strength_byte = target
            method_ch = 0b11 if target != session._actual[ch] else 0b00
        return freqs, strengths, method_ch, strength_byte'''
new = '''    def _v3_channel_fields(self, session: BleSession, ch: str) -> tuple[list[int], list[int], int, int]:
        freqs = [10, 10, 10, 10]
        strengths = [0, 0, 0, 0]
        cycle = session._cycles[ch]
        if cycle.frames:
            frame = cycle.next_frame()
            raw = bytes.fromhex(frame)
            freqs, strengths = list(raw[:4]), list(raw[4:])
        method_ch = 0b00
        strength_byte = session._targets[ch]
        # Resend the absolute-set until the device confirms the value
        # (B1 report); dropouts self-heal on the next 100 ms tick.
        if session._targets[ch] != session._sent[ch]:
            target = max(0, min(self.soft_limits[ch], session._targets[ch]))
            strength_byte = target
            method_ch = 0b11
            session._sent[ch] = target
        return freqs, strengths, method_ch, strength_byte'''
assert old in src, "channel fields"
src = src.replace(old, new)

# ovc writer: resend B3/B2 while targets differ from screen
old = '''        await self._write(session, build_ovc_b0(wave_a, wave_b))
        session.monitor.record(wave_a, wave_b)
        if session._pending:
            a = session._pending.pop("A", session._actual["A"])
            b = session._pending.pop("B", session._actual["B"])
            a, b = max(0, min(200, a)), max(0, min(200, b))
            session._screen = {"A": a, "B": b}
            await self._write(session, build_ovc_b3(a, b))
            await self._write(session, build_ovc_b2(a, b))'''
new = '''        await self._write(session, build_ovc_b0(wave_a, wave_b))
        session.monitor.record(wave_a, wave_b)
        if (session._targets["A"], session._targets["B"]) != (
            session._screen.get("A"), session._screen.get("B")
        ):
            a = max(0, min(200, session._targets["A"]))
            b = max(0, min(200, session._targets["B"]))
            session._screen = {"A": a, "B": b}
            await self._write(session, build_ovc_b3(a, b))
            await self._write(session, build_ovc_b2(a, b))'''
assert old in src, "ovc writer"
src = src.replace(old, new)

# v2 writer: write from targets
old = '''    async def _write_v2(self, session: BleSession) -> None:
        await self._write(session, pack_pwm_ab2(session._actual["A"], session._actual["B"]), V2_PWM_AB2)'''
new = '''    async def _write_v2(self, session: BleSession) -> None:
        await self._write(session, pack_pwm_ab2(session._targets["A"], session._targets["B"]), V2_PWM_AB2)'''
assert old in src, "v2 writer"
src = src.replace(old, new)

# public ops: targets semantics
old = '''    async def set_strength(self, channel: str, value: int, slot_id: str | None = None) -> None:
        session = self._session(slot_id)
        if session.kind == "bmtr":
            raise RuntimeError("灵猫是气压传感器，无输出通道")
        if session.kind == "ovc":
            value = max(0, min(200, int(value)))
            session._pending[channel] = value
            session._actual[channel] = value
        else:
            value = max(0, min(self.soft_limits[channel], int(value)))
            session._pending[channel] = value
            if session.kind == "coyote_v2":
                session._actual[channel] = value
        self._slot(session).strength[channel] = value
        self._publish()

    async def add_strength(self, channel: str, delta: int, slot_id: str | None = None) -> None:
        session = self._session(slot_id)
        await self.set_strength(channel, session._actual[channel] + int(delta), session.slot_id)'''
new = '''    async def set_strength(self, channel: str, value: int, slot_id: str | None = None) -> None:
        session = self._session(slot_id)
        if session.kind == "bmtr":
            raise RuntimeError("灵猫是气压传感器，无输出通道")
        if session.kind == "ovc":
            value = max(0, min(200, int(value)))
        else:
            value = max(0, min(self.soft_limits[channel], int(value)))
        session._targets[channel] = value
        session._actual[channel] = value  # optimistic; corrected by report
        self._slot(session).strength[channel] = value
        self._publish()

    async def add_strength(self, channel: str, delta: int, slot_id: str | None = None) -> None:
        session = self._session(slot_id)
        current = self._slot(session).strength.get(channel, 0)
        await self.set_strength(channel, current + int(delta), session.slot_id)'''
assert old in src, "public ops"
src = src.replace(old, new)

# fire: use targets
old = '''        # Apply the burst synchronously, then restore after the duration.
        # Restore to the user-visible strength (device echo may lag behind).
        saved = dict(self._slot(session).strength)
        for ch in CHANNELS:
            session._pending[ch] = cap
            session._actual[ch] = cap
            self._slot(session).strength[ch] = cap
        self._publish()

        async def _restore() -> None:
            try:
                await asyncio.sleep(duration_s)
                for ch in CHANNELS:
                    session._pending[ch] = saved.get(ch, 0)
                    session._actual[ch] = saved.get(ch, 0)
                self._slot(session).strength = dict(session._actual)
                self._publish()
                self._log(f"{session.slot_id} 开火结束，强度恢复 {saved}")
            except asyncio.CancelledError:
                pass'''
new = '''        # Apply the burst synchronously, then restore after the duration.
        # Restore to the user-visible strength (device echo may lag behind).
        saved = dict(self._slot(session).strength)
        for ch in CHANNELS:
            session._targets[ch] = cap
            session._actual[ch] = cap
            self._slot(session).strength[ch] = cap
        self._publish()

        async def _restore() -> None:
            try:
                await asyncio.sleep(duration_s)
                for ch in CHANNELS:
                    session._targets[ch] = saved.get(ch, 0)
                    session._actual[ch] = saved.get(ch, 0)
                self._slot(session).strength = dict(session._actual)
                self._publish()
                self._log(f"{session.slot_id} 开火结束，强度恢复 {saved}")
            except asyncio.CancelledError:
                pass'''
assert old in src, "fire"
src = src.replace(old, new)

# estop: targets
old = '''            try:
                await self.clear_wave(slot_id=session.slot_id)
                for ch in CHANNELS:
                    session._pending[ch] = 0
                    if session.kind != "coyote_v3":
                        session._actual[ch] = 0
                if session.kind != "coyote_v3":
                    self._slot(session).strength = {"A": 0, "B": 0}'''
new = '''            try:
                await self.clear_wave(slot_id=session.slot_id)
                for ch in CHANNELS:
                    session._targets[ch] = 0
                    session._actual[ch] = 0
                self._slot(session).strength = {"A": 0, "B": 0}'''
assert old in src, "estop"
src = src.replace(old, new)

# bmtr diagnostics: log first 8 raw frames
old = '''            elif session._unparsed_logged < 3:
                session._unparsed_logged += 1
                self._log(f"{session.slot_id} 未识别的通知: {bytes(data).hex().upper()}")
            return'''
new = '''            if len(session.__dict__.setdefault("_raw_logged", set())) < 8:
                key = bytes(data).hex().upper()
                if key not in session.__dict__["_raw_logged"]:
                    session.__dict__["_raw_logged"].add(key)
                    self._log(f"{session.slot_id} 通知帧: {key}")
            return'''
assert old in src, "bmtr diag"
src = src.replace(old, new)

# battery read failure log
old = '''        try:
            battery = await client.read_gatt_char(V3_BATTERY)
            self._slot(session).battery = int(battery[0])
        except Exception:
            pass'''
new = '''        try:
            battery = await client.read_gatt_char(V3_BATTERY)
            self._slot(session).battery = int(battery[0])
            self._log(f"{session.slot_id} 电量 {self._slot(session).battery}%")
        except Exception as exc:
            self._log(f"{session.slot_id} 电量读取失败: {exc!r}")'''
assert old in src, "battery log"
src = src.replace(old, new)

io.open('dglab/ble.py', 'w', encoding='utf-8').write(src)
print("BLE rework OK")
