# -*- coding: utf-8 -*-
"""V4 fixes: merge devices.get responses, drop im/p, adaptive strength op,
log resp frames."""
import io

src = io.open('dglab/socket_v4.py', encoding='utf-8').read()

# --- 1) merge instead of wipe on devices.get resp / snapshot ---------------
old = '''    def _replace_devices(self, cid: str, devices: list) -> None:
        entry = self._clients.setdefault(cid, {"devices": {}})
        entry["devices"] = {}
        for dev in devices:
            if isinstance(dev, dict) and dev.get("slotId"):
                entry["devices"][str(dev["slotId"])] = copy.deepcopy(dev)
        self._sync_state(cid)'''
new = '''    def _replace_devices(self, cid: str, devices: list) -> None:
        entry = self._clients.setdefault(cid, {"devices": {}})
        # Merge: devices.get responses may carry descriptors WITHOUT props -
        # wiping here would lose battery/strength reported by the snapshot.
        merged: dict[str, dict] = {}
        for dev in devices:
            if isinstance(dev, dict) and dev.get("slotId"):
                sid = str(dev["slotId"])
                old = entry["devices"].get(sid, {})
                if "props" not in dev and old.get("props"):
                    dev = {**dev, "props": old["props"]}
                if "slotState" not in dev and old.get("slotState"):
                    dev = {**dev, "slotState": old["slotState"]}
                merged[sid] = copy.deepcopy(dev)
        entry["devices"] = merged
        self._sync_state(cid)'''
assert old in src, "replace merge"
src = src.replace(old, new)

# --- 2) drop im/p from intensity ops (official SDK sends plain ops) --------
old = '''    async def add_intensity(self, channel: str, value: float,
                            slot_id: str | None = None) -> None:
        cid, sid = self._require_peer(slot_id)
        await self._operate(
            {"s": sid, "c": CHANNELS.index(channel), "t": ACTION_ADD, "v": value,
             "im": True, "p": 2}
        )

    async def set_temp_intensity(self, channel: str, value: float, duration_ms: int,
                                 slot_id: str | None = None) -> None:
        cid, sid = self._require_peer(slot_id)
        await self._operate(
            {"s": sid, "c": CHANNELS.index(channel), "t": ACTION_TEMP, "v": value,
             "d": duration_ms, "p": 2}
        )

    async def reset_intensity(self, channel: str | None = None, slot_id: str | None = None) -> None:
        cid, sid = self._require_peer(slot_id)
        for ch in CHANNELS:
            if channel in (None, ch):
                await self._operate(
                    {"s": sid, "c": CHANNELS.index(ch), "t": ACTION_RESET, "v": 0,
                     "im": True, "p": 2}
                )'''
new = '''    async def add_intensity(self, channel: str, value: float,
                            slot_id: str | None = None) -> None:
        cid, sid = self._require_peer(slot_id)
        await self._operate(
            {"s": sid, "c": CHANNELS.index(channel), "t": ACTION_ADD, "v": value}
        )

    async def set_intensity(self, channel: str, value: float,
                            slot_id: str | None = None) -> None:
        """Absolute strength set (t=7).  The official doc restricts t=7 to 0
        for Coyote, but some devices (OVC) only respond to absolute sets."""
        cid, sid = self._require_peer(slot_id)
        await self._operate(
            {"s": sid, "c": CHANNELS.index(channel), "t": ACTION_RESET, "v": value}
        )

    async def set_temp_intensity(self, channel: str, value: float, duration_ms: int,
                                 slot_id: str | None = None) -> None:
        cid, sid = self._require_peer(slot_id)
        await self._operate(
            {"s": sid, "c": CHANNELS.index(channel), "t": ACTION_TEMP, "v": value,
             "d": duration_ms}
        )

    async def reset_intensity(self, channel: str | None = None, slot_id: str | None = None) -> None:
        cid, sid = self._require_peer(slot_id)
        for ch in CHANNELS:
            if channel in (None, ch):
                await self._operate(
                    {"s": sid, "c": CHANNELS.index(ch), "t": ACTION_RESET, "v": 0}
                )'''
assert old in src, "ops im/p"
src = src.replace(old, new)

# estop resets: drop im/p too
old = '''                for ch in CHANNELS:
                    try:
                        await self._operate(
                            {"s": sid, "c": CHANNELS.index(ch), "t": ACTION_RESET,
                             "v": 0, "im": True, "p": 2}
                        )
                    except Exception as exc:
                        self._log(f"急停 {sid}/{ch} 清零失败: {exc!r}")'''
new = '''                for ch in CHANNELS:
                    try:
                        await self._operate(
                            {"s": sid, "c": CHANNELS.index(ch), "t": ACTION_RESET, "v": 0}
                        )
                    except Exception as exc:
                        self._log(f"急停 {sid}/{ch} 清零失败: {exc!r}")'''
assert old in src, "estop im/p"
src = src.replace(old, new)

# --- 3) adaptive strength: add first, fall back to absolute set ------------
old = '''    # ------------------------------------------------------------ public ops
    def select_slot(self, slot_id: str) -> None:'''
new = '''    async def set_strength(self, channel: str, value: int, slot_id: str | None = None) -> None:
        """Adaptive absolute-strength control.

        Tries a relative AddIntensity (t=3) first; if the App does not
        confirm the new value via slots.patch within ~1.2 s (some devices,
        e.g. OVC, ignore relative adds), falls back to an absolute
        SetIntensity (t=7) and remembers the working mode per slot.
        """
        cid, sid = self._require_peer(slot_id)
        slot = self.state.slots.get(sid)
        if slot is None:
            raise RuntimeError(f"设备不存在: {sid}")
        value = max(0, min(200, int(value)))
        current = slot.strength.get(channel, 0)
        if value == current:
            return
        mode = self._strength_mode.get(sid, "add")

        if mode == "add":
            await self.add_intensity(channel, value - current, slot_id=sid)
            if await self._wait_strength(sid, channel, value, 1.2):
                return
            self._strength_mode[sid] = "set"
            self._log(f"{sid} AddIntensity 未生效，切换为绝对强度指令 (t=7)")
        await self.set_intensity(channel, value, slot_id=sid)
        if not await self._wait_strength(sid, channel, value, 1.2):
            if mode == "set":
                self._log(f"{sid} 绝对强度指令也未生效 (目标 {value})")

    async def _wait_strength(self, slot_id: str, channel: str, value: int,
                             timeout: float) -> bool:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            slot = self.state.slots.get(slot_id)
            if slot is not None and slot.strength.get(channel) == value:
                return True
            await asyncio.sleep(0.1)
        slot = self.state.slots.get(slot_id)
        return slot is not None and slot.strength.get(channel) == value

    # ------------------------------------------------------------ public ops
    def select_slot(self, slot_id: str) -> None:'''
assert old in src, "adaptive"
src = src.replace(old, new)

old = '''        self._wave_task: asyncio.Task | None = None
        self._props_logged: set[str] = set()'''
new = '''        self._wave_task: asyncio.Task | None = None
        self._props_logged: set[str] = set()
        self._strength_mode: dict[str, str] = {}  # slotId -> "add" | "set"'''
assert old in src, "strength mode field"
src = src.replace(old, new)

# --- 4) log resp frames (errors are invisible otherwise) -------------------
old = '''    def _log_frame(self, direction: str, frame: dict) -> None:
        ftype = frame.get("type")
        if ftype in ("ping", "pong", "heartbeat"):
            return
        if isinstance(frame.get("data"), dict) and frame["data"].get("t") == "resp":
            return  # responses are reflected in device state already
        self.events.emit("frame_log", direction, frame)'''
new = '''    def _log_frame(self, direction: str, frame: dict) -> None:
        ftype = frame.get("type")
        if ftype in ("ping", "pong", "heartbeat"):
            return
        data = frame.get("data")
        if isinstance(data, dict) and data.get("t") == "resp":
            # Responses matter for diagnostics: log errors always, results
            # under verbose frame logging (they are reflected in state too).
            if data.get("error"):
                self.events.emit("log", f"[V4] 指令错误: {data.get('error')} req={data.get('reqId')}")
                return
        self.events.emit("frame_log", direction, frame)'''
assert old in src, "resp logging"
src = src.replace(old, new)

io.open('dglab/socket_v4.py', 'w', encoding='utf-8').write(src)
print("socket_v4 fixes OK")
