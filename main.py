"""Entry point: python main.py   (or the packaged DGLabOSC.exe)

Starts the DG-Lab engine on a background asyncio thread, then runs the
WinUI 3 (win32more) message pump on the main thread.

``--selftest``: launches the window, closes it automatically after ~3 s and
writes a JSON report to %TEMP%\\dglab_osc_selftest.json (exit 0 on success) -
used to smoke-test packaged builds.
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import threading
import time
import traceback

from win32more.winui3 import XamlApplication

CRASH_LOG = os.path.join(tempfile.gettempdir(), "dglab_osc_crash.log")
REPORT = {}


def _log_crash(context: str) -> None:
    try:
        with open(CRASH_LOG, "a", encoding="utf-8") as f:
            f.write(f"\n[{time.strftime('%Y-%m-%d %H:%M:%S')}] {context}\n")
            f.write(traceback.format_exc())
    except OSError:
        pass


class App(XamlApplication):
    engine = None  # type: ignore[var-annotated]
    window = None

    def OnLaunched(self, args) -> None:
        try:
            from app import Engine
            from ui.main_window import MainWindow

            if App.engine is None:
                engine = Engine()
                engine.start()
                App.engine = engine
            self.window = MainWindow(App.engine)
            App.window = self.window
            REPORT["launched"] = True
        except Exception:
            _log_crash("OnLaunched")
            REPORT["launched"] = False
            raise


def _run_selftest() -> None:
    """Auto-close the window after a few seconds and write a report."""

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
                REPORT["status_text"] = win.StatusText.Text
                REPORT["wave_count"] = win.WaveA.Items.Size
                REPORT["osc_toggle"] = bool(win.OscToggle.IsOn)
                # Theme toggle verification: switch and read back.
                before = str(win.ui.RequestedTheme)
                win.BtnTheme_Click(None, None)
                after = str(win.ui.RequestedTheme)
                win.BtnTheme_Click(None, None)
                REPORT["theme_before"] = before
                REPORT["theme_after_toggle"] = after
                REPORT["theme_ok"] = before != after
            except Exception:
                _log_crash("selftest probe")
                REPORT["error"] = "probe failed"
            try:
                win.window.Close()
            except Exception:
                pass

        window.ui_queue.put(probe_and_close)

    threading.Thread(target=watcher, daemon=True).start()


def main() -> int:
    selftest = "--selftest" in sys.argv
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
            if REPORT.get("launched") and "error" not in REPORT:
                code = 0
            else:
                code = 1
        except OSError:
            code = 1
    return code


if __name__ == "__main__":
    sys.exit(main())
