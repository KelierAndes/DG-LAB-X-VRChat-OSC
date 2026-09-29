from __future__ import annotations

import asyncio
import os
import tempfile
import unittest
import unittest.mock

from dglab.ble import BleClient, V3_NOTIFY
from dglab.state import EngineState, Slot, StateEvents, family_of
from dglab.official_waveforms_ovc import OvcWaveform
from dglab.waves import CONTINUOUS, SILENT
from ui import charts
from vrc.osc_bridge import OscBridge, OscConfig


class FakeBleakClient:
    instances: dict[str, "FakeBleakClient"] = {}

    def __init__(self, address: str, disconnected_callback=None, **kwargs):
        self.address = address
        self.disconnected_callback = disconnected_callback
        self.written: list[tuple[str, bytes]] = []
        self.notifies: dict[str, object] = {}
        FakeBleakClient.instances[address] = self

    def simulate_drop(self) -> None:
        if self.disconnected_callback:
            self.disconnected_callback(self)

    async def connect(self) -> None:
        pass

    async def disconnect(self) -> None:
        pass

    async def start_notify(self, char: str, cb) -> None:
        self.notifies[char] = cb

    async def read_gatt_char(self, char: str) -> bytearray:
        return bytearray([77])

    async def write_gatt_char(self, char: str, data, response: bool = False) -> None:
        self.written.append((char, bytes(data)))


def free_udp_port() -> int:
    import socket as _socket

    s = _socket.socket(_socket.AF_INET, _socket.SOCK_DGRAM)
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def _make_client() -> BleClient:
    events = StateEvents()
    return BleClient(
        events,
        soft_limit_a=200,
        soft_limit_b=200,
    )


class BleMultiDeviceTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        FakeBleakClient.instances.clear()
        self.patcher = unittest.mock.patch("dglab.ble.BleakClient", FakeBleakClient)
        self.patcher.start()
        self.ble = _make_client()

    async def asyncTearDown(self):
        self.patcher.stop()

    async def test_two_devices_connect_and_route(self):
        await self.ble.connect("addr-coyote", "coyote_v3")
        await self.ble.connect("addr-ovc", "ovc")
        self.assertEqual(len(self.ble.sessions), 2)
        self.assertEqual(set(self.ble.state.slots), {"addr-coyote", "addr-ovc"})
        self.assertEqual(self.ble.state.slots["addr-coyote"].type, "COYOTE_030")
        self.assertEqual(self.ble.state.slots["addr-ovc"].type, "OVC_1")

        await self.ble.set_strength("A", 30, slot_id="addr-ovc")
        await self.ble.set_strength("A", 11, slot_id="addr-coyote")
        self.assertEqual(self.ble.state.slots["addr-ovc"].strength["A"], 30)
        self.assertEqual(self.ble.state.slots["addr-coyote"].strength["A"], 11)

        await self.ble.set_wave("A", "BUBBLE", slot_id="addr-coyote")
        await self.ble.set_wave("A", "ALARM", slot_id="addr-ovc")
        self.assertEqual(len(self.ble.sessions["addr-coyote"]._cycles["A"].frames), 2)
        self.assertTrue(len(self.ble.sessions["addr-ovc"]._cycles["A"].frames) >= 1)

        await asyncio.sleep(0.25)
        for address, expected_head in (("addr-coyote", 0xB0), ("addr-ovc", 0xB0)):
            client = FakeBleakClient.instances[address]
            b0s = [data for _char, data in client.written if data[0] == 0xB0]
            self.assertTrue(b0s, address)
            frame = b0s[-1]
            self.assertEqual(len(frame), 20)
            if address == "addr-ovc":
                self.assertEqual(bytes(frame[1:8]), bytes(7))

        await self.ble.emergency_stop()
        self.assertEqual(self.ble.state.slots["addr-ovc"].strength["A"], 0)
        self.assertEqual(self.ble.state.slots["addr-ovc"].strength["B"], 0)

        await self.ble.disconnect("addr-coyote")
        self.assertEqual(set(self.ble.sessions), {"addr-ovc"})
        self.assertEqual(self.ble.state.status_text, "已连接 1 台设备")

    async def test_wave_continuity_and_strength_ack_flow(self):
        await self.ble.connect("addr-c", "coyote_v3")
        session = self.ble.sessions["addr-c"]
        self.assertTrue(session._cycles["A"].frames)
        self.assertTrue(session._cycles["B"].frames)

        from dglab.official_waveforms import COYOTE_WAVEFORMS, CoyoteWaveform
        await self.ble.set_wave("A", CoyoteWaveform.RHYTHM, slot_id="addr-c")
        client = FakeBleakClient.instances["addr-c"]

        rhythm = {f.lower() for f in COYOTE_WAVEFORMS[CoyoteWaveform.RHYTHM]["raw"]}
        steady = bytes.fromhex("2828282864646464")
        await asyncio.sleep(0.5)
        b0s = [d for _c, d in client.written if d[0] == 0xB0]
        self.assertTrue(len(b0s) >= 4)
        self.assertTrue(all(b[4:12].hex() in rhythm for b in b0s))
        await asyncio.sleep(1.2)
        b0s = [d for _c, d in client.written if d[0] == 0xB0]
        self.assertTrue(len(b0s) >= 12)
        self.assertTrue(all(b[4:12].hex() in rhythm for b in b0s))

        await self.ble.set_strength("A", 30, slot_id="addr-c")
        await asyncio.sleep(0.15)
        b0s = [d for _c, d in client.written if d[0] == 0xB0]
        applied = [b for b in b0s if (b[1] & 0x0F) == 0b1100]
        self.assertTrue(applied, "no absolute-set frame written")
        frame = applied[-1]
        self.assertEqual(frame[2], 30)
        seq = frame[1] >> 4
        self.assertEqual(seq > 0, True)

        echo = bytes([0xB1, seq, 30, 0])
        self.ble._on_notify(session, None, echo)
        self.assertEqual(session._awaiting_seq, 0)
        await self.ble.set_strength("A", 50, slot_id="addr-c")
        await asyncio.sleep(0.15)
        b0s = [d for _c, d in client.written if d[0] == 0xB0]
        applied = [b for b in b0s if (b[1] & 0x0F) == 0b1100]
        self.assertEqual(applied[-1][2], 50)

    async def test_writer_survives_transient_errors(self):
        await self.ble.connect("addr-c", "coyote_v3")
        client = FakeBleakClient.instances["addr-c"]

        original = client.write_gatt_char
        failures = {"count": 0}

        async def flaky(char, data, response=False):
            if failures["count"] < 3:
                failures["count"] += 1
                raise OSError("transient radio hiccup")
            await original(char, data, response=response)

        client.write_gatt_char = flaky
        await asyncio.sleep(1.0)
        self.assertTrue(len(client.written) >= 5, len(client.written))

    async def test_battery_and_pressure_notify(self):
        await self.ble.connect("addr-bmtr", "bmtr")
        self.assertIsNotNone(self.ble.sessions["addr-bmtr"].writer_task)

        session = self.ble.sessions["addr-bmtr"]
        client = FakeBleakClient.instances["addr-bmtr"]
        battery_cb = client.notifies.get("00001500-0000-1000-8000-00805f9b34fb")
        if battery_cb:
            battery_cb(None, bytearray([55]))
            self.assertEqual(self.ble.state.slots["addr-bmtr"].battery, 55)

        notify_cb = client.notifies[V3_NOTIFY]
        data = bytearray(15)
        data[0] = 0xD0
        data[8:10] = (1704).to_bytes(2, "little")
        notify_cb(None, data)
        self.assertAlmostEqual(self.ble.state.slots["addr-bmtr"].pressure, 17.04)


class OscFamilyInputTests(unittest.IsolatedAsyncioTestCase):
    async def test_inputs_target_first_device_per_family(self):
        state = EngineState(backend="v4", paired=True, connected=True)
        state.slots["ovc-1"] = Slot(slot_id="ovc-1", type="OVC_1")
        state.slots["coyote-1"] = Slot(slot_id="coyote-1", type="COYOTE_030")
        state.slots["coyote-2"] = Slot(slot_id="coyote-2", type="COYOTE_030")

        calls: list[tuple[str, int, str | None]] = []

        class Commands:
            async def set_strength(self, ch, v, slot_id=None):
                calls.append(("set", ch, slot_id))

            async def set_wave(self, ch, name, slot_id=None):
                calls.append(("wave", ch, slot_id))

            async def zap(self, ch, sec, slot_id=None):
                calls.append(("zap", ch, slot_id))

            async def emergency_stop(self):
                calls.append(("stop",))

        port = free_udp_port()
        bridge = OscBridge(OscConfig({"in_port": port}), lambda: state, Commands())
        await bridge.start()
        try:
            from pythonosc.udp_client import SimpleUDPClient

            sender = SimpleUDPClient("127.0.0.1", port)
            sender.send_message("/avatar/parameters/DGLabStrengthA", [42])
            sender.send_message("/avatar/parameters/DGLabOvcInStrengthA", [77])
            sender.send_message("/avatar/parameters/DGLabOvcInStrengthB", [3])
            await asyncio.sleep(0.4)
        finally:
            await bridge.stop()

        assert ("set", "A", "coyote-1") in calls, calls
        assert ("set", "A", "ovc-1") in calls, calls
        assert ("set", "B", "ovc-1") in calls, calls
        assert not any(c[2] == "coyote-2" for c in calls), calls

    async def test_wave_direct_and_step_inputs(self):
        from vrc.osc_bridge import wave_order

        order = wave_order("COYOTE")
        assert order[0] == SILENT and order[1] == CONTINUOUS
        assert len(order) == 26
        assert wave_order("OVC")[2:] == [w.value for w in OvcWaveform]

        state = EngineState(backend="v4", paired=True, connected=True)
        state.slots["coyote-1"] = Slot(slot_id="coyote-1", type="COYOTE_030")

        waves: list[tuple[str, str]] = []

        class Commands:
            _selected_wave = {"A": SILENT, "B": SILENT}

            async def set_strength(self, ch, v, slot_id=None):
                pass

            async def set_wave(self, ch, name, slot_id=None):
                waves.append((ch, name))
                self._selected_wave[ch] = name

            async def fire_start(self, slot_id=None):
                pass

            async def fire_stop(self, slot_id=None):
                pass

            async def emergency_stop(self):
                pass

        port = free_udp_port()
        bridge = OscBridge(OscConfig({"in_port": port}), lambda: state, Commands())
        await bridge.start()
        try:
            from pythonosc.udp_client import SimpleUDPClient

            sender = SimpleUDPClient("127.0.0.1", port)
            sender.send_message("/avatar/parameters/DGLabWaveA", [1])
            await asyncio.sleep(0.3)
            sender.send_message("/avatar/parameters/DGLabWaveStepA", [1])
            await asyncio.sleep(0.3)
            sender.send_message("/avatar/parameters/DGLabWaveStepA", [-1])
            await asyncio.sleep(0.3)
            sender.send_message("/avatar/parameters/DGLabWaveStepA", [0])
            await asyncio.sleep(0.3)
        finally:
            await bridge.stop()

        assert waves[0] == ("A", CONTINUOUS), waves
        assert waves[1] == ("A", order[2]), waves
        assert waves[2] == ("A", CONTINUOUS), waves
        assert len(waves) == 3, waves

    async def test_fire_parameter_is_trigger(self):
        state = EngineState(backend="v4", paired=True, connected=True)
        state.slots["coyote-1"] = Slot(slot_id="coyote-1", type="COYOTE_030")

        events: list[str] = []

        class Commands:
            _selected_wave = {"A": SILENT, "B": SILENT}

            async def set_strength(self, ch, v, slot_id=None):
                pass

            async def set_wave(self, ch, name, slot_id=None):
                pass

            async def fire_start(self, slot_id=None):
                events.append(f"start:{slot_id}")

            async def fire_stop(self, slot_id=None):
                events.append(f"stop:{slot_id}")

            async def emergency_stop(self):
                pass

        port = free_udp_port()
        bridge = OscBridge(OscConfig({"in_port": port}), lambda: state, Commands())
        await bridge.start()
        try:
            from pythonosc.udp_client import SimpleUDPClient

            sender = SimpleUDPClient("127.0.0.1", port)
            sender.send_message("/avatar/parameters/DGLabFire", [True])
            await asyncio.sleep(0.3)
            sender.send_message("/avatar/parameters/DGLabFire", [False])
            await asyncio.sleep(0.3)
        finally:
            await bridge.stop()

        assert events == ["start:coyote-1", "stop:coyote-1"], events


class ChartTests(unittest.TestCase):
    def test_render_wave_live(self):
        import time as _time

        now = _time.monotonic()
        samples = [(now - i * 0.1, (10, 20, 30, 40), (50, 60, 70, 80))
                   for i in range(50)]
        for dark in (False, True):
            png = charts.render_wave_live(samples, dark=dark)
            self.assertEqual(png[:8], bytes([0x89, 0x50, 0x4E, 0x47, 0x0D, 0x0A, 0x1A, 0x0A]))
            self.assertTrue(len(png) > 1000)

    def test_render_pressure_chart_fixed_range(self):
        import time as _time

        now = _time.monotonic()
        series = [
            ("bmtr-1", [(now - i * 0.1, 7.0 + i * 0.01) for i in range(200)]),
            ("bmtr-2", [(now - i * 0.1, 12.0 - i * 0.02) for i in range(150)]),
        ]
        for dark in (False, True):
            png = charts.render_pressure_chart(series, dark=dark)
            self.assertEqual(png[:8], bytes([0x89, 0x50, 0x4E, 0x47, 0x0D, 0x0A, 0x1A, 0x0A]))
            self.assertTrue(len(png) > 1000)

    def test_family_of_helpers(self):
        self.assertEqual(family_of("OVC_1"), "OVC")
        self.assertEqual(family_of("BMTR_1"), "BMTR")
        self.assertEqual(family_of("COYOTE_030"), "COYOTE")


if __name__ == "__main__":
    unittest.main()
