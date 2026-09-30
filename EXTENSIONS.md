# DGStudio 扩展开发文档（联动模块 SDK）

DGStudio 采用**模块化架构**：OSC 联动、未来的其他联动（Mqtt、游戏插件、
硬件外设……）都以「联动模块」的形式接入。模块放置在应用目录的
`modules/` 下，可在「模块」页**实时安装 / 卸载 / 启动 / 停止**，无需重启。

模块能拿到什么：

* 引擎公开命令层 —— 强度增减、直接设置、波形、一键开火、急停等；
* **强度参数公开 API** —— 最大强度、步长、开火强度、开火时长等参数的读写；
* 实时设备状态（EngineState）与事件总线（StateEvents）；
* 模块私有设置（自动持久化到 `config.json`）。

---

## 1. 快速开始

### 1.1 目录结构

```
DGStudio/
├── plugins.py                  模块宿主（ModuleBase / ModuleContext / PluginManager）
├── modules/                    模块目录（应用启动时与「扫描模块目录」时扫描）
│   ├── osc_bridge/             内置模块：VRChat OSC 联动
│   │   ├── __init__.py
│   │   ├── bridge.py           桥接器实现（python-osc）
│   │   └── plugin.py           模块入口：META + 模块类
│   └── strength_logger/        示例模块：强度日志（默认不启用）
│       └── plugin.py
├── config.json                 主配置（连接/控制/日志等核心设置）
└── config/                     模块配置目录（由模块宿主自动维护）
    ├── modules.json            各模块启用（自启动）状态
    ├── osc.json                OSC 模块设置（settings_key 命名）
    └── alice_cradle.json       各模块设置文件同理
```

每个模块一个文件夹，**最少只需一个 `plugin.py`**：

* 文件夹内含 `__init__.py`（包形式）或只有 `plugin.py`（散文件形式）均可，
  宿主会自动选择合适的加载方式（打包版 exe 同样支持运行时放入新模块）；
* 模块如果要拆多个文件，推荐使用包形式（`__init__.py` + 任意 `.py`），
  其余文件用相对导入或与 `plugin.py` 同目录的普通导入。

### 1.2 最小模块

`modules/hello/plugin.py`：

```python
META = {
    "id": "hello",                      # 全局唯一，建议与文件夹同名
    "name": "示例模块",
    "version": "0.1.0",
    "description": "一句话说明模块用途（显示在模块页）。",
    # 可选：模块提供的负鼠按键动作前缀（见 §3），供未加载时的配置反查
    # "actions": ["hello"],
}

from plugins import ModuleBase


class HelloModule(ModuleBase):
    id = META["id"]
    name = META["name"]
    version = META["version"]
    description = META["description"]
    settings_key = ""                   # 留空 → 设置文件 config/hello.json

    def on_load(self, ctx) -> None:     # 加载时调用一次
        self.ctx = ctx
        ctx.log("hello 已加载")

    def on_unload(self) -> None:        # 卸载时调用，必须清理事件订阅等资源
        self.ctx.log("hello 已卸载")

    async def start(self) -> None:      # 启动（运行在引擎 asyncio 循环上）
        self.ctx.log("hello 已启动")

    async def stop(self) -> None:       # 停止（卸载前宿主会先调用它）
        pass

    def is_running(self) -> bool:       # 模块页据此显示状态胶囊
        return False
```

要点：

* `META` 在模块**不执行代码**的情况下被模块页读取（AST 解析字面量），
  因此必须是一个纯字面量字典；
* 模块类**继承 `ModuleBase`** 可获得 IDE 补全与默认空实现；不继承、按
  鸭子类型提供 `id` / `name` / `on_load` / `on_unload` 也可以（缺失的
  `start` / `stop` / `is_running` 自动补为空实现）；
* 宿主取 `plugin.py` 里定义的**第一个**模块类，其余类保持私有。

### 1.3 生命周期

```
模块页「安装并启动」            模块页「卸载」
        │ install(id)                  │ uninstall(id)
        ▼                              ▼
   on_load(ctx)                   stop()  ←── 若在运行
        │                              │
   start()（异步）                 on_unload()
        │                              │
   （运行中，is_running()==True）    实例移除、代码保留在磁盘
```

* **安装** = 写入启用状态（重启后自动加载）+ 立即加载并启动；
* **卸载** = 停止运行 + 移除实例 + 启用状态记为关闭。模块**文件不会被删除**，
  内置模块（随应用分发的）尤其如此；要彻底移除自带模块请删除其文件夹；
* `start()` / `stop()` 运行在**引擎 asyncio 事件循环**上，可以直接 `await`；
  需要从事件订阅等非异步上下文提交协程时用 `ctx.submit(coro)`；
* 事件回调运行在引擎线程（或 python-osc 的接收线程）内——不要在里面
  直接操作 UI 或阻塞，需要时用 `ctx.submit()` 切回引擎循环。

---

## 2. ModuleContext API（交给模块的公开接口）

模块**只应通过 `ctx`（ModuleContext）访问引擎**。下表为全部公开成员：

### 2.1 基础设施

| 成员 | 说明 |
|---|---|
| `ctx.engine` | 引擎实例（仅用于传递给需要它的对象，日常操作用下面的封装） |
| `ctx.events` | 事件总线（`StateEvents`），可用事件见 §4 |
| `ctx.log(msg)` | 写应用日志（自动带 `[模块id]` 前缀），同时进 UI 日志页与日志文件 |
| `ctx.submit(coro)` | 把协程提交到引擎事件循环，返回 `concurrent.futures.Future` |
| `ctx.settings` | 模块私有设置字典（持久化），读改即时生效，保存时机见 §5 |

### 2.2 设备状态

| 成员 | 说明 |
|---|---|
| `ctx.get_state()` | 当前 `EngineState`（含全部 `Slot`：强度/上限/电量/气压/探活） |
| `ctx.devices()` | 已接入设备列表 `[{slot_id, name, type, family}]` |
| `ctx.resolve_slot(slot_id=None, family=None, output_only=False)` | 解析目标设备：显式 slot_id 优先，其次按 family，再次默认第一台（可排除传感器型灵猫） |

设备家族：`COYOTE`（郊狼，电刺激）、`OVC`（负鼠，振动）、`BMTR`（灵猫，气压
传感器）。`dglab.state.family_of(type)` 可从设备型号得到家族。

### 2.3 强度参数（公开方法，模块读写的正式入口）

**读取快照：**

```python
params = ctx.intensity_params()               # 默认输出设备；也可 ctx.intensity_params(slot_id)
```

返回字段：

| 字段 | 类型 | 含义 |
|---|---|---|
| `slot_id` | str \| None | 解析到的设备（未接入设备时为 None） |
| `connected` | bool | 设备是否在线 |
| `strength` | dict | `{"A": int, "B": int}` 当前强度 |
| `strength_limit` | dict | `{"A": int, "B": int}` 设备回报的硬件上限 |
| `channel_status` | dict | `{"A": int, "B": int}` 通道探活（0/2=正常，1=异常） |
| `max_strength` | int | 最大强度上限（本设备独立值，未设置则全局默认 100） |
| `strength_step` | int | 强度步长（已按设备型号量化，OVC 至少 10） |
| `fire_strength` | int | 一键开火强度（0 = 跟随 max_strength） |
| `fire_duration_s` | float | 一键开火时长（秒） |
| `wave_duration_s` | float | Socket V3 波形循环时长（秒） |
| `wave` | dict | `{"A": str, "B": str}` 当前选定波形名 |

**写入参数：**

```python
ctx.set_intensity_param("max_strength", 150)              # 全局
ctx.set_intensity_param("fire_strength", 80, slot_id=sid) # 仅该设备（device_settings 覆盖）
```

支持的键与范围：

| 键 | 范围 | 说明 |
|---|---|---|
| `max_strength` | 0–200 (int) | 最大强度上限（钳制一切强度设定，含开火/直接设置） |
| `strength_step` | 1–50 (int) | 强度加减步长（OVC 设备会被量化到 10 的倍数） |
| `fire_strength` | 0–200 (int) | 开火强度，0 表示跟随 max_strength |
| `fire_duration_s` | 0.1–60 (float) | 定时开火时长 |
| `wave_duration_s` | 1–120 (float) | 仅全局（Socket V3 波形循环时长），无设备级覆盖 |

无效键抛 `ValueError`；越界值自动钳制。写入后发出 `intensity_params` 事件。

**逐项便捷读取：**

```python
ctx.strength(slot_id, "A")           # 当前强度
ctx.strength_limit(slot_id, "A")     # 硬件上限
ctx.device_setting(slot_id, key)     # 设备级设置（含回退全局），任意键
ctx.device_step(slot_id)             # 量化后的强度步长
ctx.wave_selection()                 # {"A": 波形名, "B": 波形名}
ctx.wave_order("COYOTE")             # 该家族可用波形名序列（静默/持续在前）
```

**控制命令（均为引擎公开方法，返回协程，需 `ctx.submit(await)` 或在异步
上下文中直接 `await`）：**

| 方法 | 说明 |
|---|---|
| `ctx.set_strength(channel, value, slot_id=None)` | 直接设置强度（自动钳制 max_strength 与设备量化） |
| `ctx.add_strength(channel, delta, slot_id=None)` | 强度增减（自动步长/量化/上限保护） |
| `ctx.reset_strength(channel, slot_id=None)` | 归零：强度清零 + 波形切回静默 |
| `ctx.set_wave(channel, name, slot_id=None)` | 切换波形（不中断强度会话） |
| `ctx.fire_start(slot_id=None)` / `ctx.fire_stop(slot_id=None)` | 按住持续开火（60 秒安全超时；结束后恢复原强度与波形） |
| `ctx.zap(channel, seconds, slot_id=None)` | 定时爆发（等价 fire） |
| `ctx.emergency_stop()` | 急停：全部输出设备强度清零 + 波形重置 |

---

## 3. 按键绑定动作（负鼠按键 → 模块功能）

模块可以把自身功能挂到负鼠物理按键上：加载时向宿主注册动作，控制页绑定
选择框随之出现该项；卸载时动作自动撤下，配置中的相关绑定由应用检测并提示
（见下文「配置检查」）。OSC 模块的「发送 OSC 参数…」即由此机制提供。

```python
from plugins import ButtonAction, ModuleBase

class MyModule(ModuleBase):
    ...

    def button_actions(self) -> list:
        return [ButtonAction(
            key="myaction",                     # 绑定值前缀（全局唯一）
            label="我的动作…",                   # 绑定选择框显示文本
            argument_placeholder="参数…",        # 非空 → 绑定值带参数 "<key>:<参数>"
            on_press=self._on_press,            # (slot_id, argument | None)
            on_release=self._on_release,        # 可选
        )]

    def _on_press(self, slot_id, argument) -> None:
        self.ctx.log(f"动作触发: {argument}")
```

* 绑定值存储在按键映射配置文件中，形如 `"myaction"` 或 `"myaction:参数"`；
* 按下/抬起时宿主在**引擎线程**调用 `on_press/on_release(slot_id, argument)`，
  应快速返回，耗时操作用 `ctx.submit()` 提交协程；
* 同名 `key` 只接受第一个注册的模块；
* `META` 的 `"actions": ["myaction"]` 是静态声明（不执行代码即可读取），
  用于模块**未加载**时把配置中的绑定反查到模块。

**配置检查（应用自动执行）**：应用启动、切换按键映射配置文件、以及**卸载
模块**时，若配置中的绑定引用了未加载模块的动作（例如 OSC 模块卸载后的
`"osc:…"`），会立即弹出提示：「启用并加载」自动安装对应模块并保留映射；
「拒绝加载」则相关绑定重置为「无动作」并保存。模块装卸会通过
`modules_changed` 事件即时刷新全部页面（绑定选择框随装卸增删动作项）。

## 4. 模块设置（独立配置文件）

模块设置**独立于主 config.json**，每个模块一个 JSON 文件，由宿主自动维护：

* 文件位置：`config/<文件名>.json`（与主 config.json 同目录的 `config/` 子目录）；
* 文件名：模块 `settings_key`（如 OSC 为 `config/osc.json`），
  未声明时用模块 id（如 `config/strength_logger.json`）；
* 启用（自启动）状态集中存放在 `config/modules.json`；
* 旧版存在主配置里的模块段（顶层 `osc` 等键与 `modules` 段）会在首次运行时
  自动迁移到新文件并从主配置移除。

`ctx.settings` 是一个**写穿字典（JsonDict）**：顶层键的写入/删除/更新立即
保存到文件，无需调用任何保存方法。

```python
def on_load(self, ctx):
    self.threshold = int(ctx.settings.get("threshold", 50))

def _save_threshold(self, value):
    self.ctx.settings["threshold"] = int(value)   # 即时落盘
    # 注意：嵌套字典的就地修改（settings["a"]["b"] = …）不会自动落盘，
    # 如需保存请调用 ctx.settings.save()
```

模块页的启用状态与设置文件完全持久化，重启后按 `config/modules.json` 恢复。
另外 `META` 可声明 `"default_enabled": true`，让模块在从未被显式安装/卸载过时
默认随应用启动（内置 OSC 模块即如此）。

### 4.1 配置项声明（META["config"]，配置文件为核心）

模块在 `META` 中用纯字面量声明自己的全部配置项（宿主不执行代码即可读取），
宿主在扫描模块与装载设置时**自动把缺失项按声明补齐**写进 `config/<stem>.json`：

```python
META = {
    ...,
    "config": {
        "port": {"label": "输出端口", "type": "int", "default": 9000,
                 "min": 1, "max": 65535, "group": "bridge",
                 "desc": "发送数值的目标端口"},
        "mappings": {"label": "输入映射表", "type": "list", "default": [],
                     "group": "map", "rows": "in", "desc": "行 {param, expr}"},
        "outputs": {"label": "输出映射表", "type": "list", "default": [],
                    "group": "map", "rows": "out", "desc": "行 {param, name, expr, type}"},
        "family": {"label": "目标设备家族", "type": "choice",
                   "choices": ["COYOTE", "OVC", "BMTR"], "default": "COYOTE",
                   "group": "settings"},
    },
}
```

每项支持的字段：`label`（显示名）、`type`、`default`、`desc`（说明列）、
`group`（联动页落位，见下）、以及按类型的附加字段——`int/float`：`min/max/step/unit`；
`choice`：`choices`；`list` 且 `rows` 为 `in`/`out` 时按映射表行渲染（见 §4.2）。

`type` 与 UI 控件的对应关系（联动页自动渲染，无需模块写任何界面代码）：

| type | 语义 | 控件 |
| --- | --- | --- |
| `int` / `float` | 数值 | 滑块（量程 ≤1000 时，带数值标签）或数字输入框；修改即落盘 |
| `str` | 文本 | 文本框 |
| `bool` | 开关 | 开关 |
| `choice` | 预定义选项 | 下拉选择（只能选预设值） |
| `map` | 嵌套字典 | 每个子键一行文本框（如 `device_prefixes`） |
| `list` + `rows: in` | 输入映射表 | 见 §4.2 |
| `list` + `rows: out` | 输出映射表 | 见 §4.2 |

`group` 决定条目在联动页模块卡片中的落位：`map` 组渲染成映射表（按 `rows`
区分输入 / 输出），其余组（`bridge` / `settings` 等）进「模块设置」。需要动态声明
的模块可覆写实例方法 `config_spec()`（优先于 `META["config"]`）。

### 4.2 联动参数模型（核心参数 + 双向映射表）

联动的一切可操作参数都由**核心**统一定义（`dglab/params.py`），参数名固定不可改；
模块只声明自己那一侧的参数，两者通过两张映射表连接。这是所有联动模块共用的
统一模型——联动页每张模块卡片都按「输出映射表 → 输入映射表 → 模块设置」渲染，
郊狼 / 负鼠 / 灵猫只是表格里的设备分组，OSC 与游戏数据类模块不再有特殊分支。

**核心参数目录（固定）**

* 输入参数（模块 → 核心 → 设备）：id 为 `in_*`（郊狼）/ `in_ovc_*`（负鼠），
  覆盖 A/B 通道的强度、波形选择、波形步进、瞬时脉冲、开火，加全局 `in_emergency`。
  `core_inputs()` 返回全部条目（`key` id、`label` 中文名、`type` Int/Bool、`range` 钳制范围）。
* 输出参数（核心 → 模块）：设备实时状态，id 为 `家族.信号`（首台）或
  `家族.序号.信号`（多台，如 `COYOTE.2.Battery`），外加全局 `Action`（App 按键反馈）。
  `output_specs(family, index)` 给出某台设备的信号集。

**模块侧参数（模块自行维护可写 / 可读两个方向）**

* **可写参数**（模块喂进核心信号空间，输入表达式以 `{名称}` 引用）：常规模块
  （如 Alice in Cradle）用 `META["params"]` 静态声明自己接收的命名数值
  （HP、MP、Hurt…），覆写 `link_params()` 返回 `[(变量名, 说明)]`；
* **可读参数**（模块从核心读走并回传给对端的字段）：`META["reads"]` 按
  「核心输出信号名 → {label, name, type}」声明（如 `{"Battery": {"name": "Battery"}}`），
  覆写 `read_params()` 返回 `[(信号名, 说明)]`。模块装载时空 `outputs` 表会按声明
  与配置家族自动落地默认输出行（如 AIC 的 StrengthA / Battery / Connected），
  联动页输出表也以它们作字段名联想；
* **OSC 模块特殊**：`META["dynamic_params"] = True`，头像参数**双向自定义**——
  输入 / 输出映射行都带 `name`（模块侧参数名），选择核心参数即按设备前缀模板
  自动生成默认参数名（`bridge.default_input_name` / `default_output_name`）与
  默认直传表达式，名字可自由改；模块装载时为缺名的输入行补默认名
  （`plugin.materialize_names`）。配置文件只保存两张映射表。

**两张映射表（配置文件只写这些）**

* `mappings` 输入表，行 `{param: 核心输入参数 id, expr: 表达式}`：表达式以 `{变量}`
  引用模块侧参数（常规模块的 `META["params"]` 或 OSC 的动态参数）与核心输出参数，
  自由四则运算，结果**取整钳制**到 `range` 后派发设备动作；留空即同名直传。
  **核心参数名固定，下拉选择不可改。**
* `outputs` 输出表，行 `{param: 核心输出参数 id, name: 模块侧字段名, expr: 表达式, type}`：
  求值后回传给模块（OSC 写 `/avatar/parameters/<name>`，游戏模块进 `GET /data`）。
  **来源参数固定，`name` 可由用户自由重命名**，表达式取整 / 归真。

联动页对两张表都提供增删行与「实时值」预览；「保存设置」写盘后，宿主对每个暴露
`reload_config()` 的运行中模块调用它，使映射改动**热生效**（无需重启模块）。桥接
地址 / 端口等改动仍需重新开关本模块才生效。

联动页的大卡片按**联动模块**分类（而非设备）；`config_init`（初始化配置）模块不出
卡片，专责配置的启动装载与保存 / 载入 / 导出（见设置页「配置文件」组）。声明了
`META["config"]` 的模块会在模块页卡片上自动出现「联动设置」跳转链接，指向其在联动页
的模块卡片。

---

## 5. 事件总线（ctx.events）

`ctx.events.on(event, callback)` 注册，回调异常会被吞掉（记入日志）。
`on(event, cb)` 返回传入的回调引用，`ctx.events.off(event, cb)` 取消订阅——
事件总线不感知模块生命周期，**on_unload 时必须取消自己的全部订阅**，
否则卸载后回调仍会被调用。

| 事件 | 参数 | 触发时机 |
|---|---|---|
| `state` | `EngineState` | 连接/配对/设备列表/强度等状态变化（高频） |
| `log` | `str` | 应用日志（含其他模块通过 `ctx.log` 写入的） |
| `frame_log` | `direction: str, frame: dict` | 协议数据帧（需开「记录通信数据帧」） |
| `action` | `int \| None` | App 物理按键反馈 0-9（None 表示清除） |
| `ovc_button` / `ovc_button_up` | `slot_id: str, bit: int` | 负鼠物理按键按下 / 抬起（先于按键绑定动作） |
| `saved_devices` | `list[dict]` | 已保存蓝牙设备列表变化 |
| `intensity_params` | `dict` | `set_intensity_param` 写入后（参数同 §2.3 快照） |
| `modules_changed` | `module_id: str` | 任意模块加载/卸载后（动作表、模块页随之刷新） |
| `binding_modules_missing` | `{"modules": [...], "bindings": {...}}` | 映射配置引用未启用模块动作时（界面弹窗处理） |

---

## 6. 线程模型与约束

```
UI 线程 (WinUI 3)                    引擎线程 (asyncio loop)          其他线程
─────────────────                    ────────────────────────         ────────
模块页 / 各页面  ── submit ────────► 模块 start/stop、命令协程
                                     事件回调（state/log/…） ◄────── 协议/OSC 接收线程
```

1. `on_load` / `on_unload` 在**提交操作的线程**上执行（通常引擎线程）；
2. `start` / `stop` 在**引擎事件循环**上执行；
3. `state` 等事件回调可能在**协议接收线程**触发——回调里只做轻量处理，
   发协程用 `ctx.submit()`，不要操作 UI；
4. 禁止在事件回调里长时间阻塞（会拖慢整个引擎）。

---

## 7. 调试与打包

* **日志**：`ctx.log()` 同时进「日志」页与 `dgstudio.log`；异常栈会用
  `traceback.format_exc()` 全文落日志；
* **加载失败**：模块页「扫描模块目录」后状态如实显示；常见错误是
  `META` 非字面量、忘记定义模块类、`plugin.py` 顶层抛异常；
* **热重载**：卸载后再安装会重新实例化，但 **Python 代码不会自动重载**
  （进程内模块缓存）。开发时改完代码请重启应用；分发给用户则无影响；
* **打包**：`DGStudio.spec` 已通过 `collect_submodules("modules")` 打包
  `modules/` 下的包形式模块；只有 `plugin.py` 的散文件模块在打包版中
  通过文件路径动态加载（放在 exe 旁的 `modules/` 即可）；
* **测试**：模块逻辑可脱离 UI 做单元测试——构造假 engine 或直接测纯函数，
  参照 `tests/` 现有写法。

---

## 8. 示例：strength_logger

`modules/strength_logger/plugin.py` 是可运行的完整示例：订阅 `state` 事件，
把每台设备 A/B 通道的强度变化写入日志（每设备限速 1 条/秒）。它演示了：

* `META` + 鸭子类型模块类（不继承 `ModuleBase` 也可加载）；
* `ctx.log` / `ctx.events` 的基本用法；
* 高频事件的限速处理（避免日志刷屏）。

复制该目录、改 `META.id`、按 §2 的 API 实现自己的联动逻辑即可。
