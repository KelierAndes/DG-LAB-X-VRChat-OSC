"""VRChat OSC bridge.

Follows the VRChat OSC avatar-parameters API
(https://docs.vrchat.com/docs/avatar-parameters):

* VRChat **listens** on UDP ``9000`` (configurable via ``--osc=in:ip:out``),
  and **sends** avatar parameter changes to ``9001`` by default.
* Parameter addresses are ``/avatar/parameters/<Name>``; types Int, Float
  and Bool.  We therefore *write* device state into avatar parameters and
  *read* avatar parameters back to control the device - the same direction
  of flow VRCOSC uses for its own parameters.

Output (device -> avatar, throttled to ``rate_hz``):
    <prefix>StrengthA / <prefix>StrengthB   Int  0-200 current strength
    <prefix>LimitA / <prefix>LimitB         Int  0-200 channel limit
    <prefix>Battery                         Int  0-100 (offline: 0)
    <prefix>Connected                       Bool paired and online
    <prefix>ChannelOK_A / <prefix>ChannelOK_B  Bool channel status ok
    <prefix>Pressure                       Float kPa (BMTR 灵猫 only)
    <prefix>EdgeState                      Int 0-4 edge-control state (BMTR)
    <prefix>Action                          Int  app button feedback 0-9,
                                                 auto-resets to 0 after 0.3 s

Input (avatar -> device), names configurable in config.json:
    <in_prefix>StrengthA / StrengthB   Int/Float  set absolute strength
    <in_prefix>WaveA / WaveB           Int        select waveform directly (0=静默,
                                                  1=持续, then official waveforms)
    <in_prefix>WaveStepA / WaveStepB   Int        wave stepper: non-zero = next
                                                  (positive) / previous (negative)
    <in_prefix>ZapA / ZapB             Bool/Int   1 s pulse burst
    <in_prefix>Fire                    Bool       触发式开火: press = start burst,
                                                  release = stop and restore
    <in_prefix>Emergency               Bool/Int   emergency stop
"""
from __future__ import annotations

import asyncio
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
    """OSC wave index order per device family (Int param -> wave name).

    Index 0 = 静默 (no output), 1 = 持续, then the official waveforms in
    enum order.  Per-family: the OVC set differs from the Coyote set.
    """
    if family == "OVC":
        return [SILENT, CONTINUOUS] + [w.value for w in OvcWaveform]
    return [SILENT, CONTINUOUS] + [w.value for w in CoyoteWaveform]


class OscConfig(dict):
    """Loose accessors with defaults; loaded from config.json."""

    DEFAULTS = {
        "enabled": True,
        "out_ip": "127.0.0.1",
        "out_port": 9000,
        "in_port": 9001,
        "prefix": "DGLab",
        "rate_hz": 10,
        # Avatar parameter names that control the device (must match the
        # parameters you added to your avatar).
        "in_strength_a": "DGLabStrengthA",
        "in_strength_b": "DGLabStrengthB",
        "in_wave_a": "DGLabWaveA",
        "in_wave_b": "DGLabWaveB",
        "in_zap_a": "DGLabZapA",
        "in_zap_b": "DGLabZapB",
        "in_emergency": "DGLabEmergency",
        "in_fire": "DGLabFire",
        # 波形步进 (加减): Int 非零即上一个/下一个波形, 与直接索引互补
        "in_wave_step_a": "DGLabWaveStepA",
        "in_wave_step_b": "DGLabWaveStepB",
        # 负鼠 (OVC) input parameters - independent from the Coyote set.
        "in_ovc_strength_a": "DGLabOvcInStrengthA",
        "in_ovc_strength_b": "DGLabOvcInStrengthB",
        "in_ovc_wave_a": "DGLabOvcInWaveA",
        "in_ovc_wave_b": "DGLabOvcInWaveB",
        "in_ovc_wave_step_a": "DGLabOvcInWaveStepA",
        "in_ovc_wave_step_b": "DGLabOvcInWaveStepB",
        "in_ovc_zap_a": "DGLabOvcInZapA",
        "in_ovc_zap_b": "DGLabOvcInZapB",
        "in_ovc_fire": "DGLabOvcInFire",
        # Per-family output parameter prefixes; the first device of a family
        # uses the plain prefix, further devices get an index (DGLab2, ...).
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
    """Pushes device state into VRChat avatar parameters and receives control."""

    def __init__(self, config: OscConfig, get_state, commands, events=None):
        """
        ``get_state``      -> callable returning the current :class:`EngineState`.
        ``commands``       -> object with async coroutines:
                              set_strength(ch, v), set_wave(ch, name),
                              zap(ch, seconds), emergency_stop().
        ``events``         -> optional StateEvents; used to catch App button
                              feedback pulses.
        """
        self.config = config
        self.get_state = get_state
        self.commands = commands

        self._client = SimpleUDPClient(config["out_ip"], int(config["out_port"]))
        self._dispatcher = Dispatcher()
        self._server = None  # OSC server (threaded)
        self._server_thread: threading.Thread | None = None
        self._task: asyncio.Task | None = None
        self._running = False
        self._loop: asyncio.AbstractEventLoop | None = None
        self._action_value = 0
        self._action_until = 0.0
        self._last_sent: dict[str, Any] = {}

        if events is not None:
            events.on("action", self._on_action)
        self._register_handlers()

    def _on_action(self, action: int | None) -> None:
        """App feedback button pressed -> pulse <prefix>Action for 0.3 s."""
        if action is None:
            return
        self._action_value = int(action)
        self._action_until = time.monotonic() + 0.3

    # ------------------------------------------------------------- handlers
    def _register_handlers(self) -> None:
        cfg = self.config

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
            """加减控制: Int 非 0 即步进一个波形 (正=下一个, 负=上一个, 循环)."""
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
            """触发式开火: True (按下) starts the burst, False (放开) stops it."""
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
            # New avatar loaded: push a full refresh.
            self._last_sent.clear()

        self._dispatcher.map(f"/avatar/parameters/{cfg['in_strength_a']}", make_set_strength("A"))
        self._dispatcher.map(f"/avatar/parameters/{cfg['in_strength_b']}", make_set_strength("B"))
        self._dispatcher.map(f"/avatar/parameters/{cfg['in_wave_a']}", make_wave("A"))
        self._dispatcher.map(f"/avatar/parameters/{cfg['in_wave_b']}", make_wave("B"))
        self._dispatcher.map(f"/avatar/parameters/{cfg['in_zap_a']}", make_zap("A"))
        self._dispatcher.map(f"/avatar/parameters/{cfg['in_zap_b']}", make_zap("B"))
        self._dispatcher.map(f"/avatar/parameters/{cfg['in_fire']}", make_fire("COYOTE"))
        self._dispatcher.map(f"/avatar/parameters/{cfg['in_emergency']}", emergency)
        self._dispatcher.map(f"/avatar/parameters/{cfg.get('in_wave_step_a', 'DGLabWaveStepA')}",
                             make_wave_step("A"))
        self._dispatcher.map(f"/avatar/parameters/{cfg.get('in_wave_step_b', 'DGLabWaveStepB')}",
                             make_wave_step("B"))
        # 负鼠 (OVC) independent input set.
        self._dispatcher.map(f"/avatar/parameters/{cfg['in_ovc_strength_a']}", make_set_strength("A", "OVC"))
        self._dispatcher.map(f"/avatar/parameters/{cfg['in_ovc_strength_b']}", make_set_strength("B", "OVC"))
        self._dispatcher.map(f"/avatar/parameters/{cfg['in_ovc_wave_a']}", make_wave("A", "OVC"))
        self._dispatcher.map(f"/avatar/parameters/{cfg['in_ovc_wave_b']}", make_wave("B", "OVC"))
        self._dispatcher.map(f"/avatar/parameters/{cfg.get('in_ovc_wave_step_a', 'DGLabOvcInWaveStepA')}",
                             make_wave_step("A", "OVC"))
        self._dispatcher.map(f"/avatar/parameters/{cfg.get('in_ovc_wave_step_b', 'DGLabOvcInWaveStepB')}",
                             make_wave_step("B", "OVC"))
        self._dispatcher.map(f"/avatar/parameters/{cfg['in_ovc_zap_a']}", make_zap("A", "OVC"))
        self._dispatcher.map(f"/avatar/parameters/{cfg['in_ovc_zap_b']}", make_zap("B", "OVC"))
        self._dispatcher.map(f"/avatar/parameters/{cfg['in_ovc_fire']}", make_fire("OVC"))
        self._dispatcher.map("/avatar/change", avatar_change)

    def _spawn(self, coro) -> None:
        """Schedule a command coroutine on the engine loop.

        OSC handlers run on the server's own thread, so the loop reference is
        captured at start() and coroutines are marshalled with
        run_coroutine_threadsafe.
        """
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

    # ------------------------------------------------------------- lifecycle
    async def start(self) -> None:
        if not self.config["enabled"] or self._running:
            return
        from pythonosc.osc_server import ThreadingOSCUDPServer

        self._running = True
        self._loop = asyncio.get_running_loop()
        self._server = ThreadingOSCUDPServer(
            ("127.0.0.1", int(self.config["in_port"])), self._dispatcher
        )
        self._server_thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._server_thread.start()
        self._task = asyncio.create_task(self._push_loop())
        self.log("[OSC] 桥接已启动: 输出 -> "
                 f"{self.config['out_ip']}:{self.config['out_port']}, "
                 f"监听 127.0.0.1:{self.config['in_port']}")

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

    log = print  # replaced by engine with a real logger

    # ------------------------------------------------------------ push loop
    async def _push_loop(self) -> None:
        interval = 1.0 / max(1, int(self.config["rate_hz"]))
        prefix = self.config["prefix"]
        try:
            while self._running:
                await asyncio.sleep(interval)
                state = self.get_state()
                self._push_state(state, prefix)
        except asyncio.CancelledError:
            pass

    def _push_state(self, state: EngineState, prefix: str) -> None:
        # Per-device parameters, grouped and indexed by device family.
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

        # App button feedback pulse (Int auto-reset), global.
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
        """Device addressed by an input family: the FIRST connected device
        of that family (Coyote inputs -> first Coyote, OVC inputs -> first
        OVC).  Falls back to the first OUTPUT device (never the BMTR sensor)
        when the family is absent."""
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
            pass  # VRChat not running; keep trying silently


def _to_int(value: Any, low: int, high: int) -> int:
    try:
        return max(low, min(high, int(value)))
    except (TypeError, ValueError):
        return low


def device_osc_names(state: EngineState, prefixes: dict) -> dict[str, dict[str, str]]:
    """slot_id -> {family, name}: stable per-family indexed OSC base names.

    The first device of each family uses the plain family prefix (e.g.
    ``DGLab``); further devices of the same family get an index (``DGLab2``,
    ``DGLab3``, ...), so every connected device owns an independent set of
    avatar parameters.
    """
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
