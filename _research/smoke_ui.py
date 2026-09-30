import os
import queue
import threading
import time

from win32more.winui3 import XamlApplication, XamlLoader
from win32more.Microsoft.UI.Xaml import Window
from win32more.Windows.Foundation import TimeSpan

RESULT = {}

XAML = """
<StackPanel xmlns="http://schemas.microsoft.com/winfx/2006/xaml/presentation"
            xmlns:x="http://schemas.microsoft.com/winfx/2006/xaml" Padding="20" Spacing="8">
    <TextBlock x:Name="Label" Text="Hello WinUI3-Python" FontSize="24"/>
    <Button x:Name="Btn" Content="Click me"/>
</StackPanel>
"""


class MainWindow:
    def __init__(self):
        self.ui_queue = queue.Queue()
        self.window = Window()
        self.window.Title = "SmokeTest"
        self.ui = XamlLoader.Load(self, XAML)
        self.window.Content = self.ui
        self.clicked = False
        self.Btn.Click += self.on_click

        timer = self.window.DispatcherQueue.CreateTimer()
        timer.Interval = TimeSpan(Duration=100_000)  # 100ms
        timer.IsRepeating = True
        timer.Tick += self._on_tick
        self._timer = timer
        timer.Start()

        self.window.Activate()

    def post(self, fn):
        self.ui_queue.put(fn)

    def _on_tick(self, sender, args):
        while True:
            try:
                fn = self.ui_queue.get_nowait()
            except queue.Empty:
                return
            fn()

    def on_click(self, sender, args):
        self.clicked = True
        self.Label.Text = "clicked!"


class App(XamlApplication):
    def OnLaunched(self, args):
        self.win = MainWindow()
        threading.Thread(target=self._auto_test, daemon=True).start()

    def _auto_test(self):
        time.sleep(1.0)
        win = self.win
        win.post(lambda: win.on_click(None, None))
        time.sleep(0.5)

        def check():
            RESULT["label"] = win.Label.Text
            RESULT["clicked"] = win.clicked

        win.post(check)
        time.sleep(0.5)

        def close():
            win.window.Close()

        win.post(close)


XamlApplication.Start(App)
print("RESULT:", RESULT)
ok = RESULT.get("clicked") and RESULT.get("label") == "clicked!"
with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "smoke_result.txt"), "w") as f:
    f.write(repr(RESULT))
os._exit(0 if ok else 1)
