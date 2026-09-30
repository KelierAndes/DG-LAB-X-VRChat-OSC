# -*- coding: utf-8 -*-
"""Round-4: queue-depth wave pacing (never starve), SILENT default, no FF01."""
import io

# ================= 1) BLE: remove FF01 writes (device reboot) =============
src = io.open('dglab/ble.py', encoding='utf-8').read()

old = '''        if kind == "bmtr":
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
new = '''        if kind == "bmtr":
            enable = build_bmtr_50(enable_pressure=True)
            await self._write(session, enable)  # documented 150A pipe ONLY:
            # writing the vendor FF01 characteristic REBOOTS the device.
            # Keep re-sending the enable command every second on 150A.
            session.writer_task = asyncio.create_task(self._bmtr_keepalive(session))'''
assert old in src, "bmtr ff01 connect"
src = src.replace(old, new)

old = '''    async def _bmtr_keepalive(self, session: BleSession) -> None:
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
new = '''    async def _bmtr_keepalive(self, session: BleSession) -> None:
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
assert old in src, "keepalive ff01"
src = src.replace(old, new)

# BLE default wave: SILENT instead of CONTINUOUS
old = '''        if kind != "bmtr":
            # The DG-Lab app always keeps a waveform running; without one the
            # wave-strength bytes are all zero and NO output is produced
            # regardless of the strength value.  Start with the steady preset.
            steady = resolve_wave_frames(CONTINUOUS, session.device_type)
            session._cycles["A"].reset(steady)
            session._cycles["B"].reset(steady)
            session.writer_task = asyncio.create_task(self._writer_loop(session))'''
new = '''        if kind != "bmtr":
            # The App keeps intensity state only while wave data flows, so a
            # waveform must ALWAYS be running - default to the silent
            # (zero-strength) wave: no output until the user picks one.
            silent = resolve_wave_frames(SILENT, session.device_type)
            session._cycles["A"].reset(silent)
            session._cycles["B"].reset(silent)
            session.writer_task = asyncio.create_task(self._writer_loop(session))'''
assert old in src, "ble silent default"
src = src.replace(old, new)

old = '''from .waves import (
    CoyoteWaveform,
    FrameCycle,'''
new = '''from .waves import (
    CONTINUOUS,
    CoyoteWaveform,
    FrameCycle,
    SILENT,'''
assert old in src, "ble imports"
src = src.replace(old, new)

io.open('dglab/ble.py', 'w', encoding='utf-8').write(src)
print("ble round4 OK")
