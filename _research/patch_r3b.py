# -*- coding: utf-8 -*-
"""Engine: OVC strength quantized to multiples of 10; BLE FF0A vendor pipe."""
import io

src = io.open('app.py', encoding='utf-8').read()

old = '''    async def set_strength(self, channel: str, value: int, slot_id: str | None = None) -> None:
        backend = self._require_backend()
        limit = int(self.config["max_strength"])
        value = max(0, min(limit, int(value)))
        if isinstance(backend, SocketV4Client):'''
new = '''    def _quantize_for_device(self, slot, value: int) -> int:
        """OVC (负鼠) accepts strength changes only in multiples of 10."""
        if slot is not None and slot.type.upper().startswith("OVC"):
            return int(round(value / 10.0)) * 10
        return value

    async def set_strength(self, channel: str, value: int, slot_id: str | None = None) -> None:
        backend = self._require_backend()
        limit = int(self.config["max_strength"])
        value = max(0, min(limit, int(value)))
        if isinstance(backend, SocketV4Client):
            sid = self.resolve_slot(slot_id)
            slot = backend.state.slots.get(sid) if sid else None
            value = self._quantize_for_device(slot, value)
        if isinstance(backend, SocketV4Client):'''
assert old in src, "set_strength quantize"
src = src.replace(old, new)

old = '''        if isinstance(backend, SocketV4Client):
            slot = backend.state.slots.get(sid) if sid else None
            if slot is None:
                raise RuntimeError("V4 尚未接入设备")
            # Route through the adaptive absolute path so every press is
            # confirmed (im-free relative adds get coalesced by the App).
            await backend.set_strength(channel, slot.strength.get(channel, 0) + delta,
                                       slot_id=sid)
        elif isinstance(backend, SocketV3Client):'''
new = '''        if isinstance(backend, SocketV4Client):
            slot = backend.state.slots.get(sid) if sid else None
            if slot is None:
                raise RuntimeError("V4 尚未接入设备")
            # Route through the absolute path; OVC deltas quantized to x10.
            target = slot.strength.get(channel, 0) + delta
            if slot.type.upper().startswith("OVC"):
                target = slot.strength.get(channel, 0) + (
                    max(10, round(abs(delta) / 10.0) * 10) * (1 if delta > 0 else -1)
                )
            await backend.set_strength(channel, target, slot_id=sid)
        elif isinstance(backend, SocketV3Client):'''
assert old in src, "add_strength quantize"
src = src.replace(old, new)

# fire: quantize for OVC
old = '''        cap = int(self.config["max_strength"])
        if slot is not None:
            cap = min(cap, min(slot.strength_limit.get("A", 200),
                               slot.strength_limit.get("B", 200)))
        cap = max(1, cap)'''
new = '''        cap = int(self.config["max_strength"])
        if slot is not None:
            cap = min(cap, min(slot.strength_limit.get("A", 200),
                               slot.strength_limit.get("B", 200)))
        cap = max(1, cap)
        if slot is not None:
            cap = self._quantize_for_device(slot, cap)
        cap = max(1, cap)'''
assert old in src, "fire quantize"
src = src.replace(old, new)

io.open('app.py', 'w', encoding='utf-8').write(src)
print("engine quantize OK")

# ============ BLE: FF0A vendor pipe for BMTR ==============================
src = io.open('dglab/ble.py', encoding='utf-8').read()

old = '''V2_BASE = "955A{:04x}-0FE2-F5AA-A094-84B8D4F3E8AD"'''
new = '''# Undocumented vendor service present on BMTR hardware (from GATT dump):
# FF0A/FF00 notify+write, FF0A/FF01 write-without-response.  The pressure
# stream very likely flows here - we mirror commands and subscribe to both.
BMTR_FF_SERVICE = "0000ff0a-0000-1000-8000-00805f9b34fb"
BMTR_FF_NOTIFY = "0000ff00-0000-1000-8000-00805f9b34fb"
BMTR_FF_WRITE = "0000ff01-0000-1000-8000-00805f9b34fb"

V2_BASE = "955A{:04x}-0FE2-F5AA-A094-84B8D4F3E8AD"'''
assert old in src, "ff uuids"
src = src.replace(old, new)

old = '''        await client.start_notify(
            V3_NOTIFY, lambda sender, data, s=session: self._on_notify(s, sender, data)
        )'''
new = '''        await client.start_notify(
            V3_NOTIFY, lambda sender, data, s=session: self._on_notify(s, sender, data)
        )
        if kind == "bmtr":
            # Mirror the notify subscription on the vendor characteristic.
            try:
                await client.start_notify(
                    BMTR_FF_NOTIFY,
                    lambda sender, data, s=session: self._on_notify(s, sender, data),
                )
                self._log(f"{session.slot_id} 已订阅厂商通知 FF00")
            except Exception as exc:
                self._log(f"{session.slot_id} FF00 订阅失败: {exc!r}")'''
assert old in src, "ff subscribe"
src = src.replace(old, new)

old = '''        elif kind == "bmtr":
            # Dump the GATT table once - if the device uses non-standard
            # characteristics, the log shows exactly what is available.
            try:
                for service in client.services:
                    for ch in service.characteristics:
                        self._log(f"{session.slot_id} GATT {service.uuid} "
                                  f"{ch.uuid} {sorted(ch.properties)}")
            except Exception as exc:
                self._log(f"{session.slot_id} GATT 枚举失败: {exc!r}")
            await self._write(session, build_bmtr_50(enable_pressure=True))
            # Keep re-sending the enable command every second - some units
            # gate the pressure stream on a recent enable command.
            session.writer_task = asyncio.create_task(self._bmtr_keepalive(session))'''
new = '''        elif kind == "bmtr":
            # Dump the GATT table once - if the device uses non-standard
            # characteristics, the log shows exactly what is available.
            try:
                for service in client.services:
                    for ch in service.characteristics:
                        self._log(f"{session.slot_id} GATT {service.uuid} "
                                  f"{ch.uuid} {sorted(ch.properties)}")
            except Exception as exc:
                self._log(f"{session.slot_id} GATT 枚举失败: {exc!r}")
            enable = build_bmtr_50(enable_pressure=True)
            await self._write(session, enable)                      # documented 150A
            try:
                await self._write(session, enable, BMTR_FF_WRITE)   # vendor FF01
                self._log(f"{session.slot_id} 使能指令已镜像至 FF01")
            except Exception as exc:
                self._log(f"{session.slot_id} FF01 写入失败: {exc!r}")
            # Keep re-sending the enable command every second on both pipes.
            session.writer_task = asyncio.create_task(self._bmtr_keepalive(session))'''
assert old in src, "bmtr mirror"
src = src.replace(old, new)

old = '''    async def _bmtr_keepalive(self, session: BleSession) -> None:
        try:
            while True:
                await asyncio.sleep(1.0)
                if self.sessions.get(session.slot_id) is not session:
                    return
                await self._write(session, build_bmtr_50(enable_pressure=True,
                                                         color=session.led_color))
        except asyncio.CancelledError:
            pass
        except Exception as exc:
            self._log(f"{session.slot_id} 使能保活失败: {exc!r}")'''
new = '''    async def _bmtr_keepalive(self, session: BleSession) -> None:
        try:
            while True:
                await asyncio.sleep(1.0)
                if self.sessions.get(session.slot_id) is not session:
                    return
                enable = build_bmtr_50(enable_pressure=True,
                                       color=session.led_color)
                await self._write(session, enable)                  # 150A
                try:
                    await self._write(session, enable, BMTR_FF_WRITE)  # FF01
                except Exception:
                    pass
        except asyncio.CancelledError:
            pass
        except Exception as exc:
            self._log(f"{session.slot_id} 使能保活失败: {exc!r}")'''
assert old in src, "keepalive mirror"
src = src.replace(old, new)

# raw notify logging for bmtr: log EVERY distinct frame from both pipes
old = '''        if session.kind == "bmtr":
            pressure = parse_bmtr_pressure(data)
            if pressure is not None:
                self._slot(session).pressure = pressure
                self._publish()
            if len(session.__dict__.setdefault("_raw_logged", set())) < 8:
                key = bytes(data).hex().upper()
                if key not in session.__dict__["_raw_logged"]:
                    session.__dict__["_raw_logged"].add(key)
                    self._log(f"{session.slot_id} 通知帧: {key}")
            return'''
new = '''        if session.kind == "bmtr":
            key = bytes(data).hex().upper()
            logged = session.__dict__.setdefault("_raw_logged", {})
            logged[key] = logged.get(key, 0) + 1
            if logged[key] <= 3:
                self._log(f"{session.slot_id} 通知帧({len(data)}B): {key}")
            pressure = parse_bmtr_pressure(data)
            if pressure is not None:
                self._slot(session).pressure = pressure
                self._publish()
            return'''
assert old in src, "raw notify"
src = src.replace(old, new)

io.open('dglab/ble.py', 'w', encoding='utf-8').write(src)
print("ble ff0a OK")
