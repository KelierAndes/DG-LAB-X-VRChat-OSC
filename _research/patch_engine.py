# -*- coding: utf-8 -*-
"""Engine additions: fire / LED / flip / OVC button binding / wave history."""
import io

src = io.open('app.py', encoding='utf-8').read()

# config defaults: step + button bindings
old = '''        "max_strength": 100,  # UI slider cap (safety)'''
new = '''        "max_strength": 100,  # cap (safety)
        "strength_step": 1,  # +/- button step
        "fire_duration_s": 1.0,'''
assert old in src
src = src.replace(old, new)

old = '''        "ble": {
            "soft_limit_a": 200,
            "soft_limit_b": 200,
            "freq_balance_a": 0,
            "freq_balance_b": 0,
            "strength_balance_a": 0,
            "strength_balance_b": 0,
        },'''
new = '''        "ble": {
            "soft_limit_a": 200,
            "soft_limit_b": 200,
            "freq_balance_a": 0,
            "freq_balance_b": 0,
            "strength_balance_a": 0,
            "strength_balance_b": 0,
            # 负鼠物理按键绑定: bit -> action (none/fire/zap_a/zap_b/estop)
            "ovc_buttons": {
                "0": "none", "1": "none", "2": "none",
                "8": "none", "9": "none", "10": "none", "11": "none",
                "12": "none", "13": "fire", "14": "none", "15": "none",
            },
        },'''
assert old in src
src = src.replace(old, new)

# engine init: subscribe ovc_button
old = '''        self.osc: OscBridge | None = None'''
new = '''        self.osc: OscBridge | None = None
        self.events.on("ovc_button", self._on_ovc_button)'''
assert old in src
src = src.replace(old, new)

# button dispatch + new commands after emergency_stop/reset_pressure block
old = '''    async def reset_pressure(self, slot_id: str | None = None) -> None:'''
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
        if isinstance(backend, (SocketV4Client, BleClient)):
            await backend.fire(slot_id=sid, duration_s=duration, value=cap)
        else:
            self._log("当前连接模式不支持一键开火")

    async def set_led_color(self, color: str, slot_id: str | None = None) -> None:
        backend = self._require_backend()
        if isinstance(backend, BleClient):
            await backend.set_led(color, slot_id=self.resolve_slot(slot_id))
        else:
            self._log("LED 颜色切换目前仅支持蓝牙直连 (负鼠/灵猫)")

    async def bmtr_flip(self, slot_id: str | None = None) -> None:
        backend = self._require_backend()
        if isinstance(backend, BleClient):
            await backend.bmtr_flip(slot_id=self.resolve_slot(slot_id, family="BMTR"))
        else:
            self._log("屏幕翻转目前仅支持蓝牙直连的灵猫")

    def wave_history(self, slot_id: str | None = None):
        """WaveMonitor for a device (real-time chart source)."""
        backend = self._backend
        if backend is None:
            return None
        if isinstance(backend, BleClient):
            sid = self.resolve_slot(slot_id)
            session = backend.sessions.get(sid) if sid else None
            return session.monitor if session else None
        if isinstance(backend, SocketV4Client):
            sid = self.resolve_slot(slot_id)
            return backend.monitors.get(sid) if sid else None
        return None

    _OVC_BUTTON_ACTIONS = ("none", "fire", "zap_a", "zap_b", "estop")

    def _on_ovc_button(self, slot_id: str, bit: int) -> None:
        binding = self.config.get("ble", {}).get("ovc_buttons", {}).get(str(bit), "none")
        if binding in ("", "none"):
            return
        self._log(f"按键 bit{bit} → {binding}")

        async def _run() -> None:
            if binding == "fire":
                await self.fire(slot_id=slot_id)
            elif binding == "zap_a":
                await self.fire(slot_id=slot_id)
            elif binding == "zap_b":
                await self.fire(slot_id=slot_id)
            elif binding == "estop":
                await self.emergency_stop()

        if self.loop is not None and self.loop.is_running():
            asyncio.run_coroutine_threadsafe(_run(), self.loop)

    async def reset_pressure(self, slot_id: str | None = None) -> None:'''
assert old in src
src = src.replace(old, new)

# zap: route to fire (burst) for V4/BLE
old = '''    async def zap(self, channel: str, seconds: float = 1.0, slot_id: str | None = None) -> None:
        """Short pulse burst using the currently selected waveform."""
        backend = self._require_backend()
        if isinstance(backend, SocketV4Client):
            sid = self.resolve_slot(slot_id)
            slot = backend.state.slots.get(sid) if sid else None
            device_type = slot.type if slot and slot.type else "COYOTE_030"
            wave = self._selected_wave.get(channel, DEFAULT_WAVE)
            try:
                resolve_wave_frames(wave, device_type)
            except KeyError:
                wave = CONTINUOUS
            await backend.send_wave(channel, wave, seconds, slot_id=sid)
        elif isinstance(backend, SocketV3Client):
            await backend.send_wave(channel, self._selected_wave.get(channel, DEFAULT_WAVE), seconds)
        else:
            self._log("[BLE] zap: 蓝牙模式波形常播，忽略瞬时脉冲")'''
new = '''    async def zap(self, channel: str, seconds: float = 1.0, slot_id: str | None = None) -> None:
        """Short burst - implemented as a temporary intensity burst (fire)."""
        await self.fire(slot_id=slot_id, duration_s=seconds)'''
assert old in src
src = src.replace(old, new)

open('app.py', 'w', encoding='utf-8').write(src)
print("engine patch OK")
