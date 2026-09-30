"""Live Coyote (郊狼 3.0) LED probe - 官方 App 可切灯色但公开协议未写明, 本探针实测.

    python _research/coyote_led_probe.py [address] [--auto] [--seconds N] [--scan-only]

无 --auto: 交互命令模式 (设备在手边逐条试):
    led <name|0-7>     0x50 灯色 3B 短帧 (150A, 无响应写)
    led17 <name|0-7>   0x50 灯色 17B 长帧 (BMTR 风格: HEAD+color+00+14x00)
    sweep [步长秒]     循环 0x00-0x06 色彩, 观察哪一步灯亮
    raw <hex> [wwr] [uuid尾4位]  任意帧写到可写特征 (默认 150A; uuid 尾4位如 150A)
    dump               重新打印 GATT
    q                  退出

--auto: 自动候选帧矩阵, 每步打印醒目标题并停 2.5s —— 运行者盯住设备灯,
把"哪一步灯亮了/变了"记下来即可。--scan-only 只扫描不出报告地址。
颜色: 0=off 1=yellow 2=magenta 3=purple 4=blue 5=cyan 6=green (与负鼠/灵猫一致)。
注意: 郊狼需已开机且未被官方 App 占用; FF01 写入在其他设备上有重启前科, 默认绝不触碰。
"""
from __future__ import annotations

import argparse
import asyncio
import sys
import threading
import time

from bleak import BleakClient, BleakScanner

V3_SERVICE = "0000180c-0000-1000-8000-00805f9b34fb"
V3_WRITE = "0000150a-0000-1000-8000-00805f9b34fb"
V3_NOTIFY = "0000150b-0000-1000-8000-00805f9b34fb"
V3_BATTERY = "00001500-0000-1000-8000-00805f9b34fb"

COYOTE_PREFIXES = ("47L121000", "47L12", "Coyote", "D-LAB")


def color_byte(token: str) -> int | None:
    names = {"off": 0, "yellow": 1, "magenta": 2, "purple": 3,
             "blue": 4, "cyan": 5, "green": 6}
    if token.isdigit():
        v = int(token)
        return v if 0 <= v <= 7 else None
    return names.get(token.lower())


def frame_short(color: int) -> bytes:
    return bytes([0x50, color & 0xFF, 0x01])


def frame_long(color: int) -> bytes:
    return bytes([0x50, color & 0xFF, 0x00]) + bytes(14)


class Probe:
    def __init__(self, client: BleakClient):
        self.client = client
        self.count = 0

    async def dump(self) -> None:
        print("--- GATT 结构 ---")
        for service in self.client.services:
            print(f"service {service.uuid}  [{service.description}]")
            for ch in service.characteristics:
                props = ",".join(ch.properties)
                print(f"  char {ch.uuid}  [{props}]  handle={ch.handle}")

    async def subscribe_all(self) -> None:
        for service in self.client.services:
            for ch in service.characteristics:
                if "notify" in ch.properties:
                    try:
                        await self.client.start_notify(ch, self._cb(ch.uuid))
                        print(f"已订阅 {ch.uuid[-4:].upper()}")
                    except Exception as exc:
                        print(f"订阅失败 {ch.uuid}: {exc!r}")

    def _cb(self, uuid: str):
        def cb(sender, data: bytearray):
            self.count += 1
            tag = uuid.split("-")[0].upper()[-8:]
            print(f"  通知 [{tag}] {len(data):2d}B {bytes(data).hex().upper()}")
        return cb

    async def write(self, hexs: str, uuid: str = V3_WRITE, wwr: bool = False) -> bool:
        data = bytes.fromhex(hexs.replace(" ", ""))
        mode = "带响应" if not wwr else "无响应"
        target = uuid[-4:].upper()
        try:
            await self.client.write_gatt_char(uuid, data, response=not wwr)
            print(f"  写入 OK [{target}] {mode} {len(data)}B: {data.hex().upper()}")
            return True
        except Exception as exc:
            print(f"  写入失败 [{target}] {mode} {data.hex().upper()}: {exc!r}")
            return False


async def auto_matrix(probe: Probe, seconds: int) -> None:
    steps: list[tuple[str, str, bool]] = [
        ("50 01 01", V3_WRITE, False),
        ("50 04 01", V3_WRITE, False),
        ("50 02 01", V3_WRITE, False),
        ("50 05 01", V3_WRITE, False),
        ("50 01 01", V3_WRITE, True),
        ("50 06 01", V3_WRITE, True),
        (frame_long(0x01).hex(), V3_WRITE, False),
        (frame_long(0x04).hex(), V3_WRITE, False),
    ]
    names = ["yellow 3B", "blue 3B", "magenta 3B", "cyan 3B",
             "yellow 3B 带响应", "green 3B 带响应", "yellow 17B 长帧", "blue 17B 长帧"]
    print(f"--- 自动矩阵: {len(steps)} 步, 每步 2.5s, 盯住设备灯 ---")
    for i, ((hexs, uuid, wwr), name) in enumerate(zip(steps, names), 1):
        print(f">>> [步骤 {i}/{len(steps)}] {name}: {hexs.replace(' ', '')} <<<")
        await probe.write(hexs, uuid, wwr)
        await asyncio.sleep(2.5)
    print("--- 灯色循环 (每色 1.2s) ---")
    for c in range(0x01, 0x07):
        print(f">>> [循环] 颜色 {c} <<<")
        await probe.write(frame_short(c).hex(), V3_WRITE, False)
        await asyncio.sleep(1.2)
    print(f">>> [结束] 熄灯 (颜色 0) <<<")
    await probe.write(frame_short(0).hex(), V3_WRITE, False)
    print(f"--- 监听 {seconds}s 收尾 ---")
    start = time.monotonic()
    last = probe.count
    while time.monotonic() - start < seconds:
        await asyncio.sleep(5)
        print(f"[小结] 累计通知 {probe.count} 帧 (近5s +{probe.count - last})")
        last = probe.count


async def interactive(probe: Probe, client: BleakClient) -> None:
    loop = asyncio.get_running_loop()
    queue: asyncio.Queue[str] = asyncio.Queue()

    def reader() -> None:
        while True:
            line = input()
            loop.call_soon_threadsafe(queue.put_nowait, line.strip())

    threading.Thread(target=reader, daemon=True).start()
    print("--- 交互模式: led N | led17 N | sweep [秒] | raw HEX [wwr] [尾4位] | dump | q ---")
    while True:
        line = await queue.get()
        if not line:
            continue
        parts = line.split()
        cmd = parts[0].lower()
        try:
            if cmd == "q":
                return
            elif cmd == "dump":
                await probe.dump()
            elif cmd in ("led", "led17") and len(parts) >= 2:
                c = color_byte(parts[1])
                if c is None:
                    print("  颜色无效 (0-7 或 off/yellow/.../green)")
                    continue
                frame = frame_short(c) if cmd == "led" else frame_long(c)
                await probe.write(frame.hex(), V3_WRITE, False)
            elif cmd == "sweep":
                gap = float(parts[1]) if len(parts) >= 2 else 1.2
                for c in range(0x00, 0x07):
                    print(f"  [sweep] 颜色 {c}")
                    await probe.write(frame_short(c).hex(), V3_WRITE, False)
                    await asyncio.sleep(gap)
            elif cmd == "raw" and len(parts) >= 2:
                wwr = "wwr" in parts[2:]
                uuid = V3_WRITE
                for token in parts[2:]:
                    if len(token) == 4 and token.upper() != "WWR":
                        matches = [ch for svc in client.services
                                   for ch in svc.characteristics
                                   if ch.uuid[-4:].upper() == token.upper()]
                        if matches:
                            uuid = matches[0].uuid
                        else:
                            print(f"  未找到尾4位 {token} 的特征, 用 150A")
                await probe.write(parts[1], uuid, wwr)
            else:
                print("  未知命令")
        except Exception as exc:
            print(f"  命令执行失败: {exc!r}")


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("address", nargs="?", default=None)
    ap.add_argument("--auto", action="store_true")
    ap.add_argument("--seconds", type=int, default=10)
    ap.add_argument("--scan-only", action="store_true")
    args = ap.parse_args()

    address = args.address
    if not address:
        print("扫描郊狼 … (8 s)")
        devs = await BleakScanner.discover(timeout=8.0)
        for d in devs:
            if any((d.name or "").startswith(p) for p in COYOTE_PREFIXES):
                address = d.address
                print(f"发现郊狼: {d.name} @ {d.address}")
                break
        if args.scan_only:
            for d in devs:
                print(f"  可见: {d.name!r} @ {d.address}")
            return 0 if address else 1
    if not address:
        print("未发现郊狼 (47L121000…)。确认设备已开机、未被官方 App 占用。")
        return 1

    async with BleakClient(address) as client:
        print(f"已连接 {address}")
        probe = Probe(client)
        await probe.dump()
        await probe.subscribe_all()
        try:
            battery = await client.read_gatt_char(V3_BATTERY)
            print(f"电量: {battery[0]}%")
        except Exception as exc:
            print(f"电量读取失败: {exc!r}")
        if args.auto:
            await auto_matrix(probe, args.seconds)
        else:
            await interactive(probe, client)
    print(f"--- 完成, 共 {probe.count} 帧通知 ---")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
