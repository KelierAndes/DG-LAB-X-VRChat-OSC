# -*- coding: utf-8 -*-
"""One-off patch: engine slot routing (run from project root)."""
import io

src = io.open('app.py', encoding='utf-8').read()

old = '''    async def set_strength(self, channel: str, value: int) -> None:
        backend = self._require_backend()
        limit = int(self.config["max_strength"])
        value = max(0, min(limit, int(value)))
        if isinstance(backend, SocketV4Client):
            slot = backend.state.active()
            if slot is None:
                raise RuntimeError("V4 尚未接入设备")
            ch = channel
            current = slot.strength[ch]
            delta = value - current
            if delta:
                await backend.add_intensity(ch, delta)
        elif isinstance(backend, SocketV3Client):
            await backend.set_strength(channel, value)
        else:
            await backend.set_strength(channel, value)

    async def add_strength(self, channel: str, delta: int) -> None:
        backend = self._require_backend()
        if isinstance(backend, SocketV4Client):
            await backend.add_intensity(channel, delta)
        elif isinstance(backend, SocketV3Client):
            await backend.add_strength(channel, delta)
        else:
            await backend.add_strength(channel, delta)

    async def set_wave(self, channel: str, name: str) -> None:
        backend = self._require_backend()
        self._selected_wave[channel] = name
        if isinstance(backend, SocketV4Client):
            await backend.send_wave(
                channel, name, float(self.config["wave_duration_s"])
            )
        elif isinstance(backend, SocketV3Client):
            await backend.send_wave(
                channel, name, float(self.config["wave_duration_s"])
            )
        else:
            await backend.set_wave(channel, name)

    async def clear_wave(self, channel: str | None = None) -> None:
        backend = self._require_backend()
        if isinstance(backend, SocketV4Client):
            await backend.clear_pulse(channel)
        elif isinstance(backend, SocketV3Client):
            await backend.clear_pulse(channel)
        else:
            await backend.clear_wave(channel)'''

new = '''    # ------------------------------------------------------ device routing
    def devices(self) -> list[dict]:
        """All known devices: [{slot_id, name, type, family}]."""
        state = self.get_state()
        out = []
        for sid in sorted(state.slots):
            slot = state.slots[sid]
            out.append({
                "slot_id": sid,
                "name": slot.name or slot.type or sid,
                "type": slot.type,
                "family": family_of(slot.type),
            })
        return out

    def resolve_slot(self, slot_id: str | None = None, family: str | None = None) -> str | None:
        """Explicit slot, else first device of family, else first device."""
        state = self.get_state()
        if slot_id and slot_id in state.slots:
            return slot_id
        if family:
            for dev in self.devices():
                if dev["family"] == family:
                    return dev["slot_id"]
        return next(iter(sorted(state.slots)), None)

    async def set_strength(self, channel: str, value: int, slot_id: str | None = None) -> None:
        backend = self._require_backend()
        limit = int(self.config["max_strength"])
        value = max(0, min(limit, int(value)))
        if isinstance(backend, SocketV4Client):
            sid = self.resolve_slot(slot_id)
            if sid is None:
                raise RuntimeError("V4 尚未接入设备")
            slot = backend.state.slots.get(sid)
            if slot is None:
                raise RuntimeError(f"设备不存在: {sid}")
            delta = value - slot.strength[channel]
            if delta:
                await backend.add_intensity(channel, delta, slot_id=sid)
        elif isinstance(backend, SocketV3Client):
            await backend.set_strength(channel, value)
        else:
            await backend.set_strength(channel, value)

    async def add_strength(self, channel: str, delta: int, slot_id: str | None = None) -> None:
        backend = self._require_backend()
        if isinstance(backend, SocketV4Client):
            await backend.add_intensity(channel, delta, slot_id=self.resolve_slot(slot_id))
        elif isinstance(backend, SocketV3Client):
            await backend.add_strength(channel, delta)
        else:
            await backend.add_strength(channel, delta)

    async def set_wave(self, channel: str, name: str, slot_id: str | None = None) -> None:
        backend = self._require_backend()
        self._selected_wave[channel] = name
        if isinstance(backend, SocketV4Client):
            sid = self.resolve_slot(slot_id)
            await backend.send_wave(
                channel, name, float(self.config["wave_duration_s"]), slot_id=sid
            )
        elif isinstance(backend, SocketV3Client):
            await backend.send_wave(
                channel, name, float(self.config["wave_duration_s"])
            )
        else:
            await backend.set_wave(channel, name)

    async def clear_wave(self, channel: str | None = None, slot_id: str | None = None) -> None:
        backend = self._require_backend()
        if isinstance(backend, SocketV4Client):
            await backend.clear_pulse(channel, slot_id=self.resolve_slot(slot_id))
        elif isinstance(backend, SocketV3Client):
            await backend.clear_pulse(channel)
        else:
            await backend.clear_wave(channel)'''

assert old in src, "block 1 not found"
src = src.replace(old, new)

old2 = '''    async def zap(self, channel: str, seconds: float = 1.0) -> None:
        """Short pulse burst using the currently selected waveform."""
        backend = self._require_backend()
        wave = self._selected_wave.get(channel, DEFAULT_WAVE)
        # Fall back to the steady preset when the selection does not exist on
        # this device type (e.g. a Coyote waveform picked for an OVC).
        try:
            resolve_wave_frames(wave, "OVC_1" if isinstance(backend, SocketV4Client) and
                                (backend.state.active() is not None and backend.state.active().type.startswith("OVC")) else "COYOTE_030")
        except KeyError:
            wave = CONTINUOUS
        if isinstance(backend, SocketV4Client):
            await backend.send_wave(channel, wave, seconds)
        elif isinstance(backend, SocketV3Client):
            await backend.send_wave(channel, wave, seconds)
        else:
            self._log("[BLE] zap: 蓝牙模式波形常播，忽略瞬时脉冲")'''

new2 = '''    async def zap(self, channel: str, seconds: float = 1.0, slot_id: str | None = None) -> None:
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

assert old2 in src, "block 2 not found"
src = src.replace(old2, new2)

old3 = '''def local_lan_ip() -> str:'''
new3 = '''def family_of(device_type: str) -> str:
    """Device family used for UI grouping and OSC prefixes."""
    t = (device_type or "").upper()
    if t.startswith("OVC"):
        return "OVC"
    if t.startswith("BMTR"):
        return "BMTR"
    return "COYOTE"


def local_lan_ip() -> str:'''
assert old3 in src, "block 3 not found"
src = src.replace(old3, new3)

src = src.replace('__all__ = ["Config", "Engine", "local_lan_ip"]',
                  '__all__ = ["Config", "Engine", "local_lan_ip", "family_of"]')

io.open('app.py', 'w', encoding='utf-8').write(src)
print("patched OK")
