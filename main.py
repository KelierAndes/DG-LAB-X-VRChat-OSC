from __future__ import annotations

import json
import os
import sys
import tempfile
import threading
import time
import traceback

from win32more.winui3 import XamlApplication

from ui.shell import App

CRASH_LOG = os.path.join(tempfile.gettempdir(), "dglab_osc_crash.log")
REPORT = {}


def _log_crash(context: str) -> None:
    try:
        with open(CRASH_LOG, "a", encoding="utf-8") as f:
            f.write(f"\n[{time.strftime('%Y-%m-%d %H:%M:%S')}] {context}\n")
            f.write(traceback.format_exc())
    except OSError:
        pass


def _run_selftest() -> None:
    def watcher() -> None:
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            window = App.window
            if window is not None:
                break
            time.sleep(0.2)
        else:
            REPORT["error"] = "window never appeared"
            os._exit(1)

        time.sleep(2.5)

        def probe_and_close():
            try:
                win = App.window
                # 注入模拟设备状态，验证实时数据装配路径
                from dglab.state import EngineState, Slot

                fake = EngineState(backend="ble", connected=True, paired=True,
                                   status_text="自检模拟状态")
                fake.slots["AABBCCDDEEFF"] = Slot(
                    slot_id="AABBCCDDEEFF", name="郊狼自检", type="COYOTE_1",
                    strength={"A": 62, "B": 46},
                    strength_limit={"A": 200, "B": 200}, battery=76)
                fake.slots["112233445599"] = Slot(
                    slot_id="112233445599", name="负鼠自检", type="OVC_1",
                    strength={"A": 20, "B": 30},
                    strength_limit={"A": 200, "B": 200}, battery=64)
                fake.slots["112233445566"] = Slot(
                    slot_id="112233445566", name="灵猫自检", type="BMTR_1",
                    pressure=18.4, edge_state=1, battery=41)
                win.state = fake

                for tag in ("dashboard", "connect", "control", "link", "log", "settings"):
                    win.goto(tag)
                    page = win._page(tag)
                    REPORT[f"page_{tag}"] = page is not None
                    try:
                        tick = getattr(page, "tick", None)
                        if tick is not None:
                            tick()
                        REPORT[f"tick_{tag}_ok"] = True
                    except Exception as exc:
                        REPORT[f"tick_{tag}_ok"] = False
                        REPORT["tick_error"] = f"{tag}: {exc!r}"

                control = win._page("control")
                control.tick()
                view = control._cards.get("AABBCCDDEEFF")
                REPORT["ctrl_label_a"] = view.labels["A"].Text if view else None

                ovc = control._cards.get("112233445599")
                page._updating = False
                combo_a = view.wave_combos["A"]
                combo_a.SelectedIndex = 1
                combo_a.SelectedIndex = 2
                REPORT["wave_step_ok"] = combo_a.SelectedIndex == 2
                led = ovc.led_combo
                led.SelectedIndex = 2
                REPORT["led_pick_ok"] = led.SelectedIndex == 2
                control._set_binding(ovc, 13, "fire")
                bind_label = ovc.bindings.get(13)
                REPORT["binding_pick_ok"] = (bind_label is not None
                                             and bind_label.Text == control._binding_label_text("fire"))
                page._updating = True

                from win32more.Microsoft.UI.Xaml import Visibility
                glow = ovc.button_glows.get(13)
                control.flash_button(13, True)
                lit = glow is not None and glow.Visibility == Visibility.Visible
                control.flash_button(13, False)
                held = glow.Visibility == Visibility.Visible  # 最短点亮窗口
                control._glow_state[13] = (0.0, 0.0)          # 模拟到期
                control._refresh_glows()
                REPORT["glow_flash_ok"] = (lit and held
                                           and glow.Visibility == Visibility.Collapsed)

                win.goto("dashboard")
                win._page("dashboard").tick()

                before = str(win.RootGrid.RequestedTheme)
                win.toggle_theme()
                after = str(win.RootGrid.RequestedTheme)
                REPORT["theme_before"] = before
                REPORT["theme_after_toggle"] = after
                REPORT["theme_ok"] = before != after
                REPORT["nav_tags"] = sorted(win._items.keys())
                REPORT["status_text"] = win.state.status_text
            except Exception:
                _log_crash("selftest probe")
                REPORT["error"] = "probe failed"
            try:
                win.Close()
            except Exception:
                pass

        window.ui_queue.put(probe_and_close)

    threading.Thread(target=watcher, daemon=True).start()


def main() -> int:
    selftest = "--selftest" in sys.argv
    if selftest:
        def _probe_engine():
            from app import Engine

            path = os.path.join(tempfile.gettempdir(), "dglab_osc_selftest_cfg.json")
            if os.path.exists(path):
                try:
                    os.remove(path)
                except OSError:
                    pass
            return Engine(config_path=path)

        App.engine_factory = _probe_engine
    code = 0
    try:
        if selftest:
            _run_selftest()
        XamlApplication.Start(App)
    except Exception:
        _log_crash("main")
        REPORT["fatal"] = traceback.format_exc()
        code = 1
    finally:
        engine = App.engine
        if engine is not None:
            try:
                engine.stop()
            except Exception:
                pass
    if selftest:
        try:
            path = os.path.join(tempfile.gettempdir(), "dglab_osc_selftest.json")
            with open(path, "w", encoding="utf-8") as f:
                json.dump(REPORT, f, ensure_ascii=False, default=str, indent=2)
            if REPORT.get("launched", True) and "error" not in REPORT:
                ok = all(v for k, v in REPORT.items()
                         if k.startswith("page_") or k.endswith("_ok"))
                code = 0 if ok else 1
            else:
                code = 1
        except OSError:
            code = 1
    return code


if __name__ == "__main__":
    sys.exit(main())
