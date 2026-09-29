
from __future__ import annotations

import time

from win32more.Microsoft.UI.Xaml import Thickness
from win32more.Microsoft.UI.Xaml.Controls import Page
from win32more.winui3 import XamlClass

from dglab.state import family_of
from ui import live, nav, theme, widgets as W
from ui.paths import xaml

class DashboardPage(XamlClass, Page):
    def __init__(self, shell):
        super().__init__()
        self.shell = shell
        self.LoadComponentFromFile(xaml("DashboardPage.xaml"), encoding="utf-8")
        self._st = None
        self._lv = -1
        self._last = 0.0
        self.rebuild()

    def tick(self) -> None:
        shell = self.shell
        now = time.monotonic()
        if now - self._last < 0.4:
            return
        if shell.state is self._st and shell.logs.version == self._lv:
            return
        self.rebuild()

    def on_notify(self) -> None:
        self.rebuild()

    def _disconnect_all(self) -> None:
        async def _do():
            if self.shell.engine._backend is not None:
                await self.shell.engine._disconnect_backend()

        self.shell.submit(_do())
        self.shell.logs.append("已请求断开全部连接")

    def rebuild(self) -> None:
        shell = self.shell
        self._st = shell.state
        self._lv = shell.logs.version
        self._last = time.monotonic()

        W.page_head(
            self.HeadHost,
            {"title": "概览", "subtitle": "已连接设备状态、连接通道与实时 OSC 数值",
             "breadcrumb": ["控制台", "概览"]},
            actions=[
                W.text_button("刷新", symbol="Refresh", on_click=lambda s, e: self.rebuild()),
                W.text_button("全部断开", symbol="DisconnectDrive",
                              on_click=lambda s, e: self._disconnect_all()),
            ],
        )
        self._fill_stats()
        self.ChannelsHost.Content = self._channels_card()
        self.OscHost.Content = self._osc_card()

        host = self.DevicesHost
        host.Children.Clear()
        state = shell.state
        for sid in sorted(state.slots):
            host.Children.Append(self._device_card(sid, state.slots[sid]))
        if not state.slots:
            host.Children.Append(self._empty_note())

    def _fill_stats(self) -> None:
        host = self.StatsHost
        host.Children.Clear()
        host.ColumnDefinitions.Clear()
        for i, stat in enumerate(live.stats(self.shell.engine, self.shell.logs)):
            host.ColumnDefinitions.Append(W.column(stars=1))
            host.Children.Append(W.put(W.card(self._stat_body(stat)), i))

    def _stat_body(self, stat: dict):
        g = W.grid(W.fixed(46), W.star(1), W.auto())
        ic = W.icon(symbol=stat["symbol"], size=16,
                    color="accent" if stat["accent"] else "text3")
        tile = W.box(
            width=38, height=38, corner=8,
            background=theme.brush("accent_soft" if stat["accent"] else "track"),
            child=ic,
        )
        g.Children.Append(W.put(tile, 0))

        info = W.stack(spacing=2, v="center")
        numbers = W.stack(horizontal=True, spacing=3, v="bottom")
        numbers.Children.Append(W.text(stat["value"], size=22, bold=W.SEMIBOLD,
                                       trimming=True))
        if stat["unit"]:
            numbers.Children.Append(W.text(stat["unit"], size=12, color="text3", v="bottom"))
        info.Children.Append(numbers)
        info.Children.Append(W.text(stat["label"], size=12, color="text3", trimming=True))
        g.Children.Append(W.put(info, 1))

        detail = stat.get("detail")
        if detail:
            right = W.stack(spacing=1, v="center", h="right")
            for i, line in enumerate(detail):
                right.Children.Append(W.text(line, size=10,
                                             color="text2" if i == 0 else "text3",
                                             trimming=True, h="right"))
            g.Children.Append(W.put(right, 2))
        else:
            fg, bg = ("success", "success_soft") if stat["accent"] else ("text3", "track")
            g.Children.Append(W.put(W.pill(stat["trend"], fg, bg), 2))
        return g

    def _device_card(self, sid: str, slot):
        family = family_of(slot.type)
        fg, bg = ("success", "success_soft")

        tile = W.box(
            width=30, height=30, corner=7,
            background=theme.brush("accent_soft"),
            child=W.icon(symbol=live.FAMILY_SYMBOLS[family], size=14, color="accent_text"),
            v="center",
        )
        title = W.stack(horizontal=True, spacing=8, v="center")
        title.Children.Append(W.text(slot.name or slot.type or sid, size=14, bold=W.SEMIBOLD, trimming=True))
        title.Children.Append(W.text(f"{live.FAMILY_LABELS[family]} · {slot.type}", size=11, color="text3", v="center"))
        badge = W.pill("在线", fg, bg, dot_color=fg)

        head = W.grid(W.fixed(38), W.star(1), W.auto())
        head.Children.Append(W.put(tile, 0))
        head.Children.Append(W.put(title, 1))
        head.Children.Append(W.put(badge, 2))

        links = W.stack(horizontal=True, spacing=12, v="center")
        links.Children.Append(nav.link("连接", "connect"))
        links.Children.Append(nav.link("控制", "control"))

        if not slot.is_output_device:
            row = self._sensor_row(slot, links)
        else:
            row = self._output_row(slot, links)

        inner = W.stack(spacing=8, h="stretch")
        inner.Children.Append(head)
        inner.Children.Append(row)
        return W.card(inner, padding=12)

    def _sensor_row(self, slot, links):
        pressure = slot.pressure
        value = f"{pressure:.2f} kPa" if pressure is not None else "—"
        percent = max(0.0, min(1.0, (pressure or 0.0) / live.PRESSURE_MAX_KPA)) * 100
        row = W.grid(W.star(1.4), W.star(1), W.auto())
        row.ColumnSpacing = 16
        row.Children.Append(W.put(self._quick_cell("气压", value, percent, "accent"), 0))
        bat = slot.battery or 0
        row.Children.Append(W.put(
            self._quick_cell("电量", live.battery_text(slot), bat,
                             "success" if bat > 60 else "warning"),
            1))
        row.Children.Append(W.put(_gap(links, 10), 2))
        return row

    def _output_row(self, slot, links):
        row = W.grid(W.star(1), W.star(1), W.star(1), W.star(1.7), W.auto())
        row.ColumnSpacing = 16
        for i, ch in enumerate(("A", "B")):
            out = live.output_row(slot, ch)
            row.Children.Append(W.put(
                self._quick_cell(f"{ch} 强度", f"{out['value']}/{out['limit']}",
                                 out["percent"], "accent"),
                i))
        bat = slot.battery or 0
        row.Children.Append(W.put(
            self._quick_cell("电量", live.battery_text(slot), bat,
                             "success" if bat > 60 else "warning"),
            2))
        wave_a = live.wave_label(str(self.shell.engine._selected_wave.get("A", "")))
        wave_b = live.wave_label(str(self.shell.engine._selected_wave.get("B", "")))
        row.Children.Append(W.put(self._wave_cell(f"A {wave_a} · B {wave_b}"), 3))
        row.Children.Append(W.put(_gap(links, 10), 4))
        return row

    def _quick_cell(self, label: str, value: str, percent: float, tone: str):
        cell = W.stack(horizontal=True, spacing=8, v="center")
        cell.Children.Append(W.text(label, size=11, color="text3"))
        cell.Children.Append(W.text(value, size=13, bold=W.SEMIBOLD))
        cell.Children.Append(W.meter(percent, width=54, fg=tone))
        return cell

    def _wave_cell(self, value: str):
        cell = W.stack(horizontal=True, spacing=8, v="center")
        cell.Children.Append(W.text("波形", size=11, color="text3"))
        cell.Children.Append(W.text(value, size=12, color="text2", trimming=True))
        return cell

    def _empty_note(self):
        row = W.grid(W.star(1), W.auto())
        row.Children.Append(W.put(
            W.text("尚未接入设备：在连接页使用 Socket V3 / V4 中继或蓝牙扫描接入",
                   size=12, color="text3", v="center"), 0))
        row.Children.Append(W.put(nav.link("前往连接页", "connect"), 1))
        return W.box(
            corner=6, padding=Thickness(12, 9, 12, 9),
            background=theme.brush("section"), border=theme.brush("stroke"),
            child=row, h="stretch",
        )

    def _channels_card(self):
        rows = W.stack(spacing=0)
        entries = self._channel_entries()
        for i, entry in enumerate(entries):
            if i:
                rows.Children.Append(W.divider())
            rows.Children.Append(self._channel_row(entry))

        head = W.card_head(
            "连接通道",
            subtitle="Socket V3 / V4 中继 · 蓝牙 GATT · OSC 桥接",
            symbol="Remote",
            trailing=nav.link("管理连接", "connect"),
        )
        inner = W.stack(spacing=10)
        inner.Children.Append(head)
        inner.Children.Append(rows)
        return W.card(inner)

    def _channel_entries(self) -> list[dict]:
        engine = self.shell.engine
        state = self.shell.state
        entries: list[dict] = []
        backend = engine.backend_kind
        if backend in ("v4", "v3"):
            url = engine.config["v4_url" if backend == "v4" else "v3_url"]
            entries.append({
                "name": f"{'负鼠/郊狼 4.x' if backend == 'v4' else '郊狼 3.x'} · Socket V{backend[-1]}",
                "transport": "WebSocket",
                "endpoint": url,
                "direction": "中继",
                "state": "connected" if state.connected else "connecting",
            })
        elif backend == "ble":
            for sid in sorted(state.slots):
                slot = state.slots[sid]
                entries.append({
                    "name": f"{slot.name or slot.type or sid} · 蓝牙 GATT",
                    "transport": "BLE",
                    "endpoint": sid,
                    "direction": "双向",
                    "state": "connected",
                })
        osc = engine.osc
        osc_on = osc is not None and getattr(osc, "_running", False)
        cfg = engine.config["osc"]
        entries.append({
            "name": "VRChat OSC 桥接",
            "transport": "UDP",
            "endpoint": f"{cfg['out_ip']}:{cfg['out_port']} ← {cfg['in_port']}",
            "direction": "双向",
            "state": "connected" if osc_on else "closed",
        })
        return entries

    def _channel_row(self, entry: dict):
        token = {"connected": ("success", "已建立"),
                 "connecting": ("accent_text", "连接中"),
                 "closed": ("text3", "未启用")}.get(entry["state"], ("text3", "未启用"))
        g = W.grid(W.star(1), W.fixed(120), W.auto())

        info = W.stack(spacing=2, v="center")
        info.Children.Append(W.text(entry["name"], size=13, bold=W.SEMIBOLD, trimming=True))
        info.Children.Append(W.text(
            f"{entry['transport']} · {entry['endpoint']} · {entry['direction']}",
            size=11, color="text3", trimming=True))
        g.Children.Append(W.put(info, 0))

        g.Children.Append(W.put(_spacer(), 1))
        g.Children.Append(W.put(W.text(token[1], size=11, color=token[0]), 2))
        return W.box(height=48, child=g)

    def _osc_card(self):
        head = W.card_head(
            "OSC 数据值",
            subtitle="当前设备数值（对外发布的参数）",
            symbol="Sync",
            accent=True,
            trailing=nav.link("参数映射", "link"),
        )

        table_head = W.grid(W.star(2), W.fixed(56), W.fixed(96), W.star(2))
        gap = Thickness(12, 0, 0, 0)
        for label, col in (("参数", 0), ("类型", 1), ("当前值", 2), ("幅度", 3)):
            cell = W.text(label, size=12, color="text3", margin=gap if col else None)
            table_head.Children.Append(W.put(cell, col))

        rows = W.stack(spacing=0)
        rows.Children.Append(W.divider(margin=Thickness(0, 6, 0, 0)))
        lines = live.osc_value_rows(self.shell.state)
        if not lines:
            rows.Children.Append(W.box(height=36, child=W.text(
                "（未接入设备）", size=12, color="text3", v="center")))
        for line in lines:
            rows.Children.Append(self._osc_row(line))

        inner = W.stack(spacing=8)
        inner.Children.Append(head)
        inner.Children.Append(table_head)
        inner.Children.Append(rows)
        return W.card(inner)

    def _osc_row(self, line: dict):
        g = W.grid(W.star(2), W.fixed(56), W.fixed(96), W.star(2))
        gap = Thickness(12, 0, 0, 0)
        g.Children.Append(W.put(W.text(line["address"], size=12, color="text2",
                                       trimming=True, v="center"), 0))
        g.Children.Append(W.put(W.text(line["vtype"], size=11, color="text3",
                                       margin=gap, v="center"), 1))
        g.Children.Append(W.put(W.text(line["value"], size=13, bold=W.SEMIBOLD,
                                       margin=gap, v="center"), 2))
        meter = W.box(margin=gap, child=W.meter(line["percent"], width=140),
                      v="center", h="left")
        g.Children.Append(W.put(meter, 3))
        return W.box(height=36, child=g)

def _gap(el, left: float):
    el.Margin = Thickness(left, 0, 0, 0)
    return el

def _spacer():
    return W.box(height=1, h="stretch")
