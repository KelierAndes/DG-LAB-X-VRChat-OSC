
from __future__ import annotations

import threading
import time
from collections import deque

from dglab.official_waveforms import COYOTE_WAVEFORMS, CoyoteWaveform
from dglab.official_waveforms_ovc import OVC_WAVEFORMS, OvcWaveform
from dglab.state import EngineState, family_of
from dglab.waves import CONTINUOUS, SILENT

FAMILIES = ("COYOTE", "OVC", "BMTR")
FAMILY_LABELS = {"COYOTE": "郊狼 (电刺激)", "OVC": "负鼠 (振动)", "BMTR": "灵猫 (气压)"}
FAMILY_SYMBOLS = {"COYOTE": "Remote", "OVC": "CellPhone", "BMTR": "Target"}

OVC_BUTTON_BITS = [
    (0, "SEL_1"), (1, "SEL_2"), (2, "HOME"),
    (8, "Up"), (9, "Down"), (10, "Left"), (11, "Right"),
    (12, "B"), (13, "A"), (14, "G"), (15, "D"),
]
BUTTON_ACTIONS = [
    ("none", "无"),
    ("a_strength_up", "A 通道强度 +10"), ("a_strength_down", "A 通道强度 -10"),
    ("a_strength_zero", "A 通道强度 归0"),
    ("a_wave_up", "A 切换上一个波形"), ("a_wave_down", "A 切换下一个波形"),
    ("b_strength_up", "B 通道强度 +10"), ("b_strength_down", "B 通道强度 -10"),
    ("b_strength_zero", "B 通道强度 归0"),
    ("b_wave_up", "B 切换上一个波形"), ("b_wave_down", "B 切换下一个波形"),
    ("fire", "持续开火 (按住开火)"), ("estop", "急停"),
    ("osc", "发送 OSC 参数…"), ("key", "模拟键盘按键…"),
]
BUTTON_ACTION_LABELS = dict(BUTTON_ACTIONS)

BACKEND_LABELS = {
    "none": "未连接",
    "v4": "Socket V4",
    "v3": "Socket V3",
    "ble": "蓝牙直连",
}

EDGE_STATES = {0: "停止", 1: "刺激", 2: "冷静计时", 3: "冷静判定", 4: "允许高潮"}

PRESSURE_MIN_KPA = 0.0
PRESSURE_MAX_KPA = 60.0
PRESSURE_WINDOW_S = 60.0
PRESSURE_COLORS = ("#3b82d0", "#d64541", "#48aa60", "#a05ac8")

LOG_LEVELS = ("全部", "调试", "信息", "警告", "错误")
LEVEL_LABELS = {"debug": "调试", "info": "信息", "warn": "警告", "error": "错误"}

def wave_items(family: str = "COYOTE") -> list[tuple[str, str]]:
    items: list[tuple[str, str]] = [("静默 (无输出)", SILENT)]
    if family == "OVC":
        table, enum_cls = OVC_WAVEFORMS, OvcWaveform
    else:
        table, enum_cls = COYOTE_WAVEFORMS, CoyoteWaveform
    for wave in enum_cls:
        label = table[wave].get("label", {})
        cn = label.get("cn") or wave.value
        items.append((f"{cn} ({wave.value})", wave.value))
    items.append(("持续 (Continuous)", CONTINUOUS))
    return items

def wave_label(value: str, family: str = "COYOTE") -> str:
    for label, item in wave_items(family):
        if item == value:
            return label
    return value or SILENT

def classify_log(msg: str) -> str:
    if any(k in msg for k in ("失败", "错误", "异常", "Traceback")):
        return "error"
    if any(k in msg for k in ("警告", "重试", "超时", "丢包", "限幅")):
        return "warn"
    if msg.startswith(("<", ">>", "{")) or " frame" in msg.lower():
        return "debug"
    return "info"

class LogBuffer:

    def __init__(self, maxlen: int = 800):
        self._items: deque[tuple[float, str, str]] = deque(maxlen=maxlen)
        self._lock = threading.Lock()
        self.version = 0
        self.mirror = None

    def append(self, msg: str, *, from_engine: bool = False) -> None:
        with self._lock:
            self._items.append((time.time(), classify_log(msg), msg))
            self.version += 1
        if not from_engine and self.mirror is not None:
            try:
                self.mirror(msg)
            except Exception:
                pass

    def clear(self) -> None:
        with self._lock:
            self._items.clear()
            self.version += 1

    def snapshot(self) -> list[tuple[float, str, str]]:
        with self._lock:
            return list(self._items)

    def filtered(self, level: str = "全部", keyword: str = "", limit: int = 300):
        lines = self.snapshot()
        if level != "全部":
            key = {v: k for k, v in LEVEL_LABELS.items()}.get(level, "")
            lines = [line for line in lines if line[1] == key]
        kw = keyword.strip().lower()
        if kw:
            lines = [line for line in lines if kw in line[2].lower()]
        return list(reversed(lines[-limit:]))

LED_OPTIONS = (
    (0x00, "熄灭", "#9AA0A6"),
    (0x01, "黄色", "#F2C94C"),
    (0x02, "红色", "#EB5757"),
    (0x03, "紫色", "#9B51E0"),
    (0x04, "蓝色", "#2F80ED"),
    (0x05, "青色", "#27C4D3"),
    (0x06, "绿色", "#27AE60"),
)


def round_step10(value: int) -> int:
    value = int(value)
    return max(10, (value + 5) // 10 * 10)


def clamp_ovc_strength(value: int, *, allow_zero: bool = False) -> int:
    value = int(value)
    if allow_zero and value <= 0:
        return 0
    return min(200, round_step10(value))


def new_history() -> deque:
    return deque(maxlen=900)

def slot_status(slot) -> str:
    return "online"

def battery_text(slot) -> str:
    return f"{slot.battery}%" if slot.battery is not None else "—"

def output_row(slot, channel: str) -> dict:
    limit = slot.strength_limit.get(channel, 200) or 200
    value = slot.strength.get(channel, 0)
    return {
        "channel": channel,
        "value": value,
        "limit": limit,
        "percent": max(0, min(100, value / limit * 100)),
    }

def osc_value_rows(state: EngineState) -> list[dict]:
    rows: list[dict] = []
    for sid in sorted(state.slots):
        slot = state.slots[sid]
        family = family_of(slot.type)
        if family == "BMTR":
            pressure = slot.pressure if slot.pressure is not None else 0.0
            rows.append({
                "address": f"…{family}Pressure", "vtype": "float",
                "value": f"{pressure:.2f} kPa",
                "percent": max(0, min(100, pressure / PRESSURE_MAX_KPA * 100)),
            })
            edge = slot.edge_state if slot.edge_state is not None else 0
            rows.append({
                "address": f"…{family}EdgeState", "vtype": "int32",
                "value": EDGE_STATES.get(edge, str(edge)),
                "percent": int(edge / 4 * 100),
            })
        else:
            for ch in ("A", "B"):
                limit = slot.strength_limit.get(ch, 200) or 200
                value = slot.strength.get(ch, 0)
                rows.append({
                    "address": f"…{family}Strength{ch}", "vtype": "float",
                    "value": f"{value}/{limit}",
                    "percent": max(0, min(100, value / limit * 100)),
                })
        if slot.battery is not None:
            rows.append({
                "address": f"…Battery", "vtype": "int32",
                "value": f"{slot.battery}%", "percent": slot.battery,
            })
    return rows

def osc_probe_card(engine) -> dict:
    osc = engine.osc
    running = osc is not None and getattr(osc, "_running", False)
    label = "OSC 探测"
    if not running:
        return {"value": "已停止", "unit": "", "label": label, "symbol": "Sync",
                "accent": False, "detail": ["桥接未运行", "联动页可开启"]}
    last = getattr(osc, "last_rx", None)
    in_port = engine.config["osc"].get("in_port", 9001)
    if last is not None and time.monotonic() - last <= 30.0:
        age = max(0, time.monotonic() - last)
        count = getattr(osc, "rx_count", 0) or 0
        return {"value": "已连接", "unit": "", "label": label, "symbol": "Contact",
                "accent": True,
                "detail": [f"收到 {count} 包", f"{age:.0f} 秒前有数据"]}
    return {"value": "无数据", "unit": "", "label": label, "symbol": "Contact",
            "accent": False,
            "detail": [f"监听 :{in_port}", "未收到数据"]}


def stats(engine, log_buffer: LogBuffer) -> list[dict]:
    state = engine.get_state()
    devices = state.slots
    outputs = sum(1 for s in devices.values() if s.is_output_device)
    osc_on = engine.osc is not None and getattr(engine.osc, "_running", False)
    osc_cfg = engine.config["osc"]
    ports = f"{osc_cfg.get('out_port', 9000)} → {osc_cfg.get('in_port', 9001)}"
    return [
        {"value": str(len(devices)), "unit": "台", "label": "已连接设备",
         "trend": BACKEND_LABELS.get(engine.backend_kind, engine.backend_kind),
         "symbol": "CellPhone", "accent": True},
        {"value": str(outputs), "unit": f"/ {max(len(devices), outputs)}", "label": "输出设备",
         "trend": f"{len(devices) - outputs} 台传感器", "symbol": "Remote", "accent": False},
        {"value": "运行中" if osc_on else "已停止", "unit": "", "label": "OSC 桥接",
         "trend": ports, "symbol": "Sync", "accent": osc_on},
        osc_probe_card(engine),
    ]
