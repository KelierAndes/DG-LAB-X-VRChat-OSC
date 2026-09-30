"""郊狼 3.0 拨轮按键探针 (第 3 轮: 复刻官方 App 上下文) - 实测协议是否上报拨轮/按键.

⚠️ 长按拨轮 = 关机键, 探针绝不安排长按场景。

第 3 轮实测结论 (2026-09-30, probe_v3_dial_20260930_144244.log, 自测通过后):
  - 拨轮"旋转": 每格都实时上报 B1(seq=0, 新强度), 与官方文档 No.3 一致 —— 间接可见;
  - 拨轮"按下"(短按 9+ 次/双击): 全程零帧 —— 按键事件不透传。按下会切换拨轮的
    内部目标通道 (A↔B), 但该切换本身无任何 BLE 帧, 只能靠后续旋转改的是哪个
    通道反推;
  - 0x50 第 3 字节 bit0~bit3 (负鼠按键/灵猫气压同源的"使能位") 对郊狼无效果;
  - 其余全部通知特征 (1500/2A59/2003·0008) 被动监听均无数据。
  ⇒ 结论: 郊狼 3.0 无法实现 OVC 式"设备按键 → OSC 动作"映射, 能消费的只有
    B1(seq=0) 强度变化 (app 的 B1 处理已覆盖)。

前两轮结论 (_research/probe_v3_dial_*.log):
  - 被动监听时, 短按/双击/旋转全部零帧 (连文档 No.3 承诺的旋转→B1 也没有);
  - 0x50 第 3 字节 bit0~bit3 (负鼠按键/灵猫气压同源的"使能位") 只是得到应答,
    没有解锁任何上报;
  - 捕获到 3 种未记录帧, 均为命令应答/握手而非按键事件:
      53 00 + MAC尾4字节   连接握手 (订阅 150B 后立刻出现)
      E0 50 02             3B 短帧 (50 xx xx) 的写入应答
      51 cc 10 64          17B 长帧 (50 cc 00+14x00) 的写入应答
  - 疑点: 官方文档 No.3 的旋转→B1(seq=0) 场景里, App 始终以 100ms 持续发 B0;
    前两轮全程未发 B0, 设备可能只在"B0 会话"中才上报本地强度变化。

本轮方案 (默认流程):
  1. 连接, 订阅全部通知特征, 读设备信息 (0x1501/0x1502/0x2A25)。
  2. 发 BF 软上限 (200/200, 平衡 0, 与 app config.json 默认一致)。
  3. B1 自测: 发一条 B0 seq=1 A通道强度+1, 等待 B1 seq=1 —— 验证通知通路。
  4. 启动 100ms B0 静默流 (freq{10x4}+strength{0x4}, 与 app SILENT 帧一致)。
  5. 流保持期间逐段交互:
       q0 基线不动 / q1 短按1次 / q2 短按5次 / q3 双击3组 / q4 旋转+3 / q5 旋转-3
  6. 结束后把 A 通道强度清零 (B0 seq=3, 方式 0b1100)。

    python _research/coyote_v3_dial_probe.py [address] [--passive] [--skip-selftest]

判定:
  - 自测 B1(seq=1) 出现 → 通知通路正常, 之后交互段的零帧才是有效阴性;
  - q4/q5 出现 B1(seq=0) → 旋转仅以强度变化间接可见 (文档口径);
  - q1~q3 出现任何非 B1 帧 → 存在未记录按键帧 (目标发现);
  - q1~q3 与基线一致 → 按键事件在 BLE 层不透传。
"""
from __future__ import annotations

import argparse
import asyncio
import sys
import time
from datetime import datetime
from pathlib import Path

from bleak import BleakClient, BleakScanner

V3_WRITE = "0000150a-0000-1000-8000-00805f9b34fb"
COYOTE_PREFIXES = ("47L121000", "47L12")
LOG_DIR = Path(__file__).resolve().parent

# B0 静默帧: HEAD+序列号/方式+A/B强度(4B) + A波(频率4B+强度4B) + B波(频率4B+强度4B) = 20B
SILENT_B0 = bytes.fromhex("B0" + "000000" + "0A0A0A0A" + "00000000" + "0A0A0A0A" + "00000000")
assert len(SILENT_B0) == 20, len(SILENT_B0)

INFO_CHARS = {
    "00001501-0000-1000-8000-00805f9b34fb": "硬件版本",
    "00001502-0000-1000-8000-00805f9b34fb": "软件版本",
    "00002a25-0000-1000-8000-00805f9b34fb": "序列号",
}

Q_PHASES: list[tuple[str, str, int]] = [
    ("q0", "基线: 不要碰设备, 手离远一点 (B0 流保持中)", 6),
    ("q1", "短按拨轮 1 次: 按下去马上松开 (只按一次)", 10),
    ("q2", "短按拨轮 5 次: 一次一秒, 慢慢按", 10),
    ("q3", "快速双击拨轮 3 组: 每组按两下, 组间隔约 1 秒", 10),
    ("q4", "拨轮向上拨 3 格: 每格隔半秒 (强度应当 +3)", 12),
    ("q5", "拨轮向下拨 3 格: 每格隔半秒 (强度应当 -3)", 12),
]


class Recorder:
    """时间戳 + 阶段归档的通知记录, 控制台与日志文件同步输出."""

    def __init__(self, log_path: Path):
        self.log_path = log_path
        self.phase = "scan"
        self.frames: list[tuple[str, str, str, bytes]] = []  # (phase, t, uuid_tag, data)
        self._fh = log_path.open("w", encoding="utf-8")

    def line(self, text: str) -> None:
        print(text)
        self._fh.write(text + "\n")
        self._fh.flush()

    def set_phase(self, name: str) -> None:
        self.phase = name

    def on_notify(self, sender, data: bytearray) -> None:
        tag = str(getattr(sender, "uuid", "?")).split("-")[0].upper()[-4:]
        blob = bytes(data)
        ts = time.strftime("%H:%M:%S") + f".{int(time.perf_counter()*1000)%1000:03d}"
        self.frames.append((self.phase, ts, tag, blob))
        if blob and blob[0] == 0xB1 and len(blob) >= 4:
            self.line(f"  [{ts}] B1 seq={blob[1]} 强度 A={blob[2]} B={blob[3]}")
        else:
            self.line(f"  [{ts}] ★未记录帧★ [{tag}] {len(blob)}B {blob.hex().upper()}")

    def summary(self, phase: str, started: float) -> None:
        got = [f for f in self.frames if f[0] == phase]
        b1 = [f for f in got if f[3][:1] == b"\xb1"]
        other = [f for f in got if f[3][:1] != b"\xb1"]
        wall = time.monotonic() - started
        self.line(f"  << {phase} 结束: 用时 {wall:.1f}s, 共 {len(got)} 帧 "
                  f"(B1 {len(b1)} 帧 / 其他 {len(other)} 帧) >>")
        for f in other:
            self.line(f"     ↳ {phase} 其他帧 {f[3].hex().upper()}")

    def close(self) -> None:
        self._fh.close()


async def write_frame(client: BleakClient, data: bytes, note: str) -> bool:
    try:
        await client.write_gatt_char(V3_WRITE, data, response=False)
        print(f"  写入 {note}: {data.hex().upper()}")
        return True
    except Exception:
        try:
            await client.write_gatt_char(V3_WRITE, data, response=True)
            print(f"  写入 {note} (带响应): {data.hex().upper()}")
            return True
        except Exception as exc:
            print(f"  写入失败 {note}: {exc!r}")
            return False


async def run_phase(rec: Recorder, name: str, instruction: str, seconds: int) -> None:
    rec.set_phase(name)
    rec.line("")
    rec.line(f"====== 阶段 {name}: {instruction} [{seconds}s] ======")
    start = time.monotonic()
    while time.monotonic() - start < seconds:
        remain = int(seconds - (time.monotonic() - start))
        print(f"\r  …… {remain:2d}s 剩余 ", end="", flush=True)
        await asyncio.sleep(1)
    print("\r" + " " * 30 + "\r", end="", flush=True)
    rec.summary(name, start)


async def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    ap = argparse.ArgumentParser()
    ap.add_argument("address", nargs="?", default=None)
    ap.add_argument("--passive", action="store_true",
                    help="第 2 轮的被动模式: 不发 BF/B0, 只监听")
    ap.add_argument("--skip-selftest", action="store_true",
                    help="跳过 B0→B1 通路自测")
    args = ap.parse_args()

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    rec = Recorder(LOG_DIR / f"probe_v3_dial_{stamp}.log")
    rec.line(f"郊狼 3.0 拨轮探针 (第 3 轮: B0 会话上下文) @ {stamp}")

    address = args.address
    if not address:
        rec.line("扫描郊狼 3.0 (47L121000) … 8s")
        devs = await BleakScanner.discover(timeout=8.0)
        for d in devs:
            rec.line(f"  可见: {d.name!r} @ {d.address}")
            if any((d.name or "").startswith(p) for p in COYOTE_PREFIXES):
                address = d.address
                break
    if not address:
        rec.line("未发现郊狼 3.0。请确认设备已开机、未被官方 App 占用后重试。")
        rec.close()
        return 1
    rec.line(f"目标: {address}")

    async with BleakClient(address) as client:
        rec.line(f"已连接 {address}")
        rec.line("--- GATT 结构 ---")
        notifiable = []
        for service in client.services:
            rec.line(f"service {service.uuid}  [{service.description}]")
            for ch in service.characteristics:
                rec.line(f"  char {ch.uuid}  [{','.join(ch.properties)}] handle={ch.handle}")
                if "notify" in ch.properties:
                    notifiable.append(ch)
        rec.line(f"--- 订阅全部 {len(notifiable)} 个通知特征 ---")
        for ch in notifiable:
            try:
                await client.start_notify(ch, rec.on_notify)
                rec.line(f"  已订阅 {ch.uuid[-4:].upper()}")
            except Exception as exc:
                rec.line(f"  订阅失败 {ch.uuid}: {exc!r}")

        rec.line("--- 设备信息 ---")
        for uuid, label in INFO_CHARS.items():
            try:
                raw = await client.read_gatt_char(uuid)
                rec.line(f"  {label}: {raw!r} ({raw.hex().upper()})")
            except Exception as exc:
                rec.line(f"  {label} 读取失败: {exc!r}")

        if args.passive:
            rec.line("!!! 被动模式: 不发 BF/B0 (第 2 轮口径) !!!")
            await run_phase(rec, "connect", "连接静止期 (不要碰设备)", 6)
            for name, instruction, seconds in Q_PHASES:
                await run_phase(rec, name, instruction + " (注意: 无B0流)", seconds)
        else:
            await run_phase(rec, "connect", "连接静止期 (不要碰设备, 建立基线)", 6)

            rec.line("--- 发送 BF 软上限 (200/200, 平衡 0) ---")
            await write_frame(client, bytes([0xBF, 200, 200, 0, 0, 0, 0]), "BF")

            if not args.skip_selftest:
                rec.line("--- B1 通路自测: B0 seq=1, A 通道强度 +1 ---")
                rec.set_phase("selftest")
                await write_frame(
                    client,
                    bytes([0xB0, (1 << 4) | 0b0100, 1, 0]) + SILENT_B0[4:],
                    "B0 自测 (A+1)")
                await asyncio.sleep(3.0)
                st = [f for f in rec.frames if f[0] == "selftest"]
                b1_seqs = [f[3][1] for f in st if f[3][:1] == b"\xb1" and len(f[3]) >= 2]
                if 1 in b1_seqs:
                    rec.line("  ✓ 自测通过: 收到 B1(seq=1), 通知通路正常, 交互段零帧=有效阴性")
                elif st:
                    rec.line(f"  ✗ 自测异常: 收到 {len(st)} 帧但无 B1(seq=1): "
                             + " ".join(f[3].hex().upper() for f in st))
                else:
                    rec.line("  ✗ 自测失败: B0(seq=1) 后无任何回应 —— 通知通路异常, "
                             "设备可能处于边控锁定/未就绪状态, 后续零帧仅供参考")

            rec.line("--- 启动 100ms B0 静默流 (复刻官方 App 会话) ---")
            stream_on = asyncio.Event()
            disconnect = asyncio.Event()
            client._dial_disconnect_cb = lambda _c: disconnect.set()

            async def stream() -> None:
                fails = 0
                while not stream_on.is_set() and not disconnect.is_set():
                    try:
                        await client.write_gatt_char(V3_WRITE, SILENT_B0, response=False)
                        fails = 0
                    except Exception as exc:
                        fails += 1
                        if fails in (1, 5, 30):
                            rec.line(f"  ⚠ B0 流写入失败 (连续 {fails}): {exc!r}")
                        if fails >= 5:
                            rec.line("  ⚠ B0 流连续失败, 可能已断线 —— 继续记录通知, 交互请照常")
                    await asyncio.sleep(0.1)

            stream_task = asyncio.create_task(stream())
            await asyncio.sleep(1.0)

            for name, instruction, seconds in Q_PHASES:
                if disconnect.is_set():
                    rec.line(f"  !! 连接已断开, 跳过阶段 {name} !!")
                    break
                await run_phase(rec, name, instruction, seconds)

            stream_on.set()
            try:
                await stream_task
            except Exception:
                pass

            rec.line("--- 收尾: A 通道强度清零 (B0 seq=3, 方式 0b1100) ---")
            rec.set_phase("zero")
            await write_frame(
                client,
                bytes([0xB0, (3 << 4) | 0b1100, 0, 0]) + SILENT_B0[4:],
                "B0 清零 (A=0)")
            await asyncio.sleep(2.0)

    rec.line("")
    rec.line("================ 总结 ================")
    by_phase: dict[str, int] = {}
    others: list[tuple[str, str, bytes]] = []
    b1_seq0 = 0
    for phase, _t, tag, blob in rec.frames:
        by_phase[phase] = by_phase.get(phase, 0) + 1
        if blob[:1] == b"\xb1" and len(blob) >= 2 and blob[1] == 0:
            b1_seq0 += 1
        elif blob[:1] != b"\xb1":
            others.append((phase, tag, blob))
    rec.line(f"总帧数 {len(rec.frames)} (B1 seq=0 共 {b1_seq0} 帧); 各阶段: " +
             " ".join(f"{k}={v}" for k, v in sorted(by_phase.items())))
    if others:
        rec.line(f"发现 {len(others)} 个非 B1 帧 (潜在按键/未记录帧):")
        seen: set[bytes] = set()
        for phase, tag, blob in others:
            key = blob[:1] + bytes([len(blob)])
            mark = "" if key in seen else " (首次)"
            seen.add(key)
            rec.line(f"  [{phase}] [{tag}] {len(blob)}B {blob.hex().upper()}{mark}")
    rec.line("判定要点: selftest 是否收到 B1(seq=1); q4/q5 是否出现 B1(seq=0); "
             "q1~q3 是否出现非 B1 帧。")
    rec.close()
    print(f"\n日志已保存: {rec.log_path}")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
