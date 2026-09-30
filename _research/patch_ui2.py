# -*- coding: utf-8 -*-
"""UI fixes: pressure history, 清除->归零, independent theme handling."""
import io

src = io.open('ui/main_window.py', encoding='utf-8').read()

# 3. restore pressure-history collection (lost in rewrite)
old = '''            summaries = [state.slots[sid].summary() for sid in sorted(state.slots)]
            self.DeviceInfoText.Text = "\\n".join(summaries) if summaries else "（无）"
            self.OscMapText.Text = self._osc_mapping_text(state)'''
new = '''            summaries = [state.slots[sid].summary() for sid in sorted(state.slots)]
            self.DeviceInfoText.Text = "\\n".join(summaries) if summaries else "（无）"
            self.OscMapText.Text = self._osc_mapping_text(state)

            # 灵猫气压历史 (折线图数据源, ~10Hz 采样)
            for sid, slot in state.slots.items():
                if family_of(slot.type) != "BMTR" or slot.pressure is None:
                    continue
                hist = self._pressure_hist.setdefault(sid, new_history())
                last_t = hist[-1][0] if hist else 0.0
                if time.monotonic() - last_t >= 0.08:
                    hist.append((time.monotonic(), slot.pressure))'''
assert old in src, "pressure history"
src = src.replace(old, new)

# 8. 清除 buttons -> 归零 (reset strength, keep waveform running)
for ch, suffix in (("A", ""), ("B", "")):
    old = f'''    def BtnWaveClear{ch}_Click(self, sender, args) -> None:
        self._submit(self.engine.clear_wave("{ch}", slot_id=self._fam_selected["COYOTE"]))'''
    new = f'''    def BtnWaveClear{ch}_Click(self, sender, args) -> None:
        # 归零: strength -> 0 while the waveform keeps looping, so the next
        # + press produces output immediately (no wave re-selection).
        self._submit(self.engine.reset_strength("{ch}",
                                               slot_id=self._fam_selected["COYOTE"]))'''
    assert old in src, f"clear {ch}"
    src = src.replace(old, new)

old = '''    def BtnWaveClearAO_Click(self, sender, args) -> None:
        self._submit(self.engine.clear_wave("A", slot_id=self._fam_selected["OVC"]))'''
new = '''    def BtnWaveClearAO_Click(self, sender, args) -> None:
        self._submit(self.engine.reset_strength("A", slot_id=self._fam_selected["OVC"]))'''
assert old in src, "clear AO"
src = src.replace(old, new)

old = '''    def BtnWaveClearBO_Click(self, sender, args) -> None:
        self._submit(self.engine.clear_wave("B", slot_id=self._fam_selected["OVC"]))'''
new = '''    def BtnWaveClearBO_Click(self, sender, args) -> None:
        self._submit(self.engine.reset_strength("B", slot_id=self._fam_selected["OVC"]))'''
assert old in src, "clear BO"
src = src.replace(old, new)

# relabel buttons 清除A/B -> 归零A/B
src = src.replace('Content="清除A"', 'Content="归零A"').replace('Content="清除B"', 'Content="归零B"')

# 5. independent theme: explicit root background brush + force layout update
old = '''    def _apply_theme(self, initial: bool = False) -> None:
        try:
            # The root element is self.ui (XamlLoader never binds x:Name on
            # the root element, so self.RootGrid does not exist).
            self.ui.RequestedTheme = ElementTheme.Dark if self._dark else ElementTheme.Light
            self.BtnTheme.Content = "浅色模式" if self._dark else "深色模式"
        except Exception as exc:
            self._append_log(f"主题切换失败: {exc!r}")
            return
        try:
            bar = self.window.AppWindow.TitleBar
            if self._dark:
                fg = Color(A=255, R=230, G=230, B=230)
                bg = Color(A=255, R=32, G=32, B=36)
            else:
                fg = Color(A=255, R=0, G=0, B=0)
                bg = Color(A=255, R=243, G=243, B=243)
            bar.ButtonForegroundColor = fg
            bar.ButtonBackgroundColor = bg
            bar.ButtonInactiveForegroundColor = fg
            bar.ButtonInactiveBackgroundColor = bg
        except Exception:
            pass'''
new = '''    def _apply_theme(self, initial: bool = False) -> None:
        """Independent theme handling: force the element theme, an explicit
        page background and the title-bar palette (never follows the system)."""
        from win32more.Microsoft.UI.Xaml.Media import SolidColorBrush

        try:
            # The root element is self.ui (XamlLoader never binds x:Name on
            # the root element, so self.RootGrid does not exist).
            self.ui.RequestedTheme = ElementTheme.Dark if self._dark else ElementTheme.Light
            self.BtnTheme.Content = "浅色模式" if self._dark else "深色模式"
        except Exception as exc:
            self._append_log(f"主题切换失败: {exc!r}")
            return
        try:
            bg_color = Color(A=255, R=32, G=32, B=36) if self._dark else \\
                Color(A=255, R=246, G=246, B=246)
            self.ui.Background = SolidColorBrush(bg_color)
            self.ui.UpdateLayout()
        except Exception as exc:
            self._append_log(f"主题背景设置失败: {exc!r}")
        try:
            bar = self.window.AppWindow.TitleBar
            if self._dark:
                fg = Color(A=255, R=230, G=230, B=230)
                bg = Color(A=255, R=32, G=32, B=36)
            else:
                fg = Color(A=255, R=0, G=0, B=0)
                bg = Color(A=255, R=246, G=246, B=246)
            bar.ButtonForegroundColor = fg
            bar.ButtonBackgroundColor = bg
            bar.ButtonInactiveForegroundColor = fg
            bar.ButtonInactiveBackgroundColor = bg
        except Exception:
            pass'''
assert old in src, "theme"
src = src.replace(old, new)

io.open('ui/main_window.py', 'w', encoding='utf-8').write(src)
print("UI patch OK")
