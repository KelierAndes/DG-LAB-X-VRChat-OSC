from __future__ import annotations

import asyncio
import socket
import threading
import time
from typing import Any

from pythonosc.dispatcher import Dispatcher
from pythonosc.osc_message_builder import OscMessageBuilder
from pythonosc.udp_client import SimpleUDPClient

from dglab.waves import CONTINUOUS, SILENT
from dglab.state import EngineState, family_of
from dglab.waves import COYOTE_WAVEFORMS, CoyoteWaveform
from dglab.official_waveforms_ovc import OvcWaveform


def wave_order(family: str = "COYOTE") -> list[str]:
    if family == "OVC":
        return [SILENT, CONTINUOUS] + [w.value for w in OvcWaveform]
    return [SILENT, CONTINUOUS] + [w.value for w in CoyoteWaveform]


class OscConfig(dict):
    DEFAULTS = {
        "enabled": True,
        "out_ip": "127.0.0.1",
        "out_port": 9000,
        "in_port": 9001,
        "prefix": "DGLab",
        "rate_hz": 10,
        "in_strength_a": "DGLabStrengthA",
        "in_strength_b": "DGLabStrengthB",
        "in_wave_a": "DGLabWaveA",
        "in_wave_b": "DGLabWaveB",
        "in_zap_a": "DGLabZapA",
        "in_zap_b": "DGLabZapB",
        "in_emergency": "DGLabEmergency",
        "in_fire": "DGLabFire",
        "in_wave_step_a": "DGLabWaveStepA",
        "in_wave_step_b": "DGLabWaveStepB",
        "in_ovc_strength_a": "DGLabOvcInStrengthA",
        "in_ovc_strength_b": "DGLabOvcInStrengthB",
        "in_ovc_wave_a": "DGLabOvcInWaveA",
        "in_ovc_wave_b": "DGLabOvcInWaveB",
        "in_ovc_wave_step_a": "DGLabOvcInWaveStepA",
        "in_ovc_wave_step_b": "DGLabOvcInWaveStepB",
        "in_ovc_zap_a": "DGLabOvcInZapA",
        "in_ovc_zap_b": "DGLabOvcInZapB",
        "in_ovc_fire": "DGLabOvcInFire",
        "device_prefixes": {
            "COYOTE": "DGLab",
            "OVC": "DGLabOvc",
            "BMTR": "DGLabBmtr",
        },
    }

    def __init__(self, data: dict | None = None):
        super().__init__(self.DEFAULTS)
        if data:
            self.update({k: v for k, v in data.items() if v is not None})


class OscBridge:
    def __init__(self, config: OscConfig, get_state, commands, events=None):
        self.config = config
        self.get_state = get_state
        self.commands = commands

        self._client = SimpleUDPClient(config["out_ip"], int(config["out_port"]))
        self._dispatcher = Dispatcher()
        self._server = None
        self._server_thread: threading.Thread | None = None
        self._task: asyncio.Task | None = None
        self._running = False
        self._loop: asyncio.AbstractEventLoop | None = None
        self._out_ip: str = str(config["out_ip"])
        self._action_value = 0
        self._action_until = 0.0
        self._last_sent: dict[str, Any] = {}
        self.last_rx: float | None = None
        self.rx_count = 0

        if events is not None:
            events.on("action", self._on_action)
        self._register_handlers()

    def _on_action(self, action: int | None) -> None:
        if action is None:
            return
        self._action_value = int(action)
        self._action_until = time.monotonic() + 0.3

    def send_value(self, address: str, value) -> None:
        try:
            self._client.send_message(address, value)
        except Exception as exc:
            log = getattr(self, "log", None)
            if log is not None:
                log(f"OSC 发送 {address} 失败: {exc!r}")

    def _map(self, address: str, handler) -> None:
        def tracked(addr, *args):
            self.last_rx = time.monotonic()
            self.rx_count += 1
            handler(addr, *args)

        self._dispatcher.map(address, tracked)

    def _register_handlers(self) -> None:
        cfg = self.config

        def track_default(addr, *args):
            self.last_rx = time.monotonic()
            self.rx_count += 1

        self._dispatcher.set_default_handler(track_default)

        def make_set_strength(ch: str, family: str = "COYOTE"):
            def handler(_addr, *args):
                if not args:
                    return
                value = _to_int(args[0], 0, 200)
                self._spawn(
                    self.commands.set_strength(ch, value, slot_id=self.input_target_slot(family))
                )

            return handler

        def make_wave(ch: str, family: str = "COYOTE"):
            def handler(_addr, *args):
                if not args:
                    return
                idx = _to_int(args[0], 0, len(wave_order(family)) - 1)
                name = wave_order(family)[idx]
                self._spawn(
                    self.commands.set_wave(ch, name, slot_id=self.input_target_slot(family))
                )

            return handler

        def make_wave_step(ch: str, family: str = "COYOTE"):
            def handler(_addr, *args):
                if not args:
                    return
                step = _to_int(args[0], -1, 1)
                if step == 0:
                    return
                order = wave_order(family)
                current = str(getattr(self.commands, "_selected_wave", {}).get(ch)
                              or order[0])
                idx = order.index(current) if current in order else 0
                name = order[(idx + (1 if step > 0 else -1)) % len(order)]
                self._spawn(
                    self.commands.set_wave(ch, name, slot_id=self.input_target_slot(family))
                )

            return handler

        def make_zap(ch: str, family: str = "COYOTE"):
            def handler(_addr, *args):
                if not args:
                    return
                if _truthy(args[0]):
                    self._spawn(
                        self.commands.zap(ch, 1.0, slot_id=self.input_target_slot(family))
                    )

            return handler

        def make_fire(family: str = "COYOTE"):
            def handler(_addr, *args):
                if not args:
                    return
                slot = self.input_target_slot(family)
                if _truthy(args[0]):
                    self._spawn(self.commands.fire_start(slot_id=slot))
                else:
                    self._spawn(self.commands.fire_stop(slot_id=slot))

            return handler

        def emergency(_addr, *args):
            if args and _truthy(args[0]):
                self._spawn(self.commands.emergency_stop())

        def avatar_change(_addr, *args):
            self._last_sent.clear()

        self._map(f"/avatar/parameters/{cfg['in_strength_a']}", make_set_strength("A"))
        self._map(f"/avatar/parameters/{cfg['in_strength_b']}", make_set_strength("B"))
        self._map(f"/avatar/parameters/{cfg['in_wave_a']}", make_wave("A"))
        self._map(f"/avatar/parameters/{cfg['in_wave_b']}", make_wave("B"))
        self._map(f"/avatar/parameters/{cfg['in_zap_a']}", make_zap("A"))
        self._map(f"/avatar/parameters/{cfg['in_zap_b']}", make_zap("B"))
        self._map(f"/avatar/parameters/{cfg['in_fire']}", make_fire("COYOTE"))
        self._map(f"/avatar/parameters/{cfg['in_emergency']}", emergency)
        self._map(f"/avatar/parameters/{cfg.get('in_wave_step_a', 'DGLabWaveStepA')}",
                             make_wave_step("A"))
        self._map(f"/avatar/parameters/{cfg.get('in_wave_step_b', 'DGLabWaveStepB')}",
                             make_wave_step("B"))
        self._map(f"/avatar/parameters/{cfg['in_ovc_strength_a']}", make_set_strength("A", "OVC"))
        self._map(f"/avatar/parameters/{cfg['in_ovc_strength_b']}", make_set_strength("B", "OVC"))
        self._map(f"/avatar/parameters/{cfg['in_ovc_wave_a']}", make_wave("A", "OVC"))
        self._map(f"/avatar/parameters/{cfg['in_ovc_wave_b']}", make_wave("B", "OVC"))
        self._map(f"/avatar/parameters/{cfg.get('in_ovc_wave_step_a', 'DGLabOvcInWaveStepA')}",
                             make_wave_step("A", "OVC"))
        self._map(f"/avatar/parameters/{cfg.get('in_ovc_wave_step_b', 'DGLabOvcInWaveStepB')}",
                             make_wave_step("B", "OVC"))
        self._map(f"/avatar/parameters/{cfg['in_ovc_zap_a']}", make_zap("A", "OVC"))
        self._map(f"/avatar/parameters/{cfg['in_ovc_zap_b']}", make_zap("B", "OVC"))
        self._map(f"/avatar/parameters/{cfg['in_ovc_fire']}", make_fire("OVC"))
        self._map("/avatar/change", avatar_change)

    def _spawn(self, coro) -> None:
        loop = self._loop
        if loop is None or loop.is_closed():
            coro.close()
            return
        try:
            running = asyncio.get_running_loop()
        except RuntimeError:
            running = None
        if running is loop:
            loop.create_task(coro)
        else:
            asyncio.run_coroutine_threadsafe(coro, loop)

    @staticmethod
    def _local_ip() -> str:
        """本机出口网卡 IP (UDP connect 不发包, 仅查路由)."""
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            try:
                s.connect(("8.8.8.8", 80))
                return s.getsockname()[0]
            finally:
                s.close()
        except Exception:
            return "127.0.0.1"

    async def start(self) -> None:
        if not self.config["enabled"] or self._running:
            return
        from pythonosc.osc_server import ThreadingOSCUDPServer

        self._running = True
        self._loop = asyncio.get_running_loop()

        out_ip = str(self.config["out_ip"])
        if out_ip in ("127.0.0.1", "localhost", "::1"):
            resolved = self._local_ip()
            if resolved and resolved != out_ip:
                self.log(f"[OSC] 发送目标由 {out_ip} 改为本机网卡地址 {resolved}"
                         f" (部分加速器/驱动会拦截回环 UDP, 127.0.0.1 收不到)")
                out_ip = resolved
        self._client = SimpleUDPClient(out_ip, int(self.config["out_port"]))

        self._server = ThreadingOSCUDPServer(
            ("0.0.0.0", int(self.config["in_port"])), self._dispatcher
        )
        self._server_thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._server_thread.start()
        self._task = asyncio.create_task(self._push_loop())
        self.log("[OSC] 桥接已启动: 输出 -> "
                 f"{out_ip}:{self.config['out_port']}, "
                 f"监听 0.0.0.0:{self.config['in_port']}")

    async def stop(self) -> None:
        self._running = False
        if self._task:
            self._task.cancel()
            self._task = None
        if self._server:
            self._server.shutdown()
            self._server.server_close()
            self._server = None
        self.log("[OSC] 桥接已停止")

    log = print

    async def _push_loop(self) -> None:
        interval = 1.0 / max(1, int(self.config["rate_hz"]))
        prefix = self.config["prefix"]
        tick = 0
        try:
            while self._running:
                await asyncio.sleep(interval)
                state = self.get_state()
                self._push_state(state, prefix)
                tick += 1
                if tick % 10 == 0:
                    self._send_keepalive()
        except asyncio.CancelledError:
            pass

    def _send_keepalive(self) -> None:
        """从接收端口向 VRChat 发注册包, 使其把回传 OSC 发往本机网卡地址.

        VRChat 只向"最近发来 OSC 的地址"回传数据; 若回环 UDP 被加速器等拦截,
        用 127.0.0.1 学到的地址会导致反向链路同样失效, 故从 0.0.0.0:in_port
        的套接字发往网卡地址, 让 VRChat 学到 (网卡IP, in_port)。
        """
        server = self._server
        if server is None:
            return
        try:
            builder = OscMessageBuilder("/dglab/keepalive")
            builder.add_arg(1)
            server.socket.sendto(builder.build().dgram,
                                 (self._out_ip, int(self.config["out_port"])))
        except Exception:
            pass

    def _push_state(self, state: EngineState, prefix: str) -> None:
        values: dict[str, Any] = {}
        for slot_id, base in self._device_names(state).items():
            slot = state.slots.get(slot_id)
            if slot is None:
                continue
            paired = state.paired and state.connected
            if base["family"] == "BMTR":
                values[f"{base['name']}Pressure"] = float(slot.pressure or 0.0)
                values[f"{base['name']}EdgeState"] = int(slot.edge_state or 0)
            else:
                values[f"{base['name']}StrengthA"] = slot.strength["A"]
                values[f"{base['name']}StrengthB"] = slot.strength["B"]
                values[f"{base['name']}LimitA"] = slot.strength_limit["A"]
                values[f"{base['name']}LimitB"] = slot.strength_limit["B"]
                values[f"{base['name']}ChannelOK_A"] = bool(slot.channel_status["A"] in (0, 2))
                values[f"{base['name']}ChannelOK_B"] = bool(slot.channel_status["B"] in (0, 2))
            values[f"{base['name']}Battery"] = (slot.battery or 0)
            values[f"{base['name']}Connected"] = paired

        now = time.monotonic()
        if now < self._action_until:
            values[f"{prefix}Action"] = self._action_value
        elif self._last_sent.get(f"{prefix}Action", 0) != 0:
            values[f"{prefix}Action"] = 0

        for name, value in values.items():
            if self._last_sent.get(name) != value:
                self._send_param(name, value)
                self._last_sent[name] = value

    def _device_names(self, state: EngineState) -> dict[str, dict[str, str]]:
        return device_osc_names(state, self.config["device_prefixes"])

    def input_target_slot(self, family: str = "COYOTE") -> str | None:
        state = self.get_state()
        slots = {sid: state.slots[sid] for sid in sorted(state.slots)}
        for sid, slot in slots.items():
            if family_of(slot.type) == family:
                return sid
        for sid, slot in slots.items():
            if family == "BMTR" or family_of(slot.type) != "BMTR":
                return sid
        return None

    def _send_param(self, name: str, value: Any) -> None:
        builder = OscMessageBuilder(address=f"/avatar/parameters/{name}")
        if isinstance(value, bool):
            builder.add_arg(bool(value))
        elif isinstance(value, int):
            builder.add_arg(int(value))
        elif isinstance(value, float):
            builder.add_arg(float(value))
        else:
            return
        try:
            self._client.send(builder.build())
        except OSError:
            pass


def _to_int(value: Any, low: int, high: int) -> int:
    try:
        return max(low, min(high, int(value)))
    except (TypeError, ValueError):
        return low


def device_osc_names(state: EngineState, prefixes: dict) -> dict[str, dict[str, str]]:
    families: dict[str, list[str]] = {}
    for slot_id in sorted(state.slots):
        slot = state.slots[slot_id]
        families.setdefault(family_of(slot.type), []).append(slot_id)
    names: dict[str, dict[str, str]] = {}
    for family, slot_ids in families.items():
        base = (prefixes or {}).get(family, f"DGLab{family.capitalize()}")
        for index, slot_id in enumerate(slot_ids, start=1):
            names[slot_id] = {
                "family": family,
                "name": base if index == 1 else f"{base}{index}",
            }
    return names


def _truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value > 0
    return False
