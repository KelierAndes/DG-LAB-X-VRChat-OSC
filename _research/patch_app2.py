# -*- coding: utf-8 -*-
"""Fix app.py: reset_strength + set_wave dispatch (previously lost patch),
file logging, saved-device registry with auto-reconnect."""
import io

src = io.open('app.py', encoding='utf-8').read()

# --- 1) set_wave dispatch fix (V4 -> set_wave continuous) -----------------
old = '''        if isinstance(backend, SocketV4Client):
            sid = self.resolve_slot(slot_id)
            await backend.send_wave(
                channel, name, float(self.config["wave_duration_s"]), slot_id=sid
            )
        elif isinstance(backend, SocketV3Client):
            await backend.send_wave(
                channel, name, float(self.config["wave_duration_s"])
            )
        else:
            await backend.set_wave(channel, name, slot_id=self.resolve_slot(slot_id))'''
new = '''        if isinstance(backend, SocketV4Client):
            # V4 uses the continuous sender: switch the looping waveform.
            await backend.set_wave(channel, name, slot_id=self.resolve_slot(slot_id))
        elif isinstance(backend, SocketV3Client):
            await backend.send_wave(
                channel, name, float(self.config["wave_duration_s"])
            )
        else:
            await backend.set_wave(channel, name, slot_id=self.resolve_slot(slot_id))'''
assert old in src, "set_wave dispatch"
src = src.replace(old, new)

# --- 2) reset_strength -----------------------------------------------------
old = '''    async def clear_wave(self, channel: str | None = None, slot_id: str | None = None) -> None:'''
new = '''    async def reset_strength(self, channel: str, slot_id: str | None = None) -> None:
        """归零一个通道的强度 (保留波形持续发送, 再次加强度立即有输出)。"""
        backend = self._require_backend()
        sid = self.resolve_slot(slot_id)
        if isinstance(backend, SocketV4Client):
            await backend.reset_intensity(channel, slot_id=sid)
        elif isinstance(backend, SocketV3Client):
            await backend.set_strength(channel, 0)
        else:
            await backend.set_strength(channel, 0, slot_id=sid)

    async def clear_wave(self, channel: str | None = None, slot_id: str | None = None) -> None:'''
assert old in src, "reset_strength anchor"
src = src.replace(old, new)

# --- 3) config: logging + saved devices ------------------------------------
old = '''        "relay": {"v4_port": 9998, "v3_port": 9999},
    }'''
new = '''        "relay": {"v4_port": 9998, "v3_port": 9999},
        "log_frames": True,
        "auto_reconnect": True,
        "saved_devices": [],
    }'''
assert old in src, "config defaults"
src = src.replace(old, new)

# --- 4) file logger + frame logging ----------------------------------------
old = '''class Engine:
    def __init__(self, config_path: str | None = None):
        self.config = Config(config_path)'''
new = '''import logging


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
        self._file_log = _setup_file_logger(_base_dir())'''
assert old in src, "file logger"
src = src.replace(old, new)

old = '''    # ----------------------------------------------------------------- log
    def _log(self, msg: str) -> None:
        self.events.emit("log", msg)'''
new = '''    # ----------------------------------------------------------------- log
    def _log(self, msg: str) -> None:
        self.events.emit("log", msg)
        try:
            self._file_log.info(msg)
        except Exception:
            pass

    def log_frame(self, direction: str, frame: dict) -> None:
        """Verbose protocol frame logging (socket mode)."""
        if not self.config.get("log_frames"):
            return
        try:
            text = json.dumps(frame, ensure_ascii=False, separators=(",", ":"))
        except Exception:
            text = str(frame)
        if len(text) > 300:
            text = text[:300] + f"...(+{len(text) - 300})"
        self._log(f"{direction} {text}")'''
assert old in src, "log_frame"
src = src.replace(old, new)

# engine.start(): enable client frame logging + reconnect monitor
old = '''    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._loop_ready = threading.Event()
        self._thread = threading.Thread(target=self._run_loop, name="dglab-engine", daemon=True)
        self._thread.start()
        if not self._loop_ready.wait(timeout=5.0):
            raise RuntimeError("engine loop failed to start")'''
new = '''    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._loop_ready = threading.Event()
        self._thread = threading.Thread(target=self._run_loop, name="dglab-engine", daemon=True)
        self._thread.start()
        if not self._loop_ready.wait(timeout=5.0):
            raise RuntimeError("engine loop failed to start")
        self.submit(self._reconnect_loop())'''
assert old in src, "start"
src = src.replace(old, new)

# --- 5) saved-device registry + reconnect loop ------------------------------
old = '''    async def ble_disconnect_device(self, slot_id: str) -> None:
        if isinstance(self._backend, BleClient):
            await self._backend.disconnect(slot_id)'''
new = '''    async def ble_disconnect_device(self, slot_id: str) -> None:
        if isinstance(self._backend, BleClient):
            await self._backend.disconnect(slot_id)

    # ------------------------------------------------- saved devices (BLE)
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
        """Device-centric auto reconnect: re-connect saved BLE devices whose
        connection dropped, until the user disconnects on purpose."""
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
                for dev in self.config.get("saved_devices", []):
                    address = dev.get("address")
                    if not address or address in backend.sessions:
                        continue
                    if now - last_attempt.get(address, 0.0) < 5.0:
                        continue
                    last_attempt[address] = now
                    self._log(f"自动重连 {dev.get('name', address)} …")
                    try:
                        await backend.connect(address, dev.get("kind", "coyote_v3"))
                        self._log(f"自动重连成功: {address}")
                    except Exception as exc:
                        self._log(f"自动重连失败 ({address}): {exc!r}")
        except asyncio.CancelledError:
            pass'''
assert old in src, "saved devices"
src = src.replace(old, new)

# remember device after successful BLE connect
old = '''        try:
            await client.connect(address, kind)
        except Exception:
            if not client.sessions:
                self._backend = None
            raise'''
new = '''        try:
            await client.connect(address, kind)
        except Exception:
            if not client.sessions:
                self._backend = None
            raise
        slot = client.state.slots.get(address)
        self.remember_device(address, kind, (slot.name if slot else None) or kind)'''
assert old in src, "remember"
src = src.replace(old, new)

# remember_device needs time import — app.py imports time? check & add
if "\nimport time\n" not in src:
    src = src.replace("import sys\nimport threading", "import sys\nimport threading\nimport time")

io.open('app.py', 'w', encoding='utf-8').write(src)
print("app.py fixed (all 5 areas)")
