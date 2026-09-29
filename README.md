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

> **开发说明**：本仓库源码不含注释——协议内幕、实现细节、平台陷阱、调试与打包记录等开发文档集中于 `DEVELOPMENT.md`，仅本地维护，不随仓库分发。

## 功能

| 连接方式 | 说明 |
|---|---|
| **Socket V4**（推荐） | DG-Lab 4.0 App 的「Socket V4」控制入口。默认中继 `wss://trex.dungeon-lab.cn/v4`，1 控制方 : N App，扫码配对。**支持郊狼 / 负鼠 / 灵猫** |
| **Socket V3** | 官方旧版中继协议（`wss://ws.dungeon-lab.cn/`），兼容 3.x App 与自建中继（仅郊狼 3.0） |
| **蓝牙直连** | 郊狼 **3.0**（`47L121000`）、郊狼 **2.0**（`D-LAB ESTIM01`）、**负鼠 OVC**（`47L127000`）、**灵猫 BMTR**（`47L124000`）。**支持多台设备同时连接**（混合型号亦可），每台在「控制」页的独立设备卡片中操作 |
| **本地中继** | 勾选后本机即作为局域网 WebSocket 中继服务器（V4: 9998 / V3: 9999），App 扫码直连电脑，无需任何外部服务器，延迟更低 |

### 设备控制

* **多设备**：Socket V4 下多台设备同时接入，「控制」页为**每台设备一张独立控制卡片**（郊狼电刺激 / 负鼠振动 / 灵猫气压各按型号装配），强度加减、波形选择、开火与图表互不影响。急停作用于全部设备。
* **强度控制**：每台设备都支持**加减**（+/- 按钮，步长可设）与**直接设置**（面板「A/B 直接设置」输入数值后点「应用直接设置」）。Socket V4 下强度与上限**以 App 实时回报为准**，程序不自行维护；控制页**每台设备卡片**内的「最大强度上限」用于钳制该设备的所有强度设定（含开火与直接设置），未单独设置时回退全局默认值。
* **波形控制**：三种方式并存——**直接跳变**（控制页波形下拉框任选，郊狼 24 组电刺激 / 负鼠 20 组官方振动波形）、**步进加减**（下拉框两侧 ‹ / › 按钮逐个切换并循环）、**归零**。默认波形为「静默」，选择任意波形即开始输出，切换波形不中断强度会话。
* **归零按钮**：通道旁的「归零」把该通道**强度清零并同时切回「静默」波形**——立即无输出，且会话保持，再次选择波形即恢复。
* **一键开火**：两种方式。**定时爆发**（按钮「一键开火」，或 OSC 触发式参数的 True→False 单次脉冲）按「一键开火强度」爆发指定秒数后自动恢复；**触发式**（界面「按住持续开火」按住不放，或 OSC 参数保持 True）按下即起爆、放开即停止并恢复。两者结束后都会切回原本选定的波形；触发式带 60 秒安全超时。设备卡片内「开火强度」改动即时生效，0 = 跟随本卡「最大强度上限」。
* **LED 与外设**：蓝牙直连的负鼠 / 灵猫支持 LED 颜色切换（off / yellow / magenta / purple / blue / cyan / green，色值经硬件校准）；灵猫支持「翻转屏幕」；负鼠 11 个物理按键可绑定动作：无 / 一键开火 / A 脉冲 / B 脉冲 / 急停。
* **实时输出图**：郊狼 / 负鼠页面显示最近 5 秒的实际输出强度时序条形图（上排 A、下排 B，右端为当前），每 100ms 刷新。Socket 与蓝牙模式均可用。

### OSC 桥接

可在界面「设置」页配置（修改后重新开关联动页的桥接生效）。默认输出端口 **9000**（VRChat 监听）、监听端口 **9001**（VRChat 发送）。

**输出**（设备 → 头像，默认 10 Hz 节流、值变化才发送）：

| 参数 | 类型 | 含义 |
|---|---|---|
| `DGLabStrengthA` / `DGLabStrengthB` | Int | 当前强度 |
| `DGLabLimitA` / `DGLabLimitB` | Int | 通道上限 |
| `DGLabBattery` | Int | 电量（蓝牙模式；Socket 模式 App 不上报，显示 `--`） |
| `DGLabConnected` | Bool | 连接状态 |
| `DGLabChannelOK_A` / `DGLabChannelOK_B` | Bool | 通道状态 |
| `DGLabAction` | Int | App 按钮反馈 0-9（0.3 秒脉冲后自动归零） |

**输入**（头像 → 设备）：

| 参数 | 类型 | 含义 |
|---|---|---|
| `DGLabStrengthA` / `DGLabStrengthB` | Int | 设置强度（0-200 自动钳制） |
| `DGLabWaveA` / `DGLabWaveB` | Int | 直接选波形（`0`=静默、`1`=持续、`2+` 官方波形） |
| `DGLabWaveStepA` / `DGLabWaveStepB` | Int | 波形步进（非零触发：正 = 下一个、负 = 上一个） |
| `DGLabFire` | Bool | 一键开火（True 起爆 / False 停止） |
| `DGLabZapA` / `DGLabZapB` | Bool | 瞬时脉冲 |
| `DGLabEmergency` | Bool | 急停（全部设备） |

**多设备参数分组** —— 每台设备独立暴露一组头像参数，按类型分组、同类型第 2 台起自动加序号（前缀可在 config 的 `osc.device_prefixes` 自定义）：

| 设备 | 参数 |
|---|---|
| 郊狼 #1 | `DGLabStrengthA/B`、`DGLabLimitA/B`、`DGLabBattery`、`DGLabConnected`、`DGLabChannelOK_A/B` |
| 郊狼 #2 | `DGLab2StrengthA/B`、`DGLab2LimitA/B`、… |
| 负鼠 #1 | `DGLabOvcStrengthA/B`、`DGLabOvcLimitA/B`、`DGLabOvcBattery`、… |
| 灵猫 #1 | `DGLabBmtrPressure`（Float, kPa）、`DGLabBmtrEdgeState`（Int 0-4）、`DGLabBmtrBattery`、… |
| 负鼠输入 | `DGLabOvcInStrengthA/B`、`DGLabOvcInWaveA/B`（含 `…WaveStepA/B` 步进）、`DGLabOvcInZapA/B`、`DGLabOvcInFire`（独立作用于首个负鼠设备） |
| 全局 | `{前缀}Action`（按键反馈 0-9 脉冲）、`{in_emergency}` 急停（全部设备） |

输入映射按设备类型分组：郊狼输入参数作用于**首个郊狼**，负鼠输入参数作用于**首个负鼠**，同名输出参数不会造成回环。「联动」页实时显示当前映射（输入映射编辑也在该页，OSC 地址在「设置」页）。

### 界面

* **可视化**：郊狼 / 负鼠设备卡片内嵌**柱状实时波形图**（A / B 各一栏，柱高 = 输出强度 0-100，最右侧为最新采样）；灵猫卡片内嵌**气压折线图**（最近 60 秒滚动曲线，0-60 kPa 固定量程）。界面为 Fluent Studio 风格：左侧导航分为「概览 / 连接 / 控制 / 联动 / 日志 / 设置」六页。
* **主题**：侧边栏底部「切换主题」在深色 / 浅色间手动切换，独立于系统主题强制生效，选择持久化。
* **VRChat OSC 探测**：概览页的探测卡片通过监听端口是否收到 VRChat 数据判定桥接连接状态（已连接 / 无数据 / 已停止），替换了旧的日志计数卡片。
* **连接页**：Socket V4 / V3 卡片只保留连接、断开与配对二维码（生成后自动展开）；中继服务器地址、是否使用本地中继与端口统一在「设置」页配置。
* **急停**：作用于全部输出设备，与「归零」语义一致——强度清零并把波形重置为静默（会话保持），随后任选波形即可恢复输出；急停同时取消尚未结束的「按住持续开火」。
* **设备管理**：蓝牙设备以设备为单位管理，连接成功后自动记录（地址 / 类型 / 名称），可从「已保存设备」下拉框一键「重连」或「删除记录」。**自动重连**只针对本次运行中意外掉线的已连接设备（每 5 秒重试）；手动断开、以及从未在本程序连接过的设备不会被后台强行抢占。

---

## 安装与运行

### 方式一：直接使用打包好的 exe（推荐）

仓库内 `dist\DGLabOSC\` 已包含构建产物，**整个文件夹拷到任意位置**即可运行：

```
dist\DGLabOSC\
  DGLabOSC.exe      主程序
  _internal\        依赖目录（必须与 exe 放在一起）
```

双击 `DGLabOSC.exe` 启动。目标机器需安装一次 [Windows App Runtime](https://learn.microsoft.com/windows/apps/windows-app-sdk/downloads)（未装会弹官方提示）。配置文件 `config.json` 生成在 exe 同目录，崩溃日志在 `%TEMP%\dglab_osc_crash.log`。

### 方式二：从源码运行

需要 Windows 10 1809+ / Windows 11，Python 3.9+（本项目在 3.12 / 3.14 下验证过）。

```bat
git clone https://github.com/KelierAndes/DG-LAB-X-VRChat-OSC.git
cd DG-LAB-X-VRChat-OSC
py -3.12 -m venv .venv
.venv\Scripts\pip install -r requirements.txt
.venv\Scripts\python main.py
```

WinUI 3 部分使用 [`win32more`](https://pypi.org/project/win32more/)（纯 Python 的 WinRT / WinUI 3 绑定），wheel 自带 Windows App SDK 自包含 DLL，一般无需额外安装运行时；若启动报 `RuntimeNotFoundError`，安装一次 [Windows App Runtime](https://learn.microsoft.com/windows/apps/windows-app-sdk/downloads) 即可。

### VRChat 侧设置

1. 游戏内打开动作菜单 → **OSC → Enabled** 开启 OSC。
2. 在你的头像中添加上述参数（Int / Bool，名称与「联动」页配置一致）。
3. 若 9000 / 9001 端口冲突（如 VRCOSC 已占用），可在「设置」页修改端口，或用启动项 `--osc=<in>:<ip>:<out>` 改 VRChat 端口。

### 设备连接

* **V4 / V3**：选择连接方式 → 填中继地址 → 「连接并生成配对二维码」→ 打开 DG-Lab App（4.0 用 Socket V4 入口；3.x 用 Socket 入口）扫码。二维码同时展示在界面与日志。
* **蓝牙**：切换到「蓝牙直连」→「扫描设备」→ 选中设备 →「连接选中设备」。注意扫描 / 连接前设备不能被手机 App 占用。
* **本地中继**：连接页勾选「使用本地中继」后再点连接，客户端会在 `0.0.0.0:9998`（V4）/ `9999`（V3）启动内置中继服务器，二维码直接给出 `ws://<局域网IP>:端口/...`。手机 App 与电脑处于同一局域网即可扫码直连，不依赖外部服务器。

---

## 安全提示

这是电刺激设备控制工具。请从低强度开始，**始终设置安全上限**（控制页每台设备卡片中的「最大强度上限」，该设备的强度会被钳制到该值），急停按钮可随时清零全部强度并重置波形。波形参数不当可能引起刺痛。请自行承担使用风险，并遵守官方协议的非商业使用条款。

## 许可

本项目以 **GNU General Public License v3.0**（GPL-3.0）发布，全文见 [LICENSE](LICENSE)。

Copyright (C) 2026 Kelier Andes

本项目包含移植自同样以 GPL-3.0 授权的官方代码（波形数据来自 [dglab-kit-python](https://github.com/dungeonlab-open/dglab-kit-python)，本地中继移植自 [dglab-websocket-server](https://github.com/dungeonlab-open/dglab-websocket-server)），GPL-3.0 要求衍生作品整体以 GPL-3.0 分发。

DG-Lab 官方协议文档另有「协议部分禁止商用」条款，商用前请自行确认并遵守。