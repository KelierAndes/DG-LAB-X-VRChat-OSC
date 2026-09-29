
from __future__ import annotations

from win32more.Microsoft.UI.Xaml.Controls import Page
from win32more.winui3 import XamlClass

from ui import live, widgets as W
from ui.paths import xaml

_GROUP_SYMBOLS = {
    "外观": "Highlight",
    "连接": "Remote",
    "OSC": "Sync",
    "日志": "List",
    "关于": "Important",
}

class SettingsPage(XamlClass, Page):
    def __init__(self, shell):
        super().__init__()
        self.shell = shell
        self._updating = False
        self.LoadComponentFromFile(xaml("SettingsPage.xaml"), encoding="utf-8")
        self.rebuild()

    def rebuild(self) -> None:
        self._updating = True
        try:
            W.page_head(
                self.HeadHost,
                {"title": "设置",
                 "subtitle": "外观、连接、控制与日志参数；修改即时写入配置，关闭窗口时保存",
                 "breadcrumb": ["设置"]},
                actions=[
                    W.text_button("保存到文件", symbol="Save", accent=True,
                                  on_click=lambda s, e: self.shell.engine.save_config()),
                    W.text_button("恢复默认提示", symbol="Refresh",
                                  on_click=lambda s, e: self.shell.logs.append(
                                      "恢复默认：请删除 config.json 后重启应用")),
                ],
            )

            host = self.GroupsHost
            host.Children.Clear()
            host.Children.Append(self._group_appearance())
            host.Children.Append(self._group_connection())
            host.Children.Append(self._group_osc())
            host.Children.Append(self._group_log())
            host.Children.Append(self._group_about())
        finally:
            self._updating = False

    def _wrap(self, title: str, subtitle: str, rows: list) -> object:
        body = W.stack(spacing=0)
        for i, row in enumerate(rows):
            if i:
                body.Children.Append(W.divider())
            body.Children.Append(row)
        head = W.card_head(title, subtitle=subtitle,
                           symbol=_GROUP_SYMBOLS.get(title, "Setting"), accent=True)
        inner = W.stack(spacing=10)
        inner.Children.Append(head)
        inner.Children.Append(body)
        return W.card(inner)

    def _group_appearance(self) -> object:
        rows = [self._switch_row("深色主题", "Fluent Studio 深色配色（浅色为亮色调色板）",
                                 bool(self.shell.engine.config.get("ui", {}).get("dark", True)),
                                 self._dark_changed)]
        return self._wrap("外观", "界面主题", rows)

    def _group_connection(self) -> object:
        cfg = self.shell.engine.config
        relay = cfg["relay"]
        rows = [
            self._switch_row("自动重连", "蓝牙连接意外断开时每 5 秒重试",
                             bool(cfg.get("auto_reconnect", True)),
                             self._auto_changed),
            self._switch_row("启动时恢复 OSC 桥接", "打开应用即按上次状态启动 OSC",
                             bool(cfg["osc"].get("enabled", True)),
                             self._osc_enabled_changed),
            self._text_row("Socket V4 中继服务器地址", "留空使用默认官方中继",
                           str(cfg.get("v4_url", "")),
                           lambda text: cfg.__setitem__("v4_url", text or cfg["v4_url"])),
            self._switch_row("Socket V4 使用本地中继", "本机作为局域网中继服务器，App 扫码直连",
                             bool(relay.get("v4_local", False)),
                             lambda value: relay.__setitem__("v4_local", value)),
            self._text_row("Socket V4 本地中继端口", "", str(relay.get("v4_port", 9998)),
                           lambda text: self._set_int(relay, "v4_port", text, 9998)),
            self._text_row("Socket V3 中继服务器地址", "留空使用默认官方中继",
                           str(cfg.get("v3_url", "")),
                           lambda text: cfg.__setitem__("v3_url", text or cfg["v3_url"])),
            self._switch_row("Socket V3 使用本地中继", "本机作为局域网中继服务器",
                             bool(relay.get("v3_local", False)),
                             lambda value: relay.__setitem__("v3_local", value)),
            self._text_row("Socket V3 本地中继端口", "", str(relay.get("v3_port", 9999)),
                           lambda text: self._set_int(relay, "v3_port", text, 9999)),
        ]
        return self._wrap("连接", "中继服务器与蓝牙", rows)

    def _group_osc(self) -> object:
        osc = self.shell.engine.config["osc"]
        rows = [
            self._text_row("VRChat 地址", "OSC 输出目标 IP",
                           str(osc.get("out_ip", "127.0.0.1")),
                           lambda text: osc.__setitem__("out_ip", text or osc["out_ip"])),
            self._text_row("输出端口", "发送设备数值到 VRChat 的端口",
                           str(osc.get("out_port", 9000)),
                           lambda text: self._set_int(osc, "out_port", text, 9000)),
            self._text_row("监听端口", "接收 VRChat 数据与 OSC 探测的端口",
                           str(osc.get("in_port", 9001)),
                           lambda text: self._set_int(osc, "in_port", text, 9001)),
            self._text_row("全局参数前缀", "Action 等全局参数的前缀",
                           str(osc.get("prefix", "DGLab")),
                           lambda text: osc.__setitem__("prefix", text or osc["prefix"])),
        ]
        return self._wrap("OSC", "地址与端口（修改后重新开关桥接生效）", rows)

    def _group_log(self) -> object:
        cfg = self.shell.engine.config
        rows = [
            self._switch_row("记录通信数据帧", "收发的协议帧写入日志（内容较多）",
                             bool(cfg.get("log_frames", True)),
                             lambda value: cfg.__setitem__("log_frames", value)),
            self._switch_row("写日志文件", "记录到应用目录 dglab_osc.log",
                             bool(cfg.get("log_to_file", True)),
                             self._log_file_changed),
        ]
        return self._wrap("日志", "记录策略", rows)

    def _group_about(self) -> object:
        rows = [
            self._info_row("版本", "DGOSC Studio", "2.0 (Fluent Studio)"),
            self._info_row("运行时", "win32more / WinUI 3", "0.8+"),
            self._info_row("配置文件", "应用目录下 config.json",
                           self.shell.engine.config.path),
        ]
        return self._wrap("关于", "版本与数据", rows)

    def _switch_row(self, label: str, description: str, is_on: bool, on_changed) -> object:
        toggle = W.switch(is_on)

        def handler(sender, args):
            on_changed(bool(toggle.IsOn))

        toggle.Toggled += handler
        return W.field_row(label, description, toggle)

    def _text_row(self, label: str, description: str, value: str, on_commit) -> object:
        box = W.text_box(text=value, width=170)

        def _commit(sender, args):
            if self._updating:
                return
            on_commit((box.Text or "").strip())

        box.TextChanged += _commit
        return W.field_row(label, description, box)

    def _info_row(self, label: str, description: str, value: str) -> object:
        return W.field_row(label, description,
                           W.text(str(value), size=12, color="text2",
                                  h="right", v="center", trimming=True))

    def _dark_changed(self, dark: bool) -> None:
        if self._updating:
            return
        if dark != self.shell._dark:
            self.shell.toggle_theme()

    def _auto_changed(self, value: bool) -> None:
        if self._updating:
            return
        self.shell.engine.config["auto_reconnect"] = value

    def _osc_enabled_changed(self, value: bool) -> None:
        if self._updating:
            return
        self.shell.engine.config["osc"]["enabled"] = value

    def _log_file_changed(self, value: bool) -> None:
        if self._updating:
            return
        self.shell.engine.set_file_logging(value)

    @staticmethod
    def _set_int(target: dict, key: str, text: str, default, low=1, high=65535) -> None:
        try:
            target[key] = max(low, min(high, int(text)))
        except (TypeError, ValueError):
            target[key] = default

    def flush_config(self) -> None:
        pass
