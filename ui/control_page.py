
from __future__ import annotations

import time
from collections import deque

from win32more.Microsoft.UI.Xaml import Thickness
from win32more.Microsoft.UI.Xaml.Controls import Page
from win32more.winui3 import XamlClass

from dglab.ble import LED_COLORS
from dglab.state import family_of
from ui import charts
from ui import live, theme, widgets as W
from ui.paths import xaml

class CardView:

    def __init__(self, sid: str, family: str):
        self.sid = sid
        self.family = family
        self.labels: dict[str, object] = {}
        self.meters: dict[str, object] = {}
        self.wave_combos: dict[str, object] = {}
        self.pill_host = None
        self.chart_host = None
        self.reading_tb = None
        self.edge_tb = None
        self.summary_tb = None
        self.hold_label = None
        self.led_combo = None
        self.chart_image = None
        self.hist_sig: tuple | None = None
        self.direct_a = None
        self.direct_b = None
        self.bindings: dict[int, object] = {}

class _Series:
    """line_chart 的一条曲线（鸭子类型，匹配原型 PressureSeries）。"""

    def __init__(self, label: str, color: int, points: tuple):
        self.label = label
        self.color = color
        self.points = points


class ControlPage(XamlClass, Page):
    def __init__(self, shell):
        super().__init__()
        self.shell = shell
        self._updating = False
        self._cards: dict[str, CardView] = {}
        self._slots_seen: tuple = ()
        self._wave_values: dict[str, list[str]] = {}
        self._pressure_hist: dict[str, deque] = {}
        self._last_pressure_sample: dict[str, float] = {}
        self._last_chart_render = 0.0
        self._last_pressure_render = 0.0
        self._last_value_render = 0.0
        self.LoadComponentFromFile(xaml("ControlPage.xaml"), encoding="utf-8")
        self.rebuild()

    def tick(self) -> None:
        state = self.shell.state
        slots = tuple(sorted(state.slots))
        if slots != self._slots_seen:
            self.rebuild()
            return
        now = time.monotonic()
        self._sample_pressure(now)
        if now - self._last_value_render >= 0.2:
            self._last_value_render = now
            self._update_values(state)
        if now - self._last_chart_render >= 0.25:
            self._last_chart_render = now
            self._render_wave_charts()
        if now - self._last_pressure_render >= 0.5:
            self._last_pressure_render = now
            self._render_pressure_charts()

    def rebuild(self) -> None:
        state = self.shell.state
        self._slots_seen = tuple(sorted(state.slots))
        W.page_head(
            self.HeadHost,
            {"title": "控制",
             "subtitle": "每台设备一张控制卡片：输出强度、波形选择与实时柱状波形图",
             "breadcrumb": ["控制台", "控制"]},
            actions=[
                W.text_button("全部归零", symbol="Clear",
                              on_click=lambda s, e: self._clear_all()),
                W.estop_button("急停全部设备",
                               on_click=lambda s, e: self._estop()),
            ],
        )

        host = self.CardsHost
        host.Children.Clear()
        self._cards.clear()
        if not state.slots:
            host.Children.Append(self._empty_note())
            return
        for sid in sorted(state.slots):
            slot = state.slots[sid]
            family = family_of(slot.type)
            view = CardView(sid, family)
            self._cards[sid] = view
            builder = self._sensor_card if family == "BMTR" else self._output_card
            host.Children.Append(builder(view, sid, slot))

    def _empty_note(self):
        row = W.grid(W.star(1), W.auto())
        row.Children.Append(W.put(
            W.text("尚未接入设备：先在「连接」页接入后再回来控制",
                   size=12, color="text3", v="center"), 0))
        button = W.text_button("前往连接页", symbol="Link", accent=True,
                               on_click=lambda s, e: self.shell.goto("connect"))
        row.Children.Append(W.put(button, 1))
        return W.box(
            corner=6, padding=Thickness(12, 9, 12, 9),
            background=theme.brush("section"), border=theme.brush("stroke"),
            child=row, h="stretch")

    def _int_field(self, target: dict, key: str, header: str, low: int, high: int,
                   width: float, default: int, round_to: int = 1,
                   keep_zero: bool = False) -> object:
        box = W.number_field(header, target.get(key, default), width)

        def _commit(sender, args):
            if self._updating:
                return
            try:
                value = int(box.Text)
            except (TypeError, ValueError):
                return
            if round_to > 1 and not (keep_zero and value <= 0):
                value = max(round_to, (value + round_to // 2) // round_to * round_to)
            value = max(low, min(high, value))
            if keep_zero and value <= 0:
                value = 0
            target[key] = value
            text = str(value)
            if box.Text != text:
                self._updating = True
                try:
                    box.Text = text
                finally:
                    self._updating = False

        box.TextChanged += _commit
        return box

    def _device_params(self, view: CardView, sid: str) -> object:
        engine = self.shell.engine
        ovc = view.family == "OVC"
        step = 10 if ovc else 1
        low = 10 if ovc else 0
        settings = engine.config.setdefault("device_settings", {}).setdefault(sid, {})
        if ovc:
            settings.setdefault(
                "max_strength",
                live.clamp_ovc_strength(engine.config.get("max_strength", 100)))
            settings.setdefault(
                "fire_strength",
                live.clamp_ovc_strength(engine.config.get("fire_strength", 0),
                                        allow_zero=True))
            settings.setdefault(
                "strength_step",
                live.round_step10(max(10, min(50, int(engine.config.get("strength_step", 1))))))
        row = W.stack(horizontal=True, spacing=10, v="center")
        row.Children.Append(self._int_field(
            settings, "max_strength", "最大强度上限", low, 200, 110,
            int(engine.config.get("max_strength", 100)), round_to=step))
        row.Children.Append(self._int_field(
            settings, "fire_strength", "开火强度", 0, 200, 100,
            int(engine.config.get("fire_strength", 0)), round_to=step,
            keep_zero=ovc))
        row.Children.Append(self._int_field(
            settings, "strength_step", "加减步长", low if ovc else 1, 50, 80,
            int(engine.config.get("strength_step", 1)), round_to=step))
        row.Children.Append(W.text(
            "仅对本设备生效 · 开火强度 0 = 跟随本卡上限"
            + (" · OVC 强度按 10 取整" if ovc else ""),
            size=11, color="text3", v="center"))
        return W.panel(row, padding=10)

    def _output_card(self, view: CardView, sid: str, slot):
        engine = self.shell.engine
        family = view.family

        head, pill_host = self._card_head(view, sid, slot, output=True)
        view.pill_host = pill_host

        rows = W.stack(spacing=8, h="stretch")
        for ch in ("A", "B"):
            rows.Children.Append(self._output_row(view, sid, ch))

        chart_panel = W.panel(self._chart_inner(view), padding=12)

        actions = W.stack(horizontal=True, spacing=8, v="center")

        def _fire(sender, args):
            self.shell.submit(engine.fire(slot_id=sid))

        actions.Children.Append(W.text_button("一键开火", symbol="Play", accent=True,
                                              on_click=_fire))
        hold_border, hold_label = W.hold_border("按住持续开火 (放开停止)")
        hold_border.PointerPressed += self._make_hold(sid, True)
        hold_border.PointerReleased += self._make_hold(sid, False)
        hold_border.PointerCaptureLost += self._make_hold(sid, False)
        view.hold_label = hold_label
        actions.Children.Append(hold_border)

        if family == "OVC" and self.shell.state.backend == "ble":
            actions.Children.Append(self._led_combo(view, sid))

        inner = W.stack(spacing=12, h="stretch")
        inner.Children.Append(head)
        inner.Children.Append(W.divider())
        inner.Children.Append(self._device_params(view, sid))
        inner.Children.Append(rows)
        inner.Children.Append(chart_panel)
        inner.Children.Append(actions)

        if family == "OVC" and self.shell.state.backend == "ble":
            for block in self._binding_blocks(view):
                inner.Children.Append(block)

        inner.Children.Append(W.text(
            "强度用加减键调节（步长见本卡参数）；波形可下拉跳变或 ‹ / › 逐步切换；"
            "「归零」清强度并切回静默。开火在静默时临时切持续波形，结束后自动恢复。",
            size=11, color="text3", wrap=True))
        return W.card(inner)

    def _output_row(self, view: CardView, sid: str, ch: str) -> object:
        engine = self.shell.engine
        family = view.family
        items = live.wave_items(family)
        if family not in self._wave_values:
            self._wave_values[family] = [value for _label, value in items]

        def _adjust(delta):
            def handler(sender, args):
                try:
                    step = max(1, min(50, int(engine.device_setting(sid, "strength_step"))))
                except (TypeError, ValueError):
                    step = 1
                if view.family == "OVC":
                    step = live.round_step10(max(10, step))
                self.shell.submit(engine.add_strength(ch, delta * step, slot_id=sid))
            return handler

        steps = W.stack(horizontal=True, spacing=4, v="center")
        steps.Children.Append(W.button("−", width=30, height=30, v="center",
                                       on_click=_adjust(-1)))
        steps.Children.Append(W.button("+", width=30, height=30, v="center",
                                       on_click=_adjust(+1)))

        value = W.stack(spacing=2, v="center")
        label = W.text(f"{ch}: 0/0", size=13, bold=W.SEMIBOLD)
        meter = W.meter(0, width=86)
        value.Children.Append(label)
        value.Children.Append(meter)
        view.labels[ch] = label
        view.meters[ch] = meter

        def _on_combo(sender, args):
            self._on_wave_combo(view, ch, combo)

        combo = W.combo([label for label, _v in items],
                        width=150, on_changed=_on_combo)
        view.wave_combos[ch] = combo

        def _step_wave(delta):
            def handler(sender, args):
                values = self._wave_values.get(family) or []
                if not values:
                    return
                index = combo.SelectedIndex
                if index is None or index < 0:
                    index = 0
                combo.SelectedIndex = (index + delta) % len(values)
            return handler

        wave = W.stack(horizontal=True, spacing=6, v="center")
        wave.Children.Append(W.text("波形", size=11, color="text3", v="center"))
        wave.Children.Append(W.button("‹", width=30, height=30, v="center",
                                      on_click=_step_wave(-1)))
        wave.Children.Append(combo)
        wave.Children.Append(W.button("›", width=30, height=30, v="center",
                                      on_click=_step_wave(+1)))

        def _reset(sender, args):
            self.shell.submit(engine.reset_strength(ch, slot_id=sid))

        wave.Children.Append(W.text_button("归零", symbol="Clear", on_click=_reset))

        direct = W.stack(horizontal=True, spacing=8, v="center", margin=Thickness(0, 6, 0, 0))
        box = W.text_box(placeholder=f"{ch} 强度", width=150)
        if ch == "A":
            view.direct_a = box
        else:
            view.direct_b = box
        direct.Children.Append(box)

        if ch == "B":
            def _apply(sender, args):
                self._apply_direct(view, sid)

            direct.Children.Append(W.text_button("应用直接设置", symbol="Accept",
                                                 on_click=_apply))
            hint = ("直接设置 10 的倍数 (自动取整)，任填其一或同时填写" if family == "OVC"
                    else "直接设置 0-200，任填其一或同时填写")
            direct.Children.Append(W.text(hint, size=11, color="text3", v="center"))

        g = W.grid(W.auto(), W.fixed(96), W.star(1))
        g.ColumnSpacing = 14
        g.Children.Append(W.put(steps, 0))
        g.Children.Append(W.put(value, 1))
        g.Children.Append(W.put(wave, 2))
        row = W.box(
            corner=6, padding=Thickness(10, 8, 10, 8),
            background=theme.brush("track"), child=g, h="stretch")

        outer = W.stack(spacing=0, h="stretch")
        outer.Children.Append(row)
        outer.Children.Append(direct)
        return outer

    def _apply_direct(self, view: CardView, sid: str) -> None:
        ovc = view.family == "OVC"
        for ch, box in (("A", view.direct_a), ("B", view.direct_b)):
            if box is None:
                continue
            text = (box.Text or "").strip()
            if not text:
                continue
            try:
                value = int(text)
            except ValueError:
                self.shell.logs.append(f"直接设置 {ch} 失败: 不是整数 ({text!r})")
                continue
            if ovc:
                value = live.clamp_ovc_strength(value)
            self.shell.submit(self.shell.engine.set_strength(ch, value, slot_id=sid))
            box.Text = str(value) if ovc else ""

    def _on_wave_combo(self, view: CardView, ch: str, combo) -> None:
        if self._updating:
            return
        values = self._wave_values.get(view.family) or []
        index = combo.SelectedIndex
        if index is None or index < 0 or index >= len(values):
            return
        value = values[index]
        self.shell.engine._selected_wave[ch] = value
        self.shell.submit(self.shell.engine.set_wave(ch, value, slot_id=view.sid))

    def _make_hold(self, sid: str, active: bool):
        engine = self.shell.engine

        def handler(sender, args):
            view = self._cards.get(sid)
            if active:
                self.shell.submit(engine.fire_start(slot_id=sid))
                if view is not None and view.hold_label is not None:
                    view.hold_label.Text = "开火中… (放开停止)"
            else:
                self.shell.submit(engine.fire_stop(slot_id=sid))
                if view is not None and view.hold_label is not None:
                    view.hold_label.Text = "按住持续开火 (放开停止)"

        return handler

    def _led_combo(self, view: CardView, sid: str) -> object:
        names = list(LED_COLORS)
        combo = W.combo(names, selected=1, width=140)
        view.led_combo = combo

        def _changed(sender, args):
            if self._updating:
                return
            index = combo.SelectedIndex
            if 0 <= index < len(names):
                self.shell.submit(self.shell.engine.set_led_color(names[index],
                                                                  slot_id=sid))

        combo.SelectionChanged += _changed
        cell = W.stack(spacing=4)
        cell.Children.Append(W.text("LED 颜色 (蓝牙)", size=11, color="text3"))
        cell.Children.Append(combo)
        return cell

    def _binding_blocks(self, view: CardView) -> list:
        bindings = (self.shell.engine.config.setdefault("ble", {})
                    .setdefault("ovc_buttons", {}))
        blocks: list = []
        groups = (("选择 / 主页键", live.OVC_BUTTON_BITS[0:3]),
                  ("方向键", live.OVC_BUTTON_BITS[3:7]),
                  ("动作键", live.OVC_BUTTON_BITS[7:11]))
        for title, bits in groups:
            rows = W.stack(spacing=10, h="stretch")
            for start in range(0, len(bits), 3):
                g = W.grid(W.star(1), W.star(1), W.star(1))
                g.ColumnSpacing = 16
                for i, (bit, name) in enumerate(bits[start:start + 3]):
                    g.Children.Append(W.put(
                        self._binding_dropdown(view, bit, name, bindings), i))
                rows.Children.Append(g)

            inner = W.stack(spacing=8, h="stretch")
            head = W.stack(horizontal=True, spacing=8, v="center")
            head.Children.Append(W.text(f"物理按键绑定 · {title}", size=11,
                                        bold=W.SEMIBOLD, color="text3"))
            head.Children.Append(W.text("蓝牙模式下生效", size=11, color="text3", v="center"))
            inner.Children.Append(head)
            inner.Children.Append(rows)
            blocks.append(W.panel(inner))
        return blocks

    def _binding_dropdown(self, view: CardView, bit: int, name: str, bindings) -> object:
        current = bindings.get(str(bit), "none")
        selected = 0
        for i, (key, _label) in enumerate(live.BUTTON_ACTIONS):
            if key == current:
                selected = i
                break
        combo = W.combo([label for _k, label in live.BUTTON_ACTIONS],
                        selected=selected, width=140)
        view.bindings[bit] = combo

        def _changed(sender, args):
            if self._updating:
                return
            index = combo.SelectedIndex
            if 0 <= index < len(live.BUTTON_ACTIONS):
                bindings[str(bit)] = live.BUTTON_ACTIONS[index][0]

        combo.SelectionChanged += _changed
        cell = W.stack(spacing=4)
        cell.Children.Append(W.text(f"{name} (bit{bit})", size=11, color="text3"))
        cell.Children.Append(combo)
        return cell

    def _sensor_card(self, view: CardView, sid: str, slot):
        engine = self.shell.engine
        head, pill_host = self._card_head(view, sid, slot, output=False)
        view.pill_host = pill_host

        reading = W.text("气压: -- kPa", size=22, bold=W.SEMIBOLD)
        edge = W.text("边控状态: --", size=13, color="text2", v="center")
        summary = W.text(slot.summary(), size=11, color="text3", v="center")
        view.reading_tb = reading
        view.edge_tb = edge
        view.summary_tb = summary

        chart_host = W.stack(spacing=6, h="stretch")
        view.chart_host = chart_host

        chart_inner = W.stack(spacing=8, h="stretch")
        readout = W.stack(horizontal=True, spacing=18, v="center")
        readout.Children.Append(reading)
        readout.Children.Append(edge)
        readout.Children.Append(summary)
        chart_inner.Children.Append(readout)
        chart_inner.Children.Append(W.text("气压曲线（最近 60 秒 · 0-60 kPa）",
                                           size=11, color="text3"))
        chart_inner.Children.Append(chart_host)
        chart_inner.Children.Append(W.text(
            "灵猫无输出通道：本页不下发任何波形 / 强度指令，仅订阅气压与边控状态。",
            size=11, color="text3", wrap=True))

        actions = W.stack(horizontal=True, spacing=8, v="center")

        def _reset(sender, args):
            self.shell.submit(engine.reset_pressure(slot_id=sid))

        def _flip(sender, args):
            self.shell.submit(engine.bmtr_flip(slot_id=sid))

        actions.Children.Append(W.text_button("气压清零", symbol="Clear", on_click=_reset))
        actions.Children.Append(W.text_button("翻转屏幕", symbol="Sync", on_click=_flip))
        if self.shell.state.backend == "ble":
            actions.Children.Append(self._led_combo(view, sid))

        inner = W.stack(spacing=12, h="stretch")
        inner.Children.Append(head)
        inner.Children.Append(W.divider())
        inner.Children.Append(W.panel(chart_inner, padding=12))
        inner.Children.Append(actions)
        return W.card(inner)

    def _card_head(self, view: CardView, sid: str, slot, *, output: bool):
        family = view.family
        tile = W.box(
            width=34, height=34, corner=8,
            background=theme.brush("accent_soft"),
            child=W.icon(symbol=live.FAMILY_SYMBOLS[family], size=15, color="accent_text"),
            v="center")

        title = W.stack(spacing=2, v="center")
        line = W.stack(horizontal=True, spacing=8, v="bottom")
        line.Children.Append(W.text(slot.name or slot.type or sid, size=15,
                                    bold=W.SEMIBOLD, trimming=True))
        line.Children.Append(W.text(f"{live.FAMILY_LABELS[family]} · {slot.type}",
                                    size=11, color="text3", v="bottom"))
        title.Children.Append(line)
        backend = self.shell.state.backend
        title.Children.Append(W.text(
            f"连接通道: {live.BACKEND_LABELS.get(backend, backend)}",
            size=11, color="text3"))

        trailing = W.stack(horizontal=True, spacing=10, v="center")
        bat = slot.battery
        if bat is not None:
            trailing.Children.Append(W.pill(
                f"电量 {bat}%", "success" if bat > 60 else "warning",
                "success_soft" if bat > 60 else "warning_soft"))
        if output:
            trailing.Children.Append(W.text("2 路输出", size=11, color="text3", v="center"))
        else:
            trailing.Children.Append(W.pill("只监听", "accent_text", "accent_soft"))
        pill_host = W.box(child=W.pill("在线", "success", "success_soft",
                                       dot_color="success"), v="center", h="right")
        trailing.Children.Append(pill_host)

        head = W.grid(W.fixed(46), W.star(1), W.auto())
        head.Children.Append(W.put(tile, 0))
        head.Children.Append(W.put(title, 1))
        head.Children.Append(W.put(_gap(trailing, 14), 2))
        return head, pill_host

    def _chart_inner(self, view: CardView) -> object:
        head = W.stack(horizontal=True, spacing=8, v="center")
        head.Children.Append(W.text("实时输出波形", size=11, bold=W.SEMIBOLD, color="text3"))
        head.Children.Append(W.text("A / B 各一栏 · 最右侧为最新采样 · 0-100",
                                    size=11, color="text3", v="center"))
        view.chart_image = W.image(width=720, height=130)
        inner = W.stack(spacing=8, h="stretch")
        inner.Children.Append(head)
        inner.Children.Append(view.chart_image)
        return inner

    def _update_values(self, state) -> None:
        self._updating = True
        try:
            for sid, view in self._cards.items():
                slot = state.slots.get(sid)
                if slot is None:
                    continue
                if view.family == "BMTR":
                    pressure = slot.pressure
                    view.reading_tb.Text = (f"气压: {pressure:.2f} kPa"
                                            if pressure is not None else "气压: -- kPa")
                    view.edge_tb.Text = ("边控状态: "
                                         + live.EDGE_STATES.get(slot.edge_state,
                                                                str(slot.edge_state)))
                    view.summary_tb.Text = slot.summary()
                    continue
                for ch in ("A", "B"):
                    out = live.output_row(slot, ch)
                    view.labels[ch].Text = f"{ch}: {out['value']}/{out['limit']}"
                    self._set_meter(view.meters[ch], out["percent"], width=86)
                self._sync_wave_combo(view)
        finally:
            self._updating = False

    def _sync_wave_combo(self, view: CardView) -> None:
        values = self._wave_values.get(view.family) or []
        for ch, combo in view.wave_combos.items():
            value = str(self.shell.engine._selected_wave.get(ch, ""))
            if value not in values:
                continue
            index = values.index(value)
            if combo.SelectedIndex != index:
                combo.SelectedIndex = index

    @staticmethod
    def _set_meter(meter, percent: float, *, width: float = 86) -> None:
        ratio = max(0.0, min(1.0, percent / 100.0))
        try:
            fill = list(meter.Children)[1]
            fill.Width = max(width * ratio, 6.0)
        except Exception:
            pass

    def _render_wave_charts(self) -> None:
        engine = self.shell.engine
        dark = self.shell._dark
        for sid, view in self._cards.items():
            if view.family == "BMTR" or view.chart_image is None:
                continue
            monitor = engine.wave_history(sid)
            samples = monitor.window(5.0) if monitor is not None else []
            try:
                png = charts.render_wave_live(samples, dark=dark)
                self.shell.set_image_bytes(view.chart_image, png)
            except Exception as exc:
                self.shell.logs.append(f"波形图渲染失败: {exc!r}")

    def _sample_pressure(self, now: float) -> None:
        state = self.shell.state
        for sid, slot in state.slots.items():
            if family_of(slot.type) != "BMTR" or slot.pressure is None:
                continue
            last = self._last_pressure_sample.get(sid, 0.0)
            if now - last >= 0.08:
                self._last_pressure_sample[sid] = now
                hist = self._pressure_hist.setdefault(sid, live.new_history())
                hist.append((now, slot.pressure))

    def _render_pressure_charts(self) -> None:
        for sid, view in self._cards.items():
            if view.family != "BMTR" or view.chart_host is None:
                continue
            hist = self._pressure_hist.get(sid)
            if not hist:
                if view.hist_sig is not None:
                    view.hist_sig = None
                    view.chart_host.Children.Clear()
                    view.chart_host.Children.Append(
                        W.text("等待气压采样…", size=11, color="text3"))
                continue
            sig = (len(hist), hist[-1][1])
            if sig == view.hist_sig:
                continue
            view.hist_sig = sig
            slot = self.shell.state.slots.get(sid)
            label = (slot.name if slot else sid) or sid
            series = [_Series(label, 0, tuple(v for _t, v in hist))]
            host = view.chart_host
            host.Children.Clear()
            host.Children.Append(W.line_chart(
                series, live.PRESSURE_COLORS,
                ymin=live.PRESSURE_MIN_KPA, ymax=live.PRESSURE_MAX_KPA,
                x_left="-60 s", x_right="现在", width=640, height=190))

    def _clear_all(self) -> None:
        self.shell.submit(self.shell.engine.clear_wave())

    def _estop(self) -> None:
        self.shell.submit(self.shell.engine.emergency_stop())

    def flush_config(self) -> None:
        pass

def _gap(el, left: float):
    if left:
        el.Margin = Thickness(left, 0, 0, 0)
    return el
