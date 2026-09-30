"""Live BMTR (灵猫) GATT probe - run ONLY with the device powered on nearby.

    python _research/bmtr_probe.py [address] [--seconds N] [--wwr] [--ff01] [--raw HEX]

Steps:
1. Scan ~8 s for a 47L124000 (灵猫) advertiser if no address is given.
2. Connect, dump every service / characteristic with its properties.
3. Subscribe to EVERY notifiable characteristic and print all notifications
   (time, uuid, length, hex) - nothing is filtered.
4. Send the documented 0x50 pressure-enable frame (17 B) via 150A.
   Default uses write-WITH-response; --wwr uses write-without-response.
5. Watch for D0 notifications for --seconds (default 15) and print a
   per-second summary.

Flags:
  --ff01    ALSO send the enable via the vendor FF01 pipe.  KNOWN DANGEROUS:
            earlier testing showed FF01 writes reboot the device.
  --raw HEX send an extra raw frame via 150A after the enable (e.g. testing
            a suspected frame variant).
"""
from __future__ import annotations

import argparse
import asyncio
import sys
import time

from bleak import BleakClient, BleakScanner

V3_SERVICE = "0000180c-0000-1000-8000-00805f9b34fb"
V3_WRITE = "0000150a-0000-1000-8000-00805f9b34fb"
V3_NOTIFY = "0000150b-0000-1000-8000-00805f9b34fb"
V3_BATTERY = "00001500-0000-1000-8000-00805f9b34fb"

BMTR_NAME_PREFIX = "47L124000"


def enable_frame(color: int = 0x01) -> bytes:
    """Documented 0x50 frame: HEAD + color + D0 + 14 zero bytes (17 B)."""
    return bytes([0x50, color, 0xD0]) + bytes(14)


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("address", nargs="?", default=None)
    ap.add_argument("--seconds", type=int, default=15)
    ap.add_argument("--wwr", action="store_true", help="enable via write-without-response")
    ap.add_argument("--ff01", action="store_true", help="DANGEROUS: also write FF01")
    ap.add_argument("--raw", type=str, default=None, help="extra raw hex frame via 150A")
    args = ap.parse_args()

    address = args.address
    if not address:
        print(f"扫描 {BMTR_NAME_PREFIX} … (8 s)")
        devs = await BleakScanner.discover(timeout=8.0)
        for d in devs:
            if (d.name or "").startswith(BMTR_NAME_PREFIX):
                address = d.address
                print(f"发现灵猫: {d.name} @ {d.address}")
                break
    if not address:
        print("未发现灵猫 (47L124000)。请确认设备已开机、未被手机 App 占用。")
        return 1

    received: list[tuple[float, str, bytes]] = []

    def make_cb(uuid: str):
        def cb(sender, data: bytearray):
            received.append((time.monotonic(), uuid, bytes(data)))
            hexs = bytes(data).hex().upper()
            extra = ""
            if data and data[0] == 0xD0 and len(data) >= 10:
                kpa = int.from_bytes(data[8:10], "little", signed=True) / 100.0
                extra = f"  -> 气压 {kpa:.2f} kPa"
            tag = uuid.split("-")[0].upper()[-8:]
            print(f"  通知 [{tag}] {len(data):2d}B {hexs}{extra}")
        return cb

    async with BleakClient(address) as client:
        print(f"已连接 {address}")
        print("--- GATT 结构 ---")
        notifiable = []
        for service in client.services:
            print(f"service {service.uuid}  [{service.description}]")
            for ch in service.characteristics:
                props = ",".join(ch.properties)
                print(f"  char {ch.uuid}  [{props}]  handle={ch.handle}")
                if "notify" in ch.properties:
                    notifiable.append(ch)
        print(f"--- 订阅全部 {len(notifiable)} 个通知特征 ---")
        for ch in notifiable:
            try:
                await client.start_notify(ch, make_cb(ch.uuid))
                print(f"  已订阅 {ch.uuid[-4:].upper()}")
            except Exception as exc:
                print(f"  订阅失败 {ch.uuid}: {exc!r}")

        try:
            battery = await client.read_gatt_char(V3_BATTERY)
            print(f"电量: {battery[0]}%")
        except Exception as exc:
            print(f"电量读取失败: {exc!r}")

        frame = enable_frame()
        mode = "without response" if args.wwr else "with response"
        print(f"--- 发送 0x50 使能 ({len(frame)}B, {mode}): {frame.hex().upper()} ---")
        try:
            await client.write_gatt_char(V3_WRITE, frame, response=not args.wwr)
            print("150A 写入成功")
        except Exception as exc:
            print(f"150A 写入失败: {exc!r}")

        if args.ff01:
            ff01 = "0000ff01-0000-1000-8000-00805f9b34fb"
            print(f"--- 警告: 按要求经 FF01 发送使能 (可能重启设备) ---")
            try:
                await client.write_gatt_char(ff01, frame, response=False)
                print("FF01 写入成功")
            except Exception as exc:
                print(f"FF01 写入失败: {exc!r}")

        if args.raw:
            raw = bytes.fromhex(args.raw)
            print(f"--- 发送原始帧: {raw.hex().upper()} ---")
            try:
                await client.write_gatt_char(V3_WRITE, raw, response=not args.wwr)
                print("150A 原始帧写入成功")
            except Exception as exc:
                print(f"150A 原始帧写入失败: {exc!r}")

        print(f"--- 监听 {args.seconds}s, 每 5s 输出小结 ---")
        start = time.monotonic()
        while time.monotonic() - start < args.seconds:
            await asyncio.sleep(5)
            recent = [r for r in received if r[0] > time.monotonic() - 5]
            print(f"[小结] 最近5s 收到 {len(recent)} 帧"
                  + ("" if recent else "  <- 无任何通知 (使能未生效?)"))

        total = len(received)
        print(f"--- 完成: 共 {total} 帧通知 ---")
        if total == 0:
            print("结论: 使能后仍无任何通知。可能原因: 使能帧需经其它特征/格式,")
            print("      或设备需处于特定模式 (屏幕点亮/边控模式)。可尝试 --ff01 / --raw。")
        return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
