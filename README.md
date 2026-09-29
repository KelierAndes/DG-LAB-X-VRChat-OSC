# DG-Lab × VRChat OSC 控制台

基于 **Python + WinUI 3** 的 DG-Lab（郊狼 / Coyote）控制客户端：支持 **Socket V4 / V3 网络连接** 与 **蓝牙 BLE 直连**，并按照 **VRChat OSC（头像参数）协议** 把设备的主要参数暴露给 VRChat，同时可由 VRChat 头像参数反向控制设备。

```
┌──────────────┐   WebSocket    ┌────────────────────┐   OSC (UDP)   ┌────────┐
│ DG-Lab App    │ ◄───────────► │  本客户端 (WinUI 3) │ ◄───────────► │ VRChat │
│ (4.0 / 3.x)   │   V4 / V3 中继 │  ┌──────────────┐  │  9000 / 9001  │  头像  │
└──────────────┘                │  │ 蓝牙 BLE 直连 │  │               └────────┘
┌──────────────┐  BLE 4.x       │  └──────────────┘  │
│ Coyote 主机   │ ◄────────────► │        引擎层       │
└──────────────┘                └────────────────────┘
```

---

## 功能

| 连接方式 | 说明 |
|---|---|
| **Socket V4**（推荐） | DG-Lab 4.0 App 的「Socket V4」控制入口。默认中继 `wss://trex.dungeon-lab.cn/v4`，1 控制方 : N App，扫码配对。**支持郊狼/负鼠/灵猫** |
| **Socket V3** | 官方旧版中继协议（`wss://ws.dungeon-lab.cn/`），兼容 3.x App 与自建中继（仅郊狼 3.0） |
| **蓝牙直连** | 郊狼 **3.0**（`47L121000`，B0/BF/B1）、郊狼 **2.0**（`D-LAB ESTIM01`，955A PWM 协议）、**负鼠 OVC**（`47L127000`，B0 振动/B3 强度/B2 屏显）、**灵猫 BMTR**（`47L124000`，D0 气压上报传感器）。**支持多台设备同时连接**（混合型号亦可），每台在对应类型控制页独立操作 |
| **本地中继** | 勾选后本机即作为局域网 WebSocket 中继服务器（V4: 9998 / V3: 9999），App 扫码直连电脑，无需任何外部服务器 |

**OSC 桥接**（可在界面 OSC 页配置）：

* 输出（设备 → 头像，默认 10 Hz 节流、值变化才发送）：
  `/avatar/parameters/DGLabStrengthA|B`（Int，当前强度）、`DGLabLimitA|B`（Int，通道上限）、
  `DGLabBattery`（Int，电量）、`DGLabConnected`（Bool）、`DGLabChannelOK_A|B`（Bool，通道状态）、
  `DGLabAction`（Int，App 按钮反馈 0-9，0.3 秒脉冲后自动归零）
* 输入（头像 → 设备）：`DGLabStrengthA|B` 设置强度（0-200 自动钳制）、`DGLabWaveA|B` 直接选波形（Int 索引：0=静默、1=持续、2+ 官方波形）、
  `DGLabWaveStepA|B` 波形步进（Int 非零：正=下一个、负=上一个）、`DGLabFire` 触发式开火（Bool：True 起爆 / False 停止）、
  `DGLabZapA|B` 瞬时脉冲（Bool）、`DGLabEmergency` 急停（Bool）
* 默认输出端口 **9000**（VRChat 监听）、监听端口 **9001**（VRChat 发送），与 `--osc=in:ip:out` 默认值一致

**多设备（V4）**：Socket V4 下多台设备同时接入，「控制」页按设备类型分为**三个独立控制页**（郊狼电刺激 / 负鼠振动 / 灵猫气压），每页有各自的设备选择、滑条与波形下拉（郊狼 24 组电刺激波形，负鼠 20 组官方振动波形），互不影响。急停作用于全部设备。

**波形控制（Socket V4 持续模式，播放截止时间自持）**：App 会在波形数据流断流时**将通道强度归零**（真机日志确认），且对**过大批次会卡死输出**（真机实测 50 帧批次 → channelAStatus=0）。客户端因此以 **10 帧（1 秒）小批次**供给：内部按「已入队帧的预计播完时刻（播放截止时间）」记账，截止前 0.6 秒补一批，平均恰为实时速率——队列既不排空也不积压（稳定在 0.6~1.6 秒深度），强度状态全程有效。**默认波形为「静默」**（零强度帧持续发送：会话保持、无输出），选择任意波形即开始输出；切换波形用 `im` 替换在途批次（不发 clear，强度不清零）；**急停** = 停止发送 + 全队列清空 + 全通道强度清零。

**强度控制**：每台设备都支持**加减**（+/- 按钮，步长可设）与**直接设置**（面板「A/B 直接设置」输入数值后点「应用直接设置」；OSC 用 `{in_strength_a/b}` 传绝对强度），两者走同一条绝对设定路径。Socket V4 下本程序**不自行维护强度与上限，一切以 App 实时回报为准**（`props.intensityA/B` 镜像显示、`slotState` 的 intensityMax 同步显示）——加减直接下发**相对增量** (t=3 AddIntensity)，直接设置则以 App 回报值为基准求差下发，由 App 自己执行其上限并回报新值。历史版本「强度被周期性错误归零、上限在 100/200/101 之间跳变」的真机日志定位结论：**App 的增量补丁是单边的**（强度补丁只带 props、舒适上限补丁只带 slotState），旧版合并逻辑把缺失的一半当成「清空」，导致本地镜像被反复清掉——显示归零但设备实际强度不变。现已修复为缺失即保留（附回归测试回放真机补丁序列）。App 端 comfortLimit/warmUp 特性会动态调整 intensityMax（100→101→102…），显示如实跟随。日志中出现 `… 强度回报为 0` 时为归零指令回显或 App 端自适应归零，可据此区分。安全上限（界面「最大强度上限」）用于钳制所有强度设定（含开火与直接设置）。

**一键开火**：支持两种方式。**定时爆发**（按钮「一键开火」/OSC `{in_fire}` 的 True→False 单次脉冲）：按「一键开火强度」爆发指定秒数后自动恢复（强度取界面「一键开火强度」，0 = 跟随「最大强度上限」，并始终受设备通道上限约束；该输入框**改动即时生效**，无需先点保存）；**触发式**（界面「按住持续开火」按住不放，或 OSC `{in_fire}` Bool 保持 True）：按下即起爆、放开即停止并恢复。两者在通道处于静默时会临时切「持续」波形以产生输出，**结束后都会切回原本选定的波形**（不会停留在持续波形）。触发式带 60 秒安全超时，防止参数卡在 True 导致长时间输出。放开时的强度恢复会先等 App 回报抬升后的值（最多约 1 秒）再按其求差；若 App 始终未回报（卡顿/丢帧），则按发射时实际施加的增量**反向撤销**，保证爆发强度不会残留。

**波形控制**：三种方式并存——**直接跳变**（控制页波形下拉框任选；OSC `{in_wave_a/b}` 传 Int 索引，`0=静默、1=持续、2+ 为官方波形`）；**步进加减**（下拉框两侧 ‹ / › 按钮逐个切换并循环；OSC `{in_wave_step_a/b}` 传非零 Int，正数=下一个、负数=上一个）；**归零**（强度清零并切回静默）。波形帧持续循环发送，切换用 `im` 替换在途批次，不中断强度会话。

**LED 与外设**：蓝牙直连的负鼠/灵猫支持 LED 颜色切换（0x50 指令，颜色面板 off/yellow/magenta/purple/blue/cyan/green，色值经硬件校准）；灵猫支持「翻转屏幕」（0x66 指令）；负鼠 11 个物理按键（SEL_1/SEL_2/HOME/方向/ABGD）可在界面上绑定动作：无/一键开火/A 脉冲/B 脉冲/急停（按键经 D0 上报，绑定点火后物理按键即可一键开火）。

**归零按钮**：通道旁的「归零」把该通道**强度清零并同时切回「静默」波形**（波形下拉框同步显示静默）——立即无输出，且会话保持、再次选择波形即恢复。经蓝牙连接时强度采用「目标值重发」机制——每 100ms 重发绝对设定直到设备回报确认，丢包/漏应答自愈，物理旋钮改动也会被识别并同步。

**实时输出图**：郊狼/负鼠页面显示最近 5 秒的实际输出强度时序条形图（上排 A、下排 B，右端为当前），**每 100ms 刷新一次，与 App 显示节奏一致**。数据为按播放时刻投影的波形采样（每 100ms 四段，批次间按播放截止时间无缝衔接），Socket 与蓝牙模式均可用。频率图已移除。

**OSC 独立参数**：每台设备独立暴露一组头像参数，按类型分组、同类型第 2 台起自动加序号（可在 config `osc.device_prefixes` 自定义前缀）：

| 设备 | 参数 |
|---|---|
| 郊狼 #1 | `DGLabStrengthA/B`、`DGLabLimitA/B`、`DGLabBattery`、`DGLabConnected`、`DGLabChannelOK_A/B` |
| 郊狼 #2 | `DGLab2StrengthA/B`、`DGLab2LimitA/B`、… |
| 负鼠 #1 | `DGLabOvcStrengthA/B`、`DGLabOvcLimitA/B`、`DGLabOvcBattery`、… |
| 灵猫 #1 | `DGLabBmtrPressure` (Float, kPa)、`DGLabBmtrEdgeState` (Int 0-4)、`DGLabBmtrBattery`、… |
| 负鼠输入 | `DGLabOvcInStrengthA/B`、`DGLabOvcInWaveA/B`（含 `…WaveStepA/B` 步进）、`DGLabOvcInZapA/B`、`DGLabOvcInFire`（触发式）（独立作用于首个负鼠设备） |
| 全局 | `{前缀}Action`（App/设备按键反馈 0-9 脉冲）、`{in_emergency}` 急停（全部设备） |

**输入映射按设备类型分组**：郊狼输入参数（`{in_strength_a/b}` 等，默认 `DGLabStrengthA/B`…）作用于**首个郊狼**，负鼠输入参数（默认 `DGLabOvcInStrengthA/B`…）作用于**首个负鼠**——同名输出参数不会造成回环。

OSC 页实时显示当前映射，映射算法与桥接共用（`device_osc_names`）。

**可视化**：郊狼/负鼠控制页内嵌**波形预览图**（每通道一排：灰色=频率 10-1000、蓝色=强度 0-100，随波形选择即时刷新，经内存流加载无缓存问题）；灵猫页内嵌**气压折线图**（最近 60 秒滚动曲线，**0-60 kPa 固定量程与 App 一致**，多台设备多色区分）。图表配色随明暗主题自动切换。

**主题**：标题栏提供「深色模式/浅色模式」手动切换按钮，独立于系统主题强制生效（元素主题 + 页面背景 + 标题栏三重设置），选择持久化，图表配色同步切换。

**设备管理与日志**：蓝牙设备以**设备为单位**管理——连接成功后自动记录（地址/类型/名称），「连接」页可从「已保存设备」下拉框选择后一键「重连」或「删除记录」。**自动重连**只针对本次运行中**意外掉线**的已连接设备（经 bleak 断连回调检测，掉线即清除僵尸会话并每 5 秒重试，日志记录「连接意外断开 (将自动重连)」）；手动断开、以及从未在本程序连接过的设备（例如正在被手机 App 占用的设备）不会被后台强行抢占。所有日志**同步写入 exe 同目录的 `dglab_osc.log`**（带时间戳），Socket 模式默认记录收发的协议帧（`log_frames` 可关，ping/心跳已过滤）。Socket 模式下 App 不上报设备电量（已确认），电量显示为 "--"；蓝牙模式电量正常。

**灵猫气压诊断**：灵猫 (BMTR) 是**纯传感器设备**——程序不向其下发任何波形/强度指令（Socket 与蓝牙链路均已在路由层屏蔽，输出类指令永远不会落到灵猫槽位）。历史上「收不到气压」的真机定位结论：50 使能指令与写入模式均无误（A/B 实测：停止指令后数据流归零，无响应写/带响应写启动均恢复 10Hz），**真正根因是 bleak 同步调用通知回调，而回调曾是 `async def`，协程从未被执行**——通知帧一直在到达、只是从未被处理，现已改为同步回调。每秒向日志输出一行 `D0接收统计(1s)`（如 `0000150B:10帧 最新… 气压 0.00 kPa`，完全无通知时明确记录）。气压为 150B 上 17 字节 `0xD0` 帧（100ms 一帧，第 8、9 字节 int16 LE ÷100 = kPa）；连接时设备先回 6 字节 `0x53` 问候帧、每次使能回 4 字节 `0x51` 应答帧，均可在日志看到。使能指令每秒经 150A 重发保活；厂商 FF01 通道写入会导致设备重启，仅在探针脚本中按需启用。现场排查可用 `_research/bmtr_probe.py`（转储 GATT、订阅全部通知、`--wwr/--ff01/--raw` 变体实验）。

**其它**：官方波形 + 「持续」波形（按设备类型自适应）、强度安全上限、一键急停（全部设备）、灵猫气压清零、配置持久化（`config.json`）。

### 本地中继（本机作为服务器）

连接页勾选「使用本地中继」后再点连接：客户端在 `0.0.0.0:9998`（V4）/ `9999`（V3）启动内置中继服务器，自身回环接入，二维码直接给出 `ws://<局域网IP>:端口/...`。手机 App 与电脑同一局域网即可扫码直连，延迟更低且不依赖外部服务器。中继协议按官方参考实现 `dglab-websocket-server`（v3-server.ts / v4-server.ts）1:1 移植：hello/clientId 分配、tid 接入配对、message 路由、心跳、空闲超时与断开通知（含关闭码 4000/4001/4002 与 V3 错误码 401/400/402/403/404/406、波形分包定时下发）均与官方一致。

---

## 安装与运行

需要 Windows 10 1809+ / Windows 11，Python 3.9+（本项目在 3.12 / 3.14 下验证过）。

```bat
cd E:\DGOSC
py -3.12 -m venv .venv          （已有 .venv 可跳过）
.venv\Scripts\pip install -r requirements.txt
.venv\Scripts\python main.py
```

WinUI 3 部分使用 [`win32more`](https://pypi.org/project/win32more/)（纯 Python 的 WinRT/WinUI 3 绑定），wheel 自带 Windows App SDK 自包含 DLL，**一般无需额外安装运行时**；若启动报 `RuntimeNotFoundError`，安装一次 [Windows App Runtime](https://learn.microsoft.com/windows/apps/windows-app-sdk/downloads) 即可。

### VRChat 侧设置

1. 游戏内打开动作菜单 → **OSC → Enabled** 开启 OSC。
2. 在你的头像中添加上述参数（Int/Bool，名称与 OSC 页配置一致）。
3. 若 9000/9001 端口冲突（如 VRCOSC 已占用），可在 OSC 页修改端口，或用启动项 `--osc=<in>:<ip>:<out>` 改 VRChat 端口。

### 设备连接

* **V4 / V3**：选择连接方式 → 填中继地址 → 「连接并生成配对二维码」→ 打开 DG-Lab App（4.0 用 Socket V4 入口；3.x 用 Socket 入口）扫码。二维码同时展示在界面与日志。
* **蓝牙**：切换到「蓝牙直连」→「扫描设备」→ 选中设备 →「连接选中设备」。注意扫描/连接前设备不能被手机 App 占用。

---

## 项目结构

```
main.py                  入口：启动引擎线程 + WinUI 3 消息泵
app.py                   引擎：Config 持久化、后台 asyncio 循环、统一命令层
dglab/
  socket_v4.py           Socket V4 客户端（hello/ping、req-resp RPC、devices/slots 事件）
  socket_v3.py           Socket V3 客户端（bind 配对、strength/clientMsg/clear、反馈解析）
  ble.py                 BLE 直连（V3: B0/BF/B1 + 电池；V2: PWM_AB2/A34/B34）
  waves.py               波形工具（频率分段映射、hex 帧解析/构造、X/Y/Z 算法）
  official_waveforms.py  DG-Lab 官方 24 组波形数据（移植自官方 dglab-kit-python，GPL-3.0）
  state.py               EngineState/Slot 状态模型与事件总线
vrc/osc_bridge.py        VRChat OSC 桥（python-osc；参数输出节流 + 输入映射）
ui/main_window.py        WinUI 3 主窗口（XAML 加载、二维码、滑条、OSC 配置、日志）
tests/test_protocol.py   协议单元测试
```

线程模型：引擎在后台线程跑 asyncio 事件循环；UI 通过 `asyncio.run_coroutine_threadsafe` 下发命令，协议层事件经队列由 UI 线程的 `DispatcherQueueTimer` 消费（WinRT 委托不能在非 UI 线程创建）。

## 协议实现要点（均依据官方开源文档/源码）

* **Socket V4**：连接后服务器下发 `{"type":"hello","clientId":…}` 作为控制方 ID；App 接入触发 `client_attached`；应用层消息封装在 `{"type":"message","clientId":…,"data":…}` 内，`t=req/resp/ev`；操作 `device.op`（`t=0` AppendPulseData、`t=3` AddIntensity、`t=4` SetTempIntensity、`t=7` SetIntensity 仅限清零）、`device.op.clear`、`devices.get`；设备状态经 `devices.snapshot/patch`、`slots.patch`（按 slotId 深合并）同步；控制器每 2 秒 ping、3 次无 pong 判定掉线；二维码 `https://dungeon-lab.cn/s/?v=1&action=socket&url=<enc>`。
* **Socket V3**：包络 `{"type","clientId","targetId","message"}`；服务器连接即分配 ID（`message:"targetId"`），配对成功 `message:"200"`；强度 `strength-通道+模式+数值`（反馈 `strength-A+B+Amax+Bmax`，同时兼容 `-` 分隔）；波形 `clientMsg` 携带 8 字节 hex 帧数组（100ms/帧）；错误码 200/209/400/401/402/404/405。
* **BLE V3**：20 字节 `B0` 帧（序号+双通道强度解析方式+A/B 波形各 4×2 字节）以 100ms 周期写入；`BF` 设软限制与平衡参数（重连后必须重发）；`B1` 通知回读实际强度并用于强度修改的流控（一次一个未确认修改）；频率字节 10-240（逻辑 10-1000 分段映射）。
* **BLE V2**：`PWM_AB2`（A 21-11bit、B 10-0bit，设备值=App 值×7）；`PWM_B34`=A 通道波形、`PWM_A34`=B 通道波形（X 5bit / Y 10bit / Z 5bit，X=sqrt(F/1000)×15）；100ms 刷新。
* **蓝牙驱动行为**：
* 连接后自动以「**持续**」波形启动双通道——协议规定输出 = 通道强度 × 波形强度缩放，无波形时波形字节全 0 会导致**任何强度都没有输出**（与 App 行为一致）；发送其他波形即切换，点「清除」恢复静默。
* 强度修改采用官方 B0(0b11 绝对设定)+B1 应答流控，并带 **1 秒应答超时**防死锁；输出循环对单次蓝牙写入失败**容错继续**（连续 9 秒失败才停止），瞬时无线干扰不会中断波形。
* **BLE 灵猫 BMTR**：`50`（17B）开气压上报（**仅写 150A**——实测写厂商特征 FF01 会导致设备重启关机）；通知 `D0` 中气压 = 字节 8-9 int16 LE / 100（kPa）；`66` 气压清零；每秒重发使能保活。
* **本地中继 V4/V3**：`dglab/relay_v4.py`、`dglab/relay_v3.py`，移植自官方 `dglab-websocket-server`（Bun/TS → asyncio/websockets）。

主要参考（协议部分禁止商用，授权见官方仓库）：

* 官方协议仓库 [dungeonlab-open/dglab-bluetooth-protocol](https://github.com/dungeonlab-open/dglab-bluetooth-protocol)、[dglab-websocket-simple](https://github.com/dungeonlab-open/dglab-websocket-simple)
* 官方 SDK [dglab-kit](https://github.com/dungeonlab-open/dglab-kit)、[dglab-kit-python](https://github.com/dungeonlab-open/dglab-kit-python)（V4/V3 线协议与波形数据的权威实现）
* [VRChat OSC 文档](https://docs.vrchat.com/docs/osc-overview)、[头像参数](https://docs.vrchat.com/docs/osc-avatar-parameters)
* [win32more](https://pypi.org/project/win32more/)（WinUI 3 Python 绑定）、[python-osc](https://pypi.org/project/python-osc/)、[bleak](https://pypi.org/project/bleak/)

## 测试

```bat
.venv\Scripts\python -m unittest discover -s tests
```

覆盖：波形帧编解码与官方波形校验（郊狼 24 组 + 负鼠 20 组）、频率映射、V3 反馈消息解析（`+`/`-` 分隔）、V4 设备快照/深合并/hello 流程、B0/B3/B2/BF 与官方文档示例逐字节比对、PWM/XYZ 打包、负鼠 B0/B3/B2 与灵猫 50/66/D0 气压解析、OSC 配置与钳制逻辑，以及 **V4/V3 本地中继全链路回环**（中继启动 → 客户端接入 → 模拟 App 配对 → 强度/波形/事件双向路由 → 断开通知）。

## 打包为 exe

已附 [DGLabOSC.spec](DGLabOSC.spec)（PyInstaller onedir 模式）：

```bat
.venv\Scripts\pip install pyinstaller
.venv\Scripts\python -m PyInstaller DGLabOSC.spec --noconfirm
```

产物在 `dist\DGLabOSC\`：`DGLabOSC.exe` + `_internal\` 依赖目录（约 60 MB）。分发时把整个文件夹拷走即可。

* 打包要点：win32more 在运行期按类名动态导入投影模块，spec 里已 `collect_submodules` 收集 `win32more.Microsoft`、`Windows.Foundation/Graphics/UI`、`winrt`、`bleak` 等；`win32more/dll/x64` 的 Bootstrap DLL 与 `winui3/app.xaml` 按原包路径打进 `_internal`。
* 运行时依赖：目标机器需安装 [Windows App Runtime](https://learn.microsoft.com/windows/apps/windows-app-sdk/downloads)（win32more 为框架依赖部署，Bootstrap DLL 负责引导；未装会弹官方提示或写入 `%TEMP%\dglab_osc_crash.log`）。
* `DGLabOSC.exe --selftest`：启动窗口 3 秒后自动关闭，把检查结果写入 `%TEMP%\dglab_osc_selftest.json`（退出码 0 表示通过），用于验证打包产物。
* 配置文件 `config.json` 生成在 exe 同目录；崩溃日志在 `%TEMP%\dglab_osc_crash.log`。
* 如需单文件 exe（onefile），把 spec 中 `COLLECT` 段删掉并给 `EXE` 传 `a.binaries, a.datas` 即可，但启动会变慢且自解压目录可能与 DLL 引导冲突，不推荐。

## 安全提示

这是电刺激设备控制工具。请从低强度开始，**始终设置安全上限**（界面「最大强度上限」，控制强度会被钳制到该值），急停按钮可随时清零强度并停止波形。Wave/X/Y/Z 参数不当可能引起刺痛（脉冲宽度 Z>20 时更明显）。请自行承担使用风险，并遵守官方协议的非商业使用条款。

## 许可

本项目以 **GNU General Public License v3.0**（GPL-3.0）发布，全文见 [LICENSE](LICENSE)。

Copyright (C) 2026 Kelier Andes

选择 GPL-3.0 而非 MIT 等宽松许可，是因为本项目包含移植自同样以 GPL-3.0 授权的官方代码——GPL-3.0 要求衍生作品整体以 GPL-3.0 分发：

| 本项目文件 | 移植来源 | 上游许可 |
|---|---|---|
| `dglab/official_waveforms.py`、`dglab/official_waveforms_ovc.py` | [dglab-kit-python](https://github.com/dungeonlab-open/dglab-kit-python) | GPL-3.0 |
| `dglab/relay_v3.py`、`dglab/relay_v4.py` | [dglab-websocket-server](https://github.com/dungeonlab-open/dglab-websocket-server) v3-server.ts / v4-server.ts | GPL-3.0 |

以 submodule 引入的两个参考仓库为**独立作品**，各自适用其上游许可，不受本项目许可影响：`dglab-websocket-server` 为 GPL-3.0；`dglab-websocket-simple` 上游未附许可证文件，仅可作协议阅读参考。

DG-Lab 官方协议文档另有「协议部分禁止商用」条款，商用前请自行确认并遵守。
