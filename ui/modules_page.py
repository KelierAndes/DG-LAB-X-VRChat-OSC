

from __future__ import annotations

import os
import threading
import time

from win32more import asyncui
from win32more.Microsoft.UI.Xaml import Thickness
from win32more.Microsoft.UI.Xaml.Controls import Page
from win32more.winui3 import XamlClass

from ui import nav, theme, widgets as W
from ui.dialogs import confirm_dialog, pick_open_path
from ui.paths import xaml

_EXE_FILTER = "可执行文件 (*.exe)\0*.exe\0所有文件 (*.*)\0*.*\0"


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
        if engine.modules.module_mods_dir(module_id):
            inner.Children.Append(W.divider())
            inner.Children.Append(self._game_mod_row(module_id))
        return W.card(inner)

    def _game_mod_row(self, module_id: str) -> object:
        """游戏模组安装行：路径输入框 + 自动扫描 + 一键安装。"""
        engine = self.shell.engine
        cfg = engine.modules.settings_for(module_id)
        box = W.text_box(text=str(cfg.get("mods_root") or ""),
                         placeholder="游戏根目录（含 BepInEx），可手动输入或自动扫描",
                         width=330)

        def _scan(sender, args) -> None:
            self.shell.logs.append("正在扫描游戏目录…")

            def worker() -> None:
                marker = str(((engine.modules.meta(module_id) or {})
                              .get("mods") or {}).get("marker") or "")
                found = engine.modules.scan_game_roots(marker) if marker else []

                def apply() -> None:
                    if found:
                        box.Text = found[0]
                        self.shell.logs.append(
                            f"扫描到 {len(found)} 处游戏目录，已填入第一处：{found[0]}")
                    else:
                        self.shell.logs.append(
                            "未扫描到游戏目录，请手动输入游戏根目录后安装")

                self.shell.ui_queue.put(apply)

            threading.Thread(target=worker, daemon=True).start()

        def _install(sender, args) -> None:
            self._install_game_mod(module_id, (box.Text or "").strip() or None)

        row = W.stack(horizontal=True, spacing=8, v="center", h="stretch")
        row.Children.Append(W.text("游戏模组", size=11, color="text3", v="center"))
        row.Children.Append(box)
        row.Children.Append(W.text_button("自动扫描", symbol="Find",
                                          on_click=_scan))
        row.Children.Append(W.text_button("安装游戏模组", symbol="Download",
                                          accent=True, on_click=_install))
        return row

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

    # -------------------------------------------------- 一键安装游戏模组

    def _install_game_mod(self, module_id: str,
                          root: str | None = None) -> None:
        """模块携带的 mods/ 释放到游戏目录：路径框已填 / 记住的路径直接装，
        否则后台扫描，再不行转手动指定。全部文件操作在后台线程执行。"""
        self.shell.logs.append("正在定位游戏目录…")

        def worker() -> None:
            resolved, note = (root, "") if root \
                else self._resolve_game_root(module_id)
            if resolved is None:
                self.shell.ui_queue.put(
                    lambda: asyncui.create_task(
                        self._pick_and_install_game_mod(module_id)))
                return
            try:
                count = self.shell.engine.modules.install_game_mod(
                    module_id, resolved)
            except Exception as exc:
                self.shell.logs.append(f"游戏模组安装失败: {exc}")
                return

            dest = str((self.shell.engine.modules.meta(module_id)
                        or {}).get("mods", {}).get("dest") or "")

            def done() -> None:
                self._remember_mods_root(module_id, resolved)
                self.shell.logs.append(f"游戏模组已安装（{count} 个文件）→ "
                                       f"{resolved}\\{dest}{note}")
                self.rebuild()

            self.shell.ui_queue.put(done)

        threading.Thread(target=worker, daemon=True).start()

    def _resolve_game_root(self, module_id: str) -> tuple[str | None, str]:
        modules = self.shell.engine.modules
        cfg = modules.settings_for(module_id)
        remembered = str(cfg.get("mods_root") or "").strip()
        if remembered and os.path.isdir(os.path.join(remembered, "BepInEx")):
            return remembered, ""
        meta = modules.meta(module_id) or {}
        marker = str((meta.get("mods") or {}).get("marker") or "")
        if marker:
            candidates = modules.scan_game_roots(marker)
            if candidates:
                return candidates[0], f"（自动扫描命中 {len(candidates)} 处，取第一处）"
        return None, ""

    def _remember_mods_root(self, module_id: str, root: str) -> None:
        cfg = self.shell.engine.modules.settings_for(module_id)
        if cfg.get("mods_root") != root:
            cfg["mods_root"] = root
            cfg.save()

    async def _pick_and_install_game_mod(self, module_id: str) -> None:
        ok = await confirm_dialog(
            self.shell, "未自动找到游戏目录",
            "没有在已保存路径和本机磁盘浅层扫描中定位到游戏。\n\n"
            "点击「手动指定」选择游戏主程序（exe），将自动释放安装模组到其 "
            "BepInEx 目录。",
            primary="手动指定", close="取消")
        if not ok:
            self.shell.logs.append("已取消游戏模组安装")
            return
        picked = pick_open_path("选择游戏主程序", wildcard=_EXE_FILTER)
        if not picked:
            self.shell.logs.append("已取消游戏模组安装")
            return
        root = os.path.dirname(picked)
        try:
            count = self.shell.engine.modules.install_game_mod(module_id, root)
        except Exception as exc:
            self.shell.logs.append(f"游戏模组安装失败: {exc}")
            return
        self._remember_mods_root(module_id, root)
        dest = str((self.shell.engine.modules.meta(module_id)
                    or {}).get("mods", {}).get("dest") or "")
        self.shell.logs.append(f"游戏模组已安装（{count} 个文件）→ {root}\\{dest}")
