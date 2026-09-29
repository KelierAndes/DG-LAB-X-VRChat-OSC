"""Unified device state model shared by all backends and the OSC bridge."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable


def family_of(device_type: str) -> str:
    """Device family used for UI grouping and OSC prefixes."""
    t = (device_type or "").upper()
    if t.startswith("OVC"):
        return "OVC"
    if t.startswith("BMTR"):
        return "BMTR"
    return "COYOTE"


@dataclass
class Slot:
    """One controlled device (Socket V4 slots; single synthetic slot for V3/BLE)."""

    slot_id: str = ""
    name: str = ""
    type: str = ""
    # Live values.
    strength: dict[str, int] = field(default_factory=lambda: {"A": 0, "B": 0})
    strength_limit: dict[str, int] = field(default_factory=lambda: {"A": 200, "B": 200})
    battery: int | None = None
    channel_status: dict[str, int] = field(default_factory=lambda: {"A": 0, "B": 0})
    # BMTR_1 (灵猫) pressure sensor, kPa.
    pressure: float | None = None
    # BMTR_1 edge-control state (slotState.edge.edgeState 0-4).
    edge_state: int | None = None
    # Raw props/slotState for advanced UI display.
    props: dict = field(default_factory=dict)
    slot_state: dict = field(default_factory=dict)

    @property
    def is_output_device(self) -> bool:
        """False for pure sensors (BMTR 灵猫)."""
        return not self.type.upper().startswith("BMTR")

    def summary(self) -> str:
        bat = f"{self.battery}%" if self.battery is not None else "--"
        if not self.is_output_device:
            pressure = f"{self.pressure:.2f}" if self.pressure is not None else "--"
            return f"{self.name or self.type or self.slot_id}  气压 {pressure} kPa  电量 {bat}"
        return (
            f"{self.name or self.type or self.slot_id}  "
            f"A={self.strength['A']}/{self.strength_limit['A']} "
            f"B={self.strength['B']}/{self.strength_limit['B']}  电量 {bat}"
        )


@dataclass
class EngineState:
    """Snapshot of everything the UI / OSC bridge need."""

    backend: str = "none"  # none | v4 | v3 | ble
    # Connection level: ws/bt transport online.
    connected: bool = False
    # Pairing level: app attached (V4/V3) or BLE device connected.
    paired: bool = False
    status_text: str = "未连接"
    client_id: str = ""
    target_id: str = ""
    qr_text: str = ""
    slots: dict[str, Slot] = field(default_factory=dict)
    active_slot: str = ""
    last_action: int | None = None  # App feedback button 0-9

    def active(self) -> Slot | None:
        return self.slots.get(self.active_slot)

    def copy(self) -> "EngineState":
        slots = {k: Slot(**{**vars(v)}) for k, v in self.slots.items()}
        return EngineState(
            backend=self.backend,
            connected=self.connected,
            paired=self.paired,
            status_text=self.status_text,
            client_id=self.client_id,
            target_id=self.target_id,
            qr_text=self.qr_text,
            slots=slots,
            active_slot=self.active_slot,
            last_action=self.last_action,
        )


class StateEvents:
    """Minimal synchronous event hub.

    Callbacks fire on the backend's asyncio thread; UI subscribers must
    marshal to the UI thread themselves (the UI does this via its queue).
    """

    def __init__(self) -> None:
        self._subs: dict[str, list[Callable]] = {}

    def on(self, event: str, cb: Callable) -> None:
        self._subs.setdefault(event, []).append(cb)

    def emit(self, event: str, *args) -> None:
        for cb in self._subs.get(event, ()):
            try:
                cb(*args)
            except Exception:
                pass

    def remove_all(self) -> None:
        self._subs.clear()
