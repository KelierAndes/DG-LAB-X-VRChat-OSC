"""示例联动模块：强度日志。

演示 ModuleContext 的最小用法：订阅引擎状态事件，把各设备通道的强度
变化写入应用日志（每台设备每秒最多一条，避免刷屏）。默认不启用，
可在「模块」页安装体验，或作为开发模板复制修改。
"""

META = {
    "id": "strength_logger",
    "name": "强度日志示例",
    "version": "0.1.0",
    "description": "演示模块 API：订阅强度变化写入应用日志（每设备限速 1 条/秒）。"
                   "可作为开发新模块的模板。",
}


class StrengthLogger:
    """不继承也可运行——宿主按 ModuleBase 协议鸭子类型调用。

    继承 plugins.ModuleBase 可以获得默认空实现与 IDE 提示，推荐继承。
    """

    id = META["id"]
    name = META["name"]
    version = META["version"]
    description = META["description"]

    def on_load(self, ctx) -> None:
        self.ctx = ctx
        self._last = {}
        self._last_ts = {}
        self.ctx.log("强度日志示例已加载（可通过 ctx.engine 访问全部公开 API）")
        self._on_state_handler = self.ctx.events.on("state", self._on_state)

    def on_unload(self) -> None:
        self.ctx.events.off("state", self._on_state_handler)
        self.ctx.log("强度日志示例已卸载")

    def _on_state(self, state) -> None:
        import time

        now = time.monotonic()
        for slot_id, slot in state.slots.items():
            for channel in ("A", "B"):
                value = slot.strength.get(channel, 0)
                key = (slot_id, channel)
                if self._last.get(key) == value:
                    continue
                if now - self._last_ts.get(key, 0.0) < 1.0:
                    continue
                self._last[key] = value
                self._last_ts[key] = now
                self.ctx.log(f"{slot.name or slot_id} 通道 {channel} 强度 → {value}")

    def is_running(self) -> bool:
        return True
