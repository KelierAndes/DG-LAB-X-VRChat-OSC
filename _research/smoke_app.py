"""Full-app smoke: engine + WinUI window, auto-closes after ~3 s."""
import os
import queue
import sys
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from win32more.Microsoft.UI.Xaml import Application
from win32more.winui3 import XamlApplication

from app import Engine
from ui.main_window import MainWindow

REPORT = {}


class App(XamlApplication):
    def OnLaunched(self, args) -> None:
        self.engine = Engine()
        self.engine.start()
        self.window = MainWindow(self.engine)
        REPORT["launched"] = True
        threading.Thread(target=self._auto_test, daemon=True).start()

    def _auto_test(self):
        time.sleep(2.0)

        def check_and_close():
            REPORT["status_text"] = self.window.StatusText.Text
            REPORT["log_lines"] = self.window.LogText.Text.count("\n") + 1 if self.window.LogText.Text else 0
            REPORT["wave_count"] = self.window.WaveA.Items.Size
            REPORT["slider_max"] = self.window.SliderA.Maximum
            REPORT["osc_toggle"] = self.window.OscToggle.IsOn
            self.window.window.Close()

        self.window.ui_queue.put(check_and_close)


exit_code = 0
try:
    XamlApplication.Start(App)
except Exception as exc:
    REPORT["start_error"] = repr(exc)
    exit_code = 1

print("REPORT:", REPORT)
with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "smoke_result.txt"), "w", encoding="utf-8") as f:
    import json
    json.dump(REPORT, f, ensure_ascii=False, default=str, indent=2)
sys.exit(exit_code)
