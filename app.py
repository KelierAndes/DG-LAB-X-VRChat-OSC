from __future__ import annotations

import asyncio
import json
import os
import sys
import threading
import time
from concurrent.futures import Future
from typing import Any

import socket as _socket

from dglab.ble import BleClient
from dglab.official_waveforms import CoyoteWaveform
from dglab.relay_v3 import RelayV3Server
from dglab.relay_v4 import RelayV4Server
from dglab.socket_v3 import DEFAULT_V3_RELAY, SocketV3Client
from dglab.socket_v4 import DEFAULT_V4_RELAY, SocketV4Client
from dglab.state import EngineState, StateEvents, family_of
from dglab.waves import (CONTINUOUS, COYOTE_WAVEFORMS, CoyoteWaveform, SILENT,
                         resolve_wave_frames)
from vrc.osc_bridge import OscBridge, OscConfig


def _base_dir() -> str:
    if getattr(sys, "frozen", False):
        return os.path.dirname(os.path.abspath(sys.executable))
    return os.path.dirname(os.path.abspath(__file__))


class Config(dict):
    DEFAULTS = {
        "v4_url": DEFAULT_V4_RELAY,
        "v3_url": DEFAULT_V3_RELAY,
        "wave_duration_s": 10.0,
        "max_strength": 100,
        "strength_step": 1,
        "fire_duration_s": 1.0,
        "fire_strength": 0,
        "ble": {
            "soft_limit_a": 200,
            "soft_limit_b": 200,
            "freq_balance_a": 0,
            "freq_balance_b": 0,
            "strength_balance_a": 0,
            "strength_balance_b": 0,
            "ovc_buttons": {
                "0": "none", "1": "none", "2": "none",
                "8": "none", "9": "none", "10": "none", "11": "none",
                "12": "none", "13": "fire", "14": "none", "15": "none",
            },
        },
        "osc": dict(OscConfig.DEFAULTS),
        "relay": {"v4_port": 9998, "v3_port": 9999},
        "log_frames": True,
        "auto_reconnect": True,
        "saved_devices": [],
    }

    def __init__(self, path: str | None = None):
        super().__init__(json.loads(json.dumps(self.DEFAULTS)))
        self.path = path or os.path.join(_base_dir(), "config.json")
        self.load()

    def load(self) -> None:
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, json.JSONDecodeError):
            return
        for key, value in data.items():
            if key == "osc" and isinstance(value, dict):
                self["osc"].update(value)
            elif isinstance(value, dict) and isinstance(self.get(key), dict):
                self[key].update(value)
            else:
                self[key] = value

    def save(self) -> None:
        try:
            with open(self.path, "w", encoding="utf-8") as f:
                json.dump(self, f, ensure_ascii=False, indent=2)
        except OSError:
            pass


import logging


def _setup_file_logger(base_dir: str) -> logging.Logger:
    logger = logging.getLogger("dglab_osc")
    logger.setLevel(logging.INFO)
    if not logger.handlers:
        try:
            handler = logging.FileHandler(
                os.path.join(base_dir, "dglab_osc.log"), encoding="utf-8"
            )
            handler.setFormatter(
                logging.Formatter("%(asctime)s.%(msecs)03d %(message)s", "%Y-%m-%d %H:%M:%S")
            )
            logger.addHandler(handler)
        except OSError:
            pass
    return logger


class Engine:
    def __init__(self, config_path: str | None = None):
        self.config = Config(config_path)
        self._file_log = _setup_file_logger(_base_dir())
        self.events = StateEvents()
        self.loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None

        self._backend: SocketV4Client | SocketV3Client | BleClient | None = None
        self._relay: RelayV4Server | RelayV3Server | None = None
        self._selected_wave: dict[str, CoyoteWaveform | str] = {
            "A": SILENT,
            "B": SILENT,
        }
        self.osc: OscBridge | None = None
        self._fire_holds: dict[str, dict] = {}
        self.events.on("ovc_button", self._on_ovc_button)
        self.events.on("frame_log", lambda d, f: self.log_frame(d, f))
        self.events.on("log", self._file_only)

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._loop_ready = threading.Event()
        self._thread = threading.Thread(target=self._run_loop, name="dglab-engine", daemon=True)
        self._thread.start()
        if not self._loop_ready.wait(timeout=5.0):
            raise RuntimeError("engine loop failed to start")
        self.submit(self._reconnect_loop())

    def _run_loop(self) -> None:
        self.loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self.loop)
        self._loop_ready.set()
        try:
            self.loop.run_forever()
        finally:
            pending = asyncio.all_tasks(self.loop)
            for task in pending:
                task.cancel()
            self.loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
            self.loop.close()

    def stop(self) -> None:
        if self.loop and self.loop.is_running():
            fut = asyncio.run_coroutine_threadsafe(self.shutdown(), self.loop)
            try:
                fut.result(timeout=3.0)
            except Exception:
                pass
        if self._thread:
            self._thread.join(timeout=3.0)

    async def shutdown(self) -> None:
        try:
            await self._disconnect_backend()
        except Exception:
            pass
        try:
            if self.osc:
                await self.osc.stop()
        except Exception:
            pass
        if self.loop and self.loop.is_running():
            self.loop.call_soon_threadsafe(self.loop.stop)

    def submit(self, coro) -> Future:
        if not self.loop:
            raise RuntimeError("engine not started")
        return asyncio.run_coroutine_threadsafe(coro, self.loop)

    def _log(self, msg: str) -> None:
        self.events.emit("log", msg)

    def _file_only(self, msg: str) -> None:
        try:
            self._file_log.info(msg)
        except Exception:
            pass

    def log_frame(self, direction: str, frame: dict) -> None:
        if not self.config.get("log_frames"):
            return
        try:
            text = json.dumps(frame, ensure_ascii=False, separators=(",", ":"))
        except Exception:
            text = str(frame)
        if len(text) > 300:
            text = text[:300] + f"...(+{len(text) - 300})"
        self._log(f"{direction} {text}")

    def get_state(self) -> EngineState:
        if self._backend is not None:
            return self._backend.state
        return EngineState()

    @property
    def backend_kind(self) -> str:
        return self._backend.state.backend if self._backend else "none"

    async def _disconnect_backend(self) -> None:
        if self._backend is not None:
            try:
                await self._backend.disconnect()
            except Exception:
                pass
            self._backend = None
        await self._stop_relay()

    async def connect_v4(self) -> None:
        await self._disconnect_backend()
        client = SocketV4Client(self.config["v4_url"], events=self.events)
        self._backend = client
        try:
            await client.connect()
        except Exception:
            self._backend = None
            raise

    async def connect_v3(self) -> None:
        await self._disconnect_backend()
        client = SocketV3Client(self.config["v3_url"], events=self.events)
        self._backend = client
        try:
            await client.connect()
        except Exception:
            self._backend = None
            raise

    async def connect_v4_local(self, port: int | None = None) -> None:
        await self._disconnect_backend()
        port = int(port or self.config["relay"]["v4_port"])
        relay = RelayV4Server(port=port, events=self.events)
        try:
            await relay.start()
        except OSError as exc:
            self._log(f"V4 本地中继启动失败 (端口 {port} 被占用?): {exc!r}")
            raise
        self._relay = relay
        qr_base = f"ws://{local_lan_ip()}:{port}"
        self._log(f"本地中继地址: {qr_base} (App 需与电脑同一局域网)")
        client = SocketV4Client(
            f"ws://127.0.0.1:{port}", events=self.events, qr_base=qr_base
        )
        self._backend = client
        try:
            await client.connect()
        except Exception:
            await self._stop_relay()
            self._backend = None
            raise

    async def connect_v3_local(self, port: int | None = None) -> None:
        await self._disconnect_backend()
        port = int(port or self.config["relay"]["v3_port"])
        relay = RelayV3Server(port=port, events=self.events)
        try:
            await relay.start()
        except OSError as exc:
            self._log(f"V3 本地中继启动失败 (端口 {port} 被占用?): {exc!r}")
            raise
        self._relay = relay
        qr_base = f"ws://{local_lan_ip()}:{port}"
        self._log(f"本地中继地址: {qr_base} (App 需与电脑同一局域网)")
        client = SocketV3Client(
            f"ws://127.0.0.1:{port}", events=self.events, qr_base=qr_base
        )
        self._backend = client
        try:
            await client.connect()
        except Exception:
            await self._stop_relay()
            self._backend = None
            raise

    async def _stop_relay(self) -> None:
        if self._relay is not None:
            try:
                await self._relay.stop()
            except Exception:
                pass
            self._relay = None

    async def ble_connect(self, address: str, kind: str = "coyote_v3") -> None:
        ble_cfg = self.config["ble"]
        if isinstance(self._backend, BleClient):
            client = self._backend
        else:
            await self._disconnect_backend()
            client = BleClient(
                events=self.events,
                soft_limit_a=int(ble_cfg["soft_limit_a"]),
                soft_limit_b=int(ble_cfg["soft_limit_b"]),
                freq_balance_a=int(ble_cfg["freq_balance_a"]),
                freq_balance_b=int(ble_cfg["freq_balance_b"]),
                strength_balance_a=int(ble_cfg["strength_balance_a"]),
                strength_balance_b=int(ble_cfg["strength_balance_b"]),
            )
            self._backend = client
        try:
            await client.connect(address, kind)
        except Exception:
            if not client.sessions:
                self._backend = None
            raise
        slot = client.state.slots.get(address)
        self.remember_device(address, kind, (slot.name if slot else None) or kind)

    async def ble_disconnect_device(self, slot_id: str) -> None:
        if isinstance(self._backend, BleClient):
            await self._backend.disconnect(slot_id)

    def saved_device_list(self) -> list[dict]:
        return list(self.config.get("saved_devices", []))

    def remember_device(self, address: str, kind: str, name: str) -> None:
        devices = self.config.setdefault("saved_devices", [])
        for dev in devices:
            if dev.get("address") == address:
                dev.update(kind=kind, name=name)
                self.config.save()
                return
        devices.append({"address": address, "kind": kind, "name": name})
        self._log(f"设备已记录: {name} [{kind}] {address} (可一键重连)")
        self.config.save()
        self.events.emit("saved_devices", self.saved_device_list())

    def forget_device(self, address: str) -> None:
        devices = self.config.setdefault("saved_devices", [])
        self.config["saved_devices"] = [d for d in devices if d.get("address") != address]
        self.config.save()
        self.events.emit("saved_devices", self.saved_device_list())
        self._log(f"已删除设备记录: {address}")

    async def ble_reconnect_saved(self, address: str) -> None:
        for dev in self.config.get("saved_devices", []):
            if dev.get("address") == address:
                await self.ble_connect(address, dev.get("kind", "coyote_v3"))
                return
        raise RuntimeError(f"没有 {address} 的设备记录")

    async def _reconnect_loop(self) -> None:
        last_attempt: dict[str, float] = {}
        try:
            while True:
                await asyncio.sleep(2.0)
                if not self.config.get("auto_reconnect"):
                    continue
                backend = self._backend
                if not isinstance(backend, BleClient):
                    continue
                now = time.monotonic()
                saved = {d.get("address"): d for d in self.config.get("saved_devices", [])}
                for address in list(backend.dropped):
                    if now - last_attempt.get(address, 0.0) < 5.0:
                        continue
                    last_attempt[address] = now
                    dev = saved.get(address) or {}
                    name = dev.get("name", address)
                    kind = dev.get("kind", "coyote_v3")
                    self._log(f"自动重连 {name} …")
                    try:
                        await backend.connect(address, kind)
                        self._log(f"自动重连成功: {address}")
                    except Exception as exc:
                        self._log(f"自动重连失败 ({address}): {exc!r}")
        except asyncio.CancelledError:
            pass

    async def ble_scan(self, timeout: float = 6.0) -> list[dict]:
        return await BleClient.scan(timeout)

    def _require_backend(self):
        if self._backend is None:
            raise RuntimeError("没有已连接的设备")
        return self._backend

    def devices(self) -> list[dict]:
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

    def resolve_slot(self, slot_id: str | None = None, family: str | None = None,
                     output_only: bool = False) -> str | None:
        state = self.get_state()
        if slot_id and slot_id in state.slots:
            return slot_id
        if family:
            for dev in self.devices():
                if dev["family"] == family:
                    return dev["slot_id"]
        for sid in sorted(state.slots):
            if not output_only or state.slots[sid].is_output_device:
                return sid
        return None

    def _quantize_for_device(self, slot, value: int) -> int:
        if slot is not None and slot.type.upper().startswith("OVC"):
            return int(value / 10.0 + 0.5) * 10
        return value

    async def set_strength(self, channel: str, value: int, slot_id: str | None = None) -> None:
        backend = self._require_backend()
        limit = int(self.config["max_strength"])
        value = max(0, min(limit, int(value)))
        if isinstance(backend, SocketV4Client):
            sid = self.resolve_slot(slot_id, output_only=True)
            if sid is None:
                raise RuntimeError("V4 尚未接入设备")
            await backend.set_strength(channel, value, slot_id=sid)
        elif isinstance(backend, SocketV3Client):
            await backend.set_strength(channel, value)
        else:
            await backend.set_strength(channel, value, slot_id=self.resolve_slot(slot_id))

    async def add_strength(self, channel: str, delta: int, slot_id: str | None = None) -> None:
        backend = self._require_backend()
        sid = self.resolve_slot(slot_id, output_only=True)
        if isinstance(backend, SocketV4Client):
            slot = backend.state.slots.get(sid) if sid else None
            if slot is None:
                raise RuntimeError("V4 尚未接入设备")
            step = int(delta)
            if slot.type.upper().startswith("OVC"):
                step = max(10, round(abs(delta) / 10.0) * 10) * (1 if delta > 0 else -1)
            if step > 0 and slot.strength.get(channel, 0) >= int(self.config["max_strength"]):
                return
            await backend.add_intensity(channel, step, slot_id=sid)
        elif isinstance(backend, SocketV3Client):
            await backend.add_strength(channel, delta)
        else:
            await backend.add_strength(channel, delta, slot_id=sid)

    async def set_wave(self, channel: str, name: str, slot_id: str | None = None) -> None:
        backend = self._require_backend()
        self._selected_wave[channel] = name
        if isinstance(backend, SocketV4Client):
            await backend.set_wave(channel, name, slot_id=self.resolve_slot(slot_id, output_only=True))
        elif isinstance(backend, SocketV3Client):
            await backend.send_wave(
                channel, name, float(self.config["wave_duration_s"])
            )
        else:
            await backend.set_wave(channel, name, slot_id=self.resolve_slot(slot_id, output_only=True))

    async def reset_strength(self, channel: str, slot_id: str | None = None) -> None:
        backend = self._require_backend()
        sid = self.resolve_slot(slot_id, output_only=True)
        if isinstance(backend, SocketV4Client):
            await backend.reset_intensity(channel, slot_id=sid)
        elif isinstance(backend, SocketV3Client):
            await backend.set_strength(channel, 0)
        else:
            await backend.set_strength(channel, 0, slot_id=sid)
        await self.set_wave(channel, SILENT, slot_id=sid)

    async def clear_wave(self, channel: str | None = None, slot_id: str | None = None) -> None:
        backend = self._require_backend()
        sid = self.resolve_slot(slot_id, output_only=True)
        for ch in ("A", "B"):
            if channel in (None, ch):
                await self.set_wave(ch, SILENT, slot_id=sid)

    async def zap(self, channel: str, seconds: float = 1.0, slot_id: str | None = None) -> None:
        await self.fire(slot_id=slot_id, duration_s=seconds)

    async def emergency_stop(self) -> None:
        backend = self._require_backend()
        await backend.emergency_stop()

    def _fire_value(self, slot) -> int:
        cap = int(self.config.get("fire_strength") or 0)
        if cap <= 0:
            cap = int(self.config["max_strength"])
        cap = max(1, min(200, cap))
        if slot is not None:
            limit = min(slot.strength_limit.get("A", 200),
                        slot.strength_limit.get("B", 200))
            if limit > 0:
                cap = min(cap, limit)
            cap = self._quantize_for_device(slot, cap)
        return max(1, cap)

    def _fire_waves(self, sid: str) -> dict[str, str]:
        return {ch: str(self._selected_wave.get(ch) or SILENT) for ch in ("A", "B")}

    async def fire(self, slot_id: str | None = None, duration_s: float | None = None) -> None:
        backend = self._require_backend()
        sid = self.resolve_slot(slot_id, output_only=True)
        if sid is None:
            raise RuntimeError("没有可用设备")
        duration = float(duration_s or self.config["fire_duration_s"])
        state = self.get_state()
        slot = state.slots.get(sid)
        cap = self._fire_value(slot)
        original = self._fire_waves(sid)
        switched = [ch for ch in ("A", "B") if original[ch] in ("", SILENT)]
        try:
            for ch in switched:
                await self.set_wave(ch, CONTINUOUS, slot_id=sid)
            if isinstance(backend, (SocketV4Client, BleClient)):
                await backend.fire(slot_id=sid, duration_s=duration, value=cap)
            else:
                self._log("当前连接模式不支持一键开火")
                return
            await asyncio.sleep(duration)
        finally:
            for ch in switched:
                self._selected_wave[ch] = original[ch]
                try:
                    await self.set_wave(ch, original[ch], slot_id=sid)
                except Exception:
                    pass

    async def fire_start(self, slot_id: str | None = None) -> None:
        backend = self._require_backend()
        sid = self.resolve_slot(slot_id, output_only=True)
        if sid is None:
            raise RuntimeError("没有可用设备")
        if sid in self._fire_holds:
            return
        state = self.get_state()
        slot = state.slots.get(sid)
        value = self._fire_value(slot)
        hold = {
            "waves": self._fire_waves(sid),
            "strength": dict(slot.strength) if slot is not None else {"A": 0, "B": 0},
            "value": value,
            "applied": {"A": 0, "B": 0},
            "task": None,
        }
        self._fire_holds[sid] = hold
        try:
            for ch in ("A", "B"):
                if hold["waves"][ch] in ("", SILENT):
                    await self.set_wave(ch, CONTINUOUS, slot_id=sid)
            if isinstance(backend, SocketV4Client):
                for ch in ("A", "B"):
                    cur = slot.strength.get(ch, 0) if slot is not None else 0
                    if value > cur:
                        delta = value - cur
                        await backend.add_intensity(ch, delta, slot_id=sid)
                        hold["applied"][ch] = delta
            elif isinstance(backend, SocketV3Client):
                for ch in ("A", "B"):
                    await backend.set_strength(ch, value)
            else:
                for ch in ("A", "B"):
                    await backend.set_strength(ch, value, slot_id=sid)
        except Exception:
            self._fire_holds.pop(sid, None)
            raise
        hold["task"] = asyncio.create_task(self._fire_hold_timeout(sid))
        self._log(f"{sid} 触发开火开始 (强度 {value})")

    async def _fire_hold_timeout(self, sid: str) -> None:
        try:
            await asyncio.sleep(FIRE_HOLD_MAX_S)
        except asyncio.CancelledError:
            return
        self._log(f"{sid} 触发开火超时自动停止 (安全上限 {FIRE_HOLD_MAX_S:.0f}s)")
        try:
            await self.fire_stop(slot_id=sid)
        except Exception:
            pass

    async def fire_stop(self, slot_id: str | None = None) -> None:
        backend = self._require_backend()
        sid = self.resolve_slot(slot_id, output_only=True)
        hold = self._fire_holds.pop(sid, None)
        if hold is None:
            return
        task = hold.get("task")
        if task is not None:
            task.cancel()
        if isinstance(backend, SocketV4Client):
            raised = [
                ch for ch in ("A", "B")
                if hold["applied"].get(ch)
                and self.get_state().slots.get(sid) is not None
                and self.get_state().slots[sid].strength.get(ch, 0)
                > int(hold["strength"].get(ch, 0))
            ]
            for _ in range(10 if raised else 0):
                await asyncio.sleep(0.1)
                cur = self.get_state().slots.get(sid)
                if cur is None:
                    break
                if all(
                    ch not in raised
                    or cur.strength.get(ch, 0)
                    >= min(int(hold["value"]), int(hold["strength"].get(ch, 0))
                           + int(hold["applied"].get(ch, 0)))
                    for ch in ("A", "B")
                ):
                    break
        state = self.get_state()
        slot = state.slots.get(sid)
        for ch in ("A", "B"):
            wave = hold["waves"].get(ch, SILENT)
            self._selected_wave[ch] = wave
            try:
                await self.set_wave(ch, wave, slot_id=sid)
            except Exception:
                pass
        if isinstance(backend, SocketV4Client):
            for ch in ("A", "B"):
                cur = slot.strength.get(ch, 0) if slot is not None else 0
                delta = int(hold["strength"].get(ch, 0)) - cur
                if delta == 0:
                    delta = -int(hold["applied"].get(ch, 0))
                if delta:
                    try:
                        await backend.add_intensity(ch, delta, slot_id=sid)
                        self._log(f"{sid} 通道 {ch} 开火强度恢复 "
                                  f"{cur} → {int(hold['strength'].get(ch, 0))}"
                                  f" (增量 {delta:+d})")
                    except Exception as exc:
                        self._log(f"{sid} 开火恢复强度失败: {exc!r}")
        elif isinstance(backend, SocketV3Client):
            for ch in ("A", "B"):
                await backend.set_strength(ch, int(hold["strength"].get(ch, 0)))
        else:
            for ch in ("A", "B"):
                await backend.set_strength(ch, int(hold["strength"].get(ch, 0)),
                                           slot_id=sid)
        self._log(f"{sid} 触发开火结束 (强度与波形已恢复)")

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

    async def reset_pressure(self, slot_id: str | None = None) -> None:
        backend = self._require_backend()
        if isinstance(backend, BleClient):
            await backend.reset_pressure(slot_id=self.resolve_slot(slot_id, family="BMTR"))
        else:
            self._log("气压清零仅支持蓝牙直连的灵猫 (BMTR)")

    async def select_slot(self, slot_id: str) -> None:
        backend = self._require_backend()
        if isinstance(backend, SocketV4Client):
            backend.select_slot(slot_id)

    async def osc_start(self) -> None:
        if self.osc is None:
            self.osc = OscBridge(
                OscConfig(self.config["osc"]),
                self.get_state,
                self,
                events=self.events,
            )
            self.osc.log = self._log
        await self.osc.start()

    async def osc_stop(self) -> None:
        if self.osc:
            await self.osc.stop()

    def save_config(self) -> None:
        self.config.save()
        self._log("配置已保存")


def local_lan_ip() -> str:
    s = _socket.socket(_socket.AF_INET, _socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        s.close()


FIRE_HOLD_MAX_S = 60.0


__all__ = ["Config", "Engine", "local_lan_ip"]
