# -*- coding: utf-8 -*-
"""BLE additions: fire, LED color, BMTR flip, button events, wave monitor."""
import io

src = io.open('dglab/ble.py', encoding='utf-8').read()

old = '''from .state import EngineState, Slot, StateEvents'''
new = '''from .monitor import WaveMonitor
from .state import EngineState, Slot, StateEvents'''
assert old in src
src = src.replace(old, new)

# LED palette (official doc documents 01=yellow; others follow the app palette)
old = '''OVC_B2_BODY = bytes.fromhex("FFFF00FFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFF0809")'''
new = '''OVC_B2_BODY = bytes.fromhex("FFFF00FFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFF0809")

# 0x50 color byte values (0x01 yellow is documented; the rest follow the
# DG-Lab app palette - verify on hardware and adjust if needed).
LED_COLORS = {
    "off": 0x00,
    "yellow": 0x01,
    "green": 0x02,
    "red": 0x03,
    "blue": 0x04,
    "purple": 0x05,
    "cyan": 0x06,
}'''
assert old in src
src = src.replace(old, new)

# session fields
old = '''        self._pressed: set[int] = set()
        self.writer_task: asyncio.Task | None = None'''
new = '''        self._pressed: set[int] = set()
        self.writer_task: asyncio.Task | None = None
        self.monitor = WaveMonitor()
        self.led_color = 0x01
        self.orientation = 1
        self.fire_task: asyncio.Task | None = None
        self._fire_saved: dict[str, int] = {}'''
assert old in src
src = src.replace(old, new)

# button events: emit ovc_button for mapping
old = '''    def _handle_buttons(self, session: BleSession, data: bytearray) -> None:
        pressed = set(parse_ovc_buttons(data))
        for bit in sorted(pressed - session._pressed):
            self._log(f"{session.slot_id} 按键按下: bit{bit}")
            self.events.emit("action", bit)
        session._pressed = pressed'''
new = '''    def _handle_buttons(self, session: BleSession, data: bytearray) -> None:
        pressed = set(parse_ovc_buttons(data))
        for bit in sorted(pressed - session._pressed):
            self._log(f"{session.slot_id} 按键按下: bit{bit}")
            self.events.emit("action", bit)
            self.events.emit("ovc_button", session.slot_id, bit)
        session._pressed = pressed'''
assert old in src
src = src.replace(old, new)

# monitor recording in writer paths
old = '''    async def _write_b0(self, session: BleSession) -> None:
        fa, sa, ma, va = self._v3_channel_fields(session, "A")
        fb, sb, mb, vb = self._v3_channel_fields(session, "B")
        seq, method = 0, 0b00
        if ma or mb:
            seq = session.next_seq()
            session._awaiting_seq = seq
            session._await_since = session.ticks
            method = (ma << 2) | mb
        await self._write(session, build_b0(seq, method, va, vb, fa, sa, fb, sb))'''
new = '''    async def _write_b0(self, session: BleSession) -> None:
        fa, sa, ma, va = self._v3_channel_fields(session, "A")
        fb, sb, mb, vb = self._v3_channel_fields(session, "B")
        seq, method = 0, 0b00
        if ma or mb:
            seq = session.next_seq()
            session._awaiting_seq = seq
            session._await_since = session.ticks
            method = (ma << 2) | mb
        await self._write(session, build_b0(seq, method, va, vb, fa, sa, fb, sb))
        session.monitor.record(sa, sb)'''
assert old in src
src = src.replace(old, new)

old = '''        await self._write(session, build_ovc_b0(wave_a, wave_b))
        if session._pending:'''
new = '''        await self._write(session, build_ovc_b0(wave_a, wave_b))
        session.monitor.record(wave_a, wave_b)
        if session._pending:'''
assert old in src
src = src.replace(old, new)

# v2 recording: record the strength pct of the used frame segments
old = '''    async def _write_v2(self, session: BleSession) -> None:
        await self._write(session, pack_pwm_ab2(session._actual["A"], session._actual["B"]), V2_PWM_AB2)
        for ch, char in (("A", V2_PWM_B34), ("B", V2_PWM_A34)):
            cycle = session._cycles[ch]
            if not cycle.frames:
                continue
            frame = cycle.next_frame()
            raw = bytes.fromhex(frame)
            freqs, strengths = list(raw[:4]), list(raw[4:])
            logical = wire_to_logical_freq(round(sum(freqs) / 4))
            strength_pct = sum(strengths) / 4
            x, y = frequency_to_xy(logical)
            z = round(max(0, min(100, strength_pct)) * 20 / 100)
            await self._write(session, pack_xyz(x, y, z), char)'''
new = '''    async def _write_v2(self, session: BleSession) -> None:
        await self._write(session, pack_pwm_ab2(session._actual["A"], session._actual["B"]), V2_PWM_AB2)
        segs_a, segs_b = [0, 0, 0, 0], [0, 0, 0, 0]
        for ch, char, segs in (("A", V2_PWM_B34, segs_a), ("B", V2_PWM_A34, segs_b)):
            cycle = session._cycles[ch]
            if not cycle.frames:
                continue
            frame = cycle.next_frame()
            raw = bytes.fromhex(frame)
            freqs, strengths = list(raw[:4]), list(raw[4:])
            segs[:] = strengths
            logical = wire_to_logical_freq(round(sum(freqs) / 4))
            strength_pct = sum(strengths) / 4
            x, y = frequency_to_xy(logical)
            z = round(max(0, min(100, strength_pct)) * 20 / 100)
            await self._write(session, pack_xyz(x, y, z), char)
        session.monitor.record(segs_a, segs_b)'''
assert old in src
src = src.replace(old, new)

# public ops additions: fire, led, flip
old = '''    async def emergency_stop(self) -> None:
        for session in list(self.sessions.values()):'''
new = '''    async def fire(self, slot_id: str | None = None, duration_s: float = 1.0,
                   value: int | None = None) -> None:
        """一键开火: temporarily raise both channels, then restore."""
        session = self._session(slot_id)
        if session.kind == "bmtr":
            raise RuntimeError("灵猫是气压传感器，无输出通道")
        cap = value if value is not None else 200
        if session.fire_task is not None and not session.fire_task.done():
            session.fire_task.cancel()

        async def _run() -> None:
            try:
                saved = dict(session._actual)
                for ch in CHANNELS:
                    session._pending[ch] = cap
                    session._actual[ch] = cap
                    self._slot(session).strength[ch] = cap
                self._publish()
                await asyncio.sleep(duration_s)
                for ch in CHANNELS:
                    session._pending[ch] = saved.get(ch, 0)
                    session._actual[ch] = saved.get(ch, 0)
                self._slot(session).strength = dict(session._actual)
                self._publish()
                self._log(f"{session.slot_id} 开火结束，强度恢复 {saved}")
            except asyncio.CancelledError:
                pass

        session.fire_task = asyncio.create_task(_run())
        self._log(f"{session.slot_id} 一键开火 {duration_s}s 强度 {cap}")

    async def set_led(self, color: str, slot_id: str | None = None) -> None:
        session = self._session(slot_id)
        if session.kind not in ("ovc", "bmtr"):
            raise RuntimeError("该设备不支持 LED 颜色设置 (仅负鼠/灵猫)")
        byte = LED_COLORS.get(color)
        if byte is None:
            raise RuntimeError(f"未知颜色: {color}")
        session.led_color = byte
        if session.kind == "ovc":
            await self._write(session, build_ovc_50(enable_buttons=True, color=byte))
        else:
            await self._write(session, build_bmtr_50(enable_pressure=True, color=byte))
        self._log(f"{session.slot_id} LED 颜色 → {color}")

    async def bmtr_flip(self, slot_id: str | None = None) -> None:
        session = self._session(slot_id)
        if session.kind != "bmtr":
            raise RuntimeError("仅灵猫支持屏幕翻转")
        session.orientation = 3 if session.orientation == 1 else 1
        await self._write(session, build_bmtr_66(reset_pressure=False,
                                                 orientation=session.orientation))
        self._log(f"{session.slot_id} 屏幕方向 → {session.orientation}")

    async def emergency_stop(self) -> None:
        for session in list(self.sessions.values()):'''
assert old in src
src = src.replace(old, new)

io.open('dglab/ble.py', 'w', encoding='utf-8').write(src)
print("ble patch OK")
