

from __future__ import annotations

import time

from win32more.Microsoft.UI.Xaml import Thickness
from win32more.Microsoft.UI.Xaml.Controls import Page
from win32more.winui3 import XamlClass

from ui import nav, theme, widgets as W
from ui.paths import xaml


class ModulesPage(XamlClass, Page):
    def __init__(self, shell):
        super().__init__()
        self.shell = shell
        self._sig = None
        self._last = 0.0
        self.LoadComponentFromFile(xaml("ModulesPage.xaml"), encoding="utf-8")
        self.rebuild()

    def tick(self) -> None:
        now = time.monotonic()
        if now - self._last < 1.0:
            return
        self._last = now
        sig = self._module_sig()
        if sig != self._sig:
            self.rebuild()

    def on_notify(self) -> None:
        self.rebuild()

    def _module_sig(self) -> tuple:
        modules = self.shell.engine.modules.list_modules()
        return tuple((m["id"], m["loaded"], m["running"]) for m in modules)

    def rebuild(self) -> None:
        self._sig = self._module_sig()
        self._last = time.monotonic()
        W.page_head(
            self.HeadHost,
            {"title": "模块",
             "subtitle": "联动模块的实时安装与卸载：加载即生效，无需重启",
             "breadcrumb": ["控制台", "模块"]},
            actions=[
                W.text_button("扫描模块目录", symbol="Refresh",
                              on_click=lambda s, e: self._rescan()),
            ],
        )
        self.ListHost.Content = self._module_list()

    def _rescan(self) -> None:
        found = self.shell.engine.modules.discover()
        self.shell.logs.append(
            f"已扫描模块目录 {self.shell.engine.modules.base_dir}，发现 {len(found)} 个模块")
        self.rebuild()

    def _module_list(self) -> object:
        modules = self.shell.engine.modules.list_modules()
        host = W.stack(spacing=10, h="stretch")
        if not modules:
            host.Children.Append(self._hint_card(
                "模块目录为空",
                f"将模块文件夹放入 {self.shell.engine.modules.base_dir} 后点击「扫描模块目录」。"))
        for meta in modules:
            host.Children.Append(self._module_card(meta))
        host.Children.Append(self._hint_card(
            "开发自己的模块",
            "模块是 modules/<id>/plugin.py 中的一个 ModuleBase 子类，可获得强度参数、"
            "波形、开火、急停等公开 API。接口说明见项目根目录 EXTENSIONS.md，"
            "示例参考「强度日志示例」模块。"))
        return host

    def _module_card(self, meta: dict) -> object:
        module_id = meta["id"]
        engine = self.shell.engine

        status_fg, status_bg, status_text = (
            ("success", "success_soft", "运行中") if meta["running"]
            else ("accent_text", "accent_soft", "已加载") if meta["loaded"]
            else ("text3", "track", "未安装"))
        tile = W.box(
            width=34, height=34, corner=8,
            background=theme.brush("accent_soft" if meta["running"] else "track"),
            child=W.icon(symbol="View", size=16,
                         color="accent_text" if meta["running"] else "text2"),
            v="center",
        )

        title = W.stack(horizontal=True, spacing=8, v="center")
        title.Children.Append(W.text(meta["name"], size=14, bold=W.SEMIBOLD))
        title.Children.Append(W.text(f"v{meta['version']}", size=11, color="text3", v="center"))
        title.Children.Append(W.text(module_id, size=11, color="text3",
                                     family="Consolas", v="center"))

        head = W.grid(W.auto(), W.star(1), W.auto())
        head.Children.Append(W.put(tile, 0))
        info = W.stack(spacing=2, margin=Thickness(10, 0, 0, 0), v="center")
        info.Children.Append(title)
        desc = W.text(meta["description"] or "（无描述）", size=12, color="text3", wrap=True)
        info.Children.Append(desc)
        head.Children.Append(W.put(info, 1))
        head.Children.Append(W.put(W.pill(status_text, status_fg, status_bg,
                                          dot_color=status_fg if meta["running"] else None), 2))

        buttons = W.stack(horizontal=True, spacing=8, v="center")
        if not meta["loaded"]:
            buttons.Children.Append(W.text_button("安装并启动", symbol="Download",
                                                  accent=True,
                                                  on_click=lambda s, e, mid=module_id: self._install(mid)))
        else:
            if meta["running"]:
                buttons.Children.Append(W.text_button("停止", symbol="Stop",
                                                      on_click=lambda s, e, mid=module_id: self._stop(mid)))
            else:
                buttons.Children.Append(W.text_button("启动", symbol="Play",
                                                      on_click=lambda s, e, mid=module_id: self._start(mid)))
            buttons.Children.Append(W.text_button("卸载", symbol="Remove",
                                                  on_click=lambda s, e, mid=module_id: self._uninstall(mid)))
        if meta["config"]:
            # 声明了配置项的模块在联动页有对应模块卡片，提供跳转入口
            buttons.Children.Append(nav.link("联动设置", "link"))

        actions = W.stack(horizontal=True, spacing=8, v="center", h="right")
        actions.Children.Append(buttons)

        foot = W.grid(W.star(1), W.auto())
        note = ("启用状态已保存，重启后自动加载" if meta["enabled"]
                else "已停用，重启后不会加载")
        foot.Children.Append(W.put(W.text(note, size=11, color="text3", v="center"), 0))
        foot.Children.Append(W.put(actions, 1))

        inner = W.stack(spacing=10, h="stretch")
        inner.Children.Append(head)
        inner.Children.Append(W.divider())
        inner.Children.Append(foot)
        return W.card(inner)

    def _hint_card(self, title: str, body: str) -> object:
        inner = W.stack(spacing=6)
        inner.Children.Append(W.text(title, size=13, bold=W.SEMIBOLD))
        inner.Children.Append(W.text(body, size=12, color="text3", wrap=True))
        return W.card(inner)

    def _run_module_action(self, coro, done_msg: str) -> None:
        engine = self.shell.engine

        def _done(fut) -> None:
            exc = fut.exception()
            if exc is not None:
                engine._log(f"模块操作失败: {exc!r}")
            else:
                self.shell.logs.append(done_msg)
            self.shell.ui_queue.put(self.rebuild)

        fut = engine.submit(coro)
        fut.add_done_callback(_done)

    def _install(self, module_id: str) -> None:
        self._run_module_action(
            self.shell.engine.modules.install(module_id),
            f"模块已安装并启动: {module_id}")

    def _uninstall(self, module_id: str) -> None:
        self._run_module_action(
            self.shell.engine.modules.uninstall(module_id),
            f"模块已卸载: {module_id}")

    def _start(self, module_id: str) -> None:
        self._run_module_action(
            self.shell.engine.modules.start(module_id),
            f"模块已启动: {module_id}")

    def _stop(self, module_id: str) -> None:
        self._run_module_action(
            self.shell.engine.modules.stop(module_id),
            f"模块已停止: {module_id}")
