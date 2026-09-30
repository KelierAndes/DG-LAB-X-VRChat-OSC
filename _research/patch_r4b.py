# -*- coding: utf-8 -*-
"""Round-4b: silent default wiring, engine clear->silent, fire burst swap."""
import io

# ================= engine: clear_wave -> silent frames, fire burst ========
src = io.open('app.py', encoding='utf-8').read()

old = '''from dglab.waves import CONTINUOUS, COYOTE_WAVEFORMS, CoyoteWaveform, resolve_wave_frames'''
new = '''from dglab.waves import (CONTINUOUS, COYOTE_WAVEFORMS, CoyoteWaveform, SILENT,
                         resolve_wave_frames)'''
assert old in src, "engine import"
src = src.replace(old, new)

old = '''    async def clear_wave(self, channel: str | None = None, slot_id: str | None = None) -> None:
        backend = self._require_backend()
        if isinstance(backend, SocketV4Client):
            await backend.clear_pulse(channel, slot_id=self.resolve_slot(slot_id))
        elif isinstance(backend, SocketV3Client):
            await backend.clear_pulse(channel)
        else:
            await backend.clear_wave(channel, slot_id=self.resolve_slot(slot_id))'''
new = '''    async def clear_wave(self, channel: str | None = None, slot_id: str | None = None) -> None:
        """静默: switch the channel(s) to the silent (zero-strength) wave.

        The wave session keeps flowing (so the App's intensity state
        persists) while producing no output.  device.op.clear is reserved
        for the emergency stop - it wipes the channel intensity.
        """
        backend = self._require_backend()
        sid = self.resolve_slot(slot_id)
        for ch in ("A", "B"):
            if channel in (None, ch):
                await self.set_wave(ch, SILENT, slot_id=sid)'''
assert old in src, "clear_wave silent"
src = src.replace(old, new)

# fire: swap in a real wave while silent, restore afterwards
old = '''    async def fire(self, slot_id: str | None = None, duration_s: float | None = None) -> None:
        """一键开火: temporary max burst on the target output device."""
        backend = self._require_backend()
        sid = self.resolve_slot(slot_id)
        if sid is None:
            raise RuntimeError("没有可用设备")
        duration = float(duration_s or self.config["fire_duration_s"])
        # Fire value: configured cap, bounded by the device's own limit.
        state = self.get_state()
        slot = state.slots.get(sid)
        cap = int(self.config["max_strength"])
        if slot is not None:
            cap = min(cap, min(slot.strength_limit.get("A", 200),
                               slot.strength_limit.get("B", 200)))
        cap = max(1, cap)
        if slot is not None:
            cap = self._quantize_for_device(slot, cap)
        cap = max(1, cap)
        if isinstance(backend, (SocketV4Client, BleClient)):
            await backend.fire(slot_id=sid, duration_s=duration, value=cap)
        else:
            self._log("当前连接模式不支持一键开火")'''
new = '''    async def fire(self, slot_id: str | None = None, duration_s: float | None = None) -> None:
        """一键开火: temporary max burst on the target output device."""
        backend = self._require_backend()
        sid = self.resolve_slot(slot_id)
        if sid is None:
            raise RuntimeError("没有可用设备")
        duration = float(duration_s or self.config["fire_duration_s"])
        # Fire value: configured cap, bounded by the device's own limit.
        state = self.get_state()
        slot = state.slots.get(sid)
        cap = int(self.config["max_strength"])
        if slot is not None:
            cap = min(cap, min(slot.strength_limit.get("A", 200),
                               slot.strength_limit.get("B", 200)))
        cap = max(1, cap)
        if slot is not None:
            cap = self._quantize_for_device(slot, cap)
        cap = max(1, cap)

        # If the device is silent, temporarily run a real wave for the burst.
        silent_before = any(
            self._selected_wave.get(ch) in ("", SILENT) for ch in ("A", "B")
        )
        try:
            if silent_before:
                for ch in ("A", "B"):
                    await self.set_wave(ch, CONTINUOUS, slot_id=sid)
            if isinstance(backend, (SocketV4Client, BleClient)):
                await backend.fire(slot_id=sid, duration_s=duration, value=cap)
            else:
                self._log("当前连接模式不支持一键开火")
                return
            await asyncio.sleep(duration)
        finally:
            if silent_before:
                for ch in ("A", "B"):
                    try:
                        await self.set_wave(ch, self._selected_wave.get(ch, SILENT),
                                            slot_id=sid)
                    except Exception:
                        pass'''
assert old in src, "fire swap"
src = src.replace(old, new)

io.open('app.py', 'w', encoding='utf-8').write(src)
print("engine round4b OK")

# ================= UI: silent option + default =============================
src = io.open('ui/main_window.py', encoding='utf-8').read()

old = '''from dglab.waves import CONTINUOUS'''
new = '''from dglab.waves import CONTINUOUS, SILENT'''
assert old in src, "ui import"
src = src.replace(old, new)

old = '''    items: list[tuple[str, str]] = [("静默 (无输出)", "")]'''
new = '''    items: list[tuple[str, str]] = [("静默 (无输出)", SILENT)]'''
assert old in src, "wave items"
src = src.replace(old, new)

old = '''            box.SelectedIndex = len(items) - 1  # default: 持续'''
new = '''            box.SelectedIndex = 0  # default: 静默 (no output until picked)'''
assert old in src, "default silent"
src = src.replace(old, new)

old = '''    def _on_wave_combo(self, family: str, channel: str, box) -> None:
        if self._updating_ui:
            return
        value = self._wave_for(family, box)
        if value is None:
            return
        if value == "":
            self._submit(self.engine.clear_wave(channel,
                                                slot_id=self._fam_selected[family]))
            return
        self.engine._selected_wave[channel] = value
        self._submit(self.engine.set_wave(channel, value,
                                          slot_id=self._fam_selected[family]))'''
new = '''    def _on_wave_combo(self, family: str, channel: str, box) -> None:
        if self._updating_ui:
            return
        value = self._wave_for(family, box)
        if value is None:
            return
        self.engine._selected_wave[channel] = value
        self._submit(self.engine.set_wave(channel, value,
                                          slot_id=self._fam_selected[family]))'''
assert old in src, "combo handler"
src = src.replace(old, new)

src = src.replace(
    'Text="强度用加减键调节（Socket V4 下由 App 反馈实际值）；波形选择后立即持续循环发送；一键开火临时拉满强度 1 秒。急停作用于全部已接入设备。"/>',
    'Text="强度用加减键调节；默认波形为静默（无输出但保持会话，强度不会失效），选择任意波形后立即持续输出；一键开火在静默时也会临时以持续波形爆发。急停作用于全部已接入设备。"/>')

io.open('ui/main_window.py', 'w', encoding='utf-8').write(src)
print("ui round4b OK")
