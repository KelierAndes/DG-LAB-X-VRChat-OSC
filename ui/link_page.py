
from __future__ import annotations

import time

from win32more.Microsoft.UI.Xaml import Thickness
from win32more.Microsoft.UI.Xaml.Controls import Page
from win32more.winui3 import XamlClass

from ui import live, theme, widgets as W
from ui.paths import xaml

COYOTE_FIELDS = (
    ("in_strength_a", "郊狼A强度"),
    ("in_strength_b", "郊狼B强度"),
    ("in_wave_a", "郊狼A波形 (Int)"),
    ("in_wave_b", "郊狼B波形 (Int)"),
    ("in_wave_step_a", "郊狼A波形步进"),
    ("in_wave_step_b", "郊狼B波形步进"),
    ("in_fire", "郊狼开火 (Bool)"),
    ("in_emergency", "急停"),
)

OVC_FIELDS = (
    ("in_ovc_strength_a", "负鼠A强度"),
    ("in_ovc_strength_b", "负鼠B强度"),
    ("in_ovc_wave_a", "负鼠A波形 (Int)"),
    ("in_ovc_wave_b", "负鼠B波形 (Int)"),
    ("in_ovc_wave_step_a", "负鼠A波形步进"),
    ("in_ovc_wave_step_b", "负鼠B波形步进"),
    ("in_ovc_zap_a", "负鼠A脉冲 (Bool)"),
    ("in_ovc_zap_b", "负鼠B脉冲 (Bool)"),
    ("in_ovc_fire", "负鼠开火 (Bool)"),
)

class LinkPage(XamlClass, Page):
    def __init__(self, shell):
        super().__init__()
        self.shell = shell
        self._fields: dict[str, object] = {}
        self._mapping_tb = None
        self._pill_host = None
        self._toggle = None
        self._updating = False
        self._last_render = 0.0
        self.LoadComponentFromFile(xaml("LinkPage.xaml"), encoding="utf-8")
        self.rebuild()

    def tick(self) -> None:
        now = time.monotonic()
        if now - self._last_render < 0.5:
            return
        self._last_render = now
        self._update_status()

    def rebuild(self) -> None:
        self._updating = True
        try:
            W.page_head(
                self.HeadHost,
                {"title": "联动",
                 "subtitle": "VRChat OSC 参数映射：设备数值输出到 VRChat，头像参数下发到设备",
                 "breadcrumb": ["控制台", "联动"]},
                actions=[
                    W.text_button("保存设置", symbol="Save", accent=True,
                                  on_click=lambda s, e: self._save()),
                ],
            )
            self.VrcHost.Content = self._osc_card()
        finally:
            self._updating = False

    def _osc_card(self) -> object:
        cfg = self.shell.engine.config["osc"]

        head = W.card_head(
            "VRChat OSC 联动",
            subtitle="设备数值回写头像参数；头像参数下发控制设备",
            symbol="Contact", accent=True)

        self._pill_host = W.box(child=W.pill("已停止", "text3", "track"), v="center", h="right")
        toggle = W.switch(bool(cfg["enabled"]))
        self._toggle = toggle

        def _toggle_changed(sender, args):
            if self._updating:
                return
            self.flush_config()
            if bool(toggle.IsOn):
                self.shell.submit(self.shell.engine.osc_start())
            else:
                self.shell.submit(self.shell.engine.osc_stop())

        toggle.Toggled += _toggle_changed
        trailing = W.stack(horizontal=True, spacing=10, v="center")
        trailing.Children.Append(self._pill_host)
        trailing.Children.Append(toggle)

        head_g = W.grid(W.star(1), W.auto())
        head_g.Children.Append(W.put(head, 0))
        head_g.Children.Append(W.put(_gap(trailing, 14), 1))

        mapping_tb = W.text("（未接入设备）", size=12, color="text2",
                            family="Consolas", wrap=True)
        self._mapping_tb = mapping_tb
        output_block = W.stack(spacing=6, h="stretch")
        output_block.Children.Append(W.text(
            "当前输出参数映射（每台设备独立一组，同类型第 2 台起自动加序号）",
            size=11, bold=W.SEMIBOLD, color="text3"))
        output_block.Children.Append(W.box(
            corner=6, padding=Thickness(10, 8, 10, 8),
            background=theme.brush("track"),
            child=mapping_tb, h="stretch"))

        coyote_panel = self._mapping_panel("郊狼输入映射（作用于首个郊狼设备）", COYOTE_FIELDS)
        ovc_panel = self._mapping_panel("负鼠独立输入映射", OVC_FIELDS)

        note = W.text(
            "郊狼/负鼠设备输出: {设备名}StrengthA/StrengthB、LimitA/LimitB、Battery、"
            "Connected、ChannelOK_A/B；灵猫输出: Pressure (Float, kPa)、EdgeState (Int 0-4)。"
            "使用前请在 VRChat 的动作菜单开启 OSC (OSC → Enabled)。"
            "OSC 地址与端口在「设置」页配置，修改后重新开关上方桥接生效。",
            size=11, color="text3", wrap=True)

        inner = W.stack(spacing=12, h="stretch")
        inner.Children.Append(head_g)
        inner.Children.Append(W.divider())
        inner.Children.Append(output_block)
        inner.Children.Append(coyote_panel)
        inner.Children.Append(ovc_panel)
        inner.Children.Append(note)
        return W.card(inner)

    def _mapping_panel(self, title: str, fields) -> object:
        rows = W.stack(spacing=10, h="stretch")
        per_row = 4
        for start in range(0, len(fields), per_row):
            g = W.grid(*[W.star(1)] * per_row)
            g.ColumnSpacing = 12
            for i, (key, label) in enumerate(fields[start:start + per_row]):
                box = W.text_box(header=label,
                                 text=str(self.shell.engine.config["osc"][key]),
                                 width=140)
                self._fields[key] = box
                g.Children.Append(W.put(box, i))
            rows.Children.Append(g)

        inner = W.stack(spacing=8, h="stretch")
        inner.Children.Append(W.text(title, size=11, bold=W.SEMIBOLD, color="text3"))
        inner.Children.Append(rows)
        return W.panel(inner)

    def _update_status(self) -> None:
        engine = self.shell.engine
        osc_on = engine.osc is not None and getattr(engine.osc, "_running", False)
        if self._pill_host is not None:
            self._pill_host.Child = (
                W.pill("运行中", "success", "success_soft", dot_color="success")
                if osc_on else W.pill("已停止", "text3", "track"))
        if self._mapping_tb is not None:
            self._mapping_tb.Text = self._mapping_text()

    def _mapping_text(self) -> str:
        try:
            from vrc.osc_bridge import device_osc_names
            state = self.shell.state
            prefixes = self.shell.engine.config["osc"].get("device_prefixes", {})
            names = device_osc_names(state, prefixes)
        except Exception:
            return "（未接入设备）"
        lines = []
        for sid in sorted(names):
            family, base = names[sid]
            if family == "BMTR":
                params = f"{base}Pressure, {base}EdgeState, {base}Battery, {base}Connected"
            else:
                params = (f"{base}StrengthA/B, {base}LimitA/B, "
                          f"{base}Battery, {base}Connected, {base}ChannelOK_A/B")
            lines.append(f"{live.FAMILY_LABELS.get(family, family)}: {params}")
        return "\n".join(lines) if lines else "（未接入设备）"

    def _save(self) -> None:
        self.flush_config()
        self.shell.engine.save_config()

    def flush_config(self) -> None:
        osc = self.shell.engine.config["osc"]
        for key, box in self._fields.items():
            value = (box.Text or "").strip()
            if value:
                osc[key] = value
        if self._toggle is not None:
            osc["enabled"] = bool(self._toggle.IsOn)

def _gap(el, left: float):
    if left:
        el.Margin = Thickness(left, 0, 0, 0)
    return el
