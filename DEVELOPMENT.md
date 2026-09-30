# 开发日志（DEVELOPMENT）

> 本文档记录实现细节、协议内幕、调试过程与打包方式，**仅面向开发/维护**。
> 已加入 `.gitignore`，不随仓库分发。面向用户的说明见 [README.md](README.md)。

---

## 项目结构

```
main.py                  入口：启动引擎线程 + WinUI 3 消息泵（--selftest 自检）
app.py                   引擎：Config 持久化、后台 asyncio 循环、统一命令层、强度参数公开 API
plugins.py               模块宿主：PluginManager / ModuleBase / ModuleContext（发现/加载/装卸/启停）
modules/                 联动模块目录（实时装卸，osc_bridge 为内置模块，strength_logger 为示例）
  osc_bridge/bridge.py     VRChat OSC 桥（python-osc；参数输出节流 + 输入映射 + 输入值记录）
  osc_bridge/plugin.py     OSC 模块入口（META + OscModule，启动时重建桥接使设置立即生效）
  strength_logger/plugin.py  示例模块：强度日志（默认不启用，作为开发模板）
  alice_cradle/server.py     游戏数据 HTTP 服务（POST /data 收命名数值、GET /data 回传输出表、GET /status 调试）
  alice_cradle/plugin.py     爱丽丝的摇篮模块入口（META + AliceCradleModule，默认不自启）
EXTENSIONS.md            扩展开发文档（模块 SDK：生命周期、ModuleContext API、强度参数、事件、打包）
dglab/
  socket_v4.py           Socket V4 客户端（hello/ping、req-resp RPC、devices/slots 事件）
  socket_v3.py           Socket V3 客户端（bind 配对、strength/clientMsg/clear、反馈解析）
  ble.py                 BLE 直连（V3: B0/BF/B1 + 电池；V2: PWM_AB2/A34/B34）
  waves.py               波形工具（频率分段映射、hex 帧解析/构造、X/Y/Z 算法）
  official_waveforms.py  DG-Lab 官方 24 组波形数据（移植自官方 dglab-kit-python，GPL-3.0）
  official_waveforms_ovc.py  负鼠官方 20 组振动波形数据（同上，GPL-3.0）
  relay_v3.py            本地中继 V3（移植自官方 v3-server.ts）
  relay_v4.py            本地中继 V4（移植自官方 v4-server.ts）
  state.py               EngineState/Slot 状态模型与事件总线
xaml/                    页面 XAML 骨架（MainWindow + 七个 Page，PyInstaller 打进 _MEIPASS/xaml）
ui/
  shell.py               主窗口：NavigationView 壳层、主题、ui_queue 派发、页面缓存
  live.py                实时数据层：引擎状态 → 视图模型、日志缓冲、波形/气压表数据
  theme.py               深浅色两套显式色值令牌（动态行取色，页面随主题重建）
  widgets.py             控件工厂（卡片/标签/表格行/波形柱图/折线图/输入框等）
  nav.py                 页面间文字跳转链接
  paths.py               XAML 路径解析（源码态 / _MEIPASS）
  dashboard_page.py      概览：统计卡（设备/输出设备/输入输出链路计数）、输入通道、输出通道(探活)、输入/输出数据值卡、设备速览
  connect_page.py        连接：V4/V3 中继卡片（二维码自动展开+断开）+ 蓝牙扫描/已保存设备；中继参数在设置页
  control_page.py        控制：每设备一张卡（强度/波形/开火/绑定/气压曲线）+ 参数条
  link_page.py           联动：按联动模块分类的大卡片，模块内统一模板（输出映射表/输入映射表/模块设置），条目由 META["config"] 声明自动渲染
  log_page.py            日志：等级筛选 + 搜索 + 实时列表（最新在上）
  settings_page.py       设置：外观/连接(中继地址/本地中继)/配置文件(保存/载入/导出)/日志/关于（OSC 设置在联动页）
modules_page.py        模块：联动模块列表，实时安装/卸载/启动/停止，扫描新模块
tests/                   协议与功能回归测试
build_exe.py             打包辅助脚本
DGStudio.spec            PyInstaller 打包配置（产物 DGStudio/dist，含 collect_submodules("modules")）

mods/AliceInCradleLink/  爱丽丝的摇篮 Unity 模组（BepInEx 5 / netstandard2.1，`dotnet build -t:Deploy` 部署到游戏）
  Plugin.cs                  入口：捕获主线程同步上下文 + 看门狗自愈重建 runner
  LinkRunner.cs              每帧逻辑宿主（Update 采样数值、OnGUI 画面板）
  DataClient.cs              纯数据发送端客户端（POST /data 上报 + GET /data 轮询回传，后台线程）
  VitalSampler.cs            HP/MP/EP 采样与差分信号（Hurt/Heal/MpLost/MpGain/Orgasming），不做强度换算
  VitalReader.cs             反射读取 protected 的 hp/maxhp/mp/maxmp
  LinkConfig.cs              BepInEx 配置项（地址、上报/轮询间隔、信号口径、状态面板）
  OverlayUi.cs               F9 状态面板（IMGUI + 系统中文字体，显示回传字段网格）
_vendor/                 模组编译依赖（BepInEx.dll / 0Harmony.dll 与官方发行 zip）
_tools/asmprobe/         游戏程序集元数据探针（核对类型链/字段可见性/API 是否存在）

dglab-websocket-server/  [submodule] 官方 V4/V3 中继参考实现（Bun/TS，GPL-3.0）
dglab-websocket-simple/  [submodule] 官方协议文档与波形示例（JS）
```

两个 submodule 只是协议对照资料，**不参与构建与运行**——`dglab/relay_v3.py`、`relay_v4.py` 是照其 TS 实现移植后的独立 Python 代码。

线程模型：引擎在后台线程跑 asyncio 事件循环；UI 通过 `asyncio.run_coroutine_threadsafe` 下发命令，协议层事件经队列由 UI 线程的 `DispatcherQueueTimer` 消费（WinRT 委托不能在非 UI 线程创建）。

---

## 界面架构（Fluent Studio 重构）

界面按 `winui3-app/DgoscStudioPy` 原型重构（原单窗口 Pivot 四页 → NavigationView 六页）：

* **数据流**：引擎 `events` 的 `state`/`log`/`saved_devices` 事件在引擎线程触发 → `shell.state`（引擎发的副本，直接换引用）/ `shell.logs`（LogBuffer 环形缓冲）→ 各页 `tick()`（壳层 100 ms 定时器回调）按节流频率拉取刷新。跨线程动作统一走 `shell.ui_queue`（如 BLE 扫描完成回调）。
* **页面 = XamlClass**：每个 Page 继承 `win32more.winui3.XamlClass`，`LoadComponentFromFile(xaml("XxxPage.xaml"))` 加载骨架（`x:Name` 挂到 self），动态内容用 `widgets.py` 工厂按实时数据装配。壳层 `MainWindow` 同理继承 `XamlClass, Window`。
* **主题**：`theme.py` 维护深浅两套显式 RGBA 令牌；动态行构建时取色。切换主题 = 换令牌 + `RootGrid.RequestedTheme` + **重建全部已构造页面**（缓存页残留旧配色）。`ui.dark` 持久化在 config。
* **刷新节流**：控制页数值 0.2 s、柱状波形图 0.25 s、气压折线图 0.5 s、气压采样 0.08 s；概览/连接 0.4~0.5 s；日志页 0.25 s 且只在 `logs.version` 变化时重排。控制页设备集变化才整卡重建（`CardView` 持引用做部分更新：标签/强度条/波形下拉同步，`_updating` 守卫防回环）。
* **图表全矢量**：柱状波形（`widgets.wave_bars`，`engine.wave_history(sid).window(5.0)` 采样降采样到 44 柱）与气压折线（`widgets.line_chart`）都是 Border/Polyline 动态元素，不再走 PIL PNG。二维码仍用 qrcode+PIL → `InMemoryRandomAccessStream` → `BitmapImage` 管线。
* **连接页三通道卡片**：V4 / V3 / 蓝牙各一张；实际后端互斥（连接即断开旧后端），状态胶囊如实显示「未启用 / 等待扫码配对 / 已连接」。配对二维码区块挂在对应激活通道的卡片里。
* **日志分级**：引擎日志是纯文本，`live.classify_log` 按关键词近似分级（失败/错误→error，重试/超时/限幅→warn，数据帧→debug）。

---

## 协议实现要点（均依据官方开源文档/源码）

* **Socket V4**：连接后服务器下发 `{"type":"hello","clientId":…}` 作为控制方 ID；App 接入触发 `client_attached`；应用层消息封装在 `{"type":"message","clientId":…,"data":…}` 内，`t=req/resp/ev`；操作 `device.op`（`t=0` AppendPulseData、`t=3` AddIntensity、`t=4` SetTempIntensity、`t=7` SetIntensity 仅限清零）、`device.op.clear`、`devices.get`；设备状态经 `devices.snapshot/patch`、`slots.patch`（按 slotId 深合并）同步；控制器每 2 秒 ping、3 次无 pong 判定掉线；二维码 `https://dungeon-lab.cn/s/?v=1&action=socket&url=<enc>`。
* **Socket V3**：包络 `{"type","clientId","targetId","message"}`；服务器连接即分配 ID（`message:"targetId"`），配对成功 `message:"200"`；强度 `strength-通道+模式+数值`（反馈 `strength-A+B+Amax+Bmax`，同时兼容 `-` 分隔）；波形 `clientMsg` 携带 8 字节 hex 帧数组（100ms/帧）；错误码 200/209/400/401/402/404/405。
* **BLE V3**：20 字节 `B0` 帧（序号+双通道强度解析方式+A/B 波形各 4×2 字节）以 100ms 周期写入；`BF` 设软限制与平衡参数（重连后必须重发）；`B1` 通知回读实际强度并用于强度修改的流控（一次一个未确认修改）；频率字节 10-240（逻辑 10-1000 分段映射）。
* **BLE V2**：`PWM_AB2`（A 21-11bit、B 10-0bit，设备值=App 值×7）；`PWM_B34`=A 通道波形、`PWM_A34`=B 通道波形（X 5bit / Y 10bit / Z 5bit，X=sqrt(F/1000)×15）；100ms 刷新。
* **BLE 灵猫 BMTR**：`50`（17B）开气压上报（**仅写 150A**——实测写厂商特征 FF01 会导致设备重启关机）；通知 `D0` 中气压 = 字节 8-9 int16 LE / 100（kPa）；`66` 气压清零；每秒重发使能保活。
* **本地中继 V4/V3**：`dglab/relay_v4.py`、`dglab/relay_v3.py` 按官方参考实现 `dglab-websocket-server`（v3-server.ts / v4-server.ts）1:1 移植：hello/clientId 分配、tid 接入配对、message 路由、心跳、空闲超时与断开通知（含关闭码 4000/4001/4002 与 V3 错误码 401/400/402/403/404/406、波形分包定时下发）均与官方一致。

**蓝牙驱动行为**

* 连接后自动以「**静默**」波形启动双通道（零强度帧持续发送：会话保持、无输出，强度状态不失效）——协议规定输出 = 通道强度 × 波形强度缩放，无波形时波形字节全 0 会导致**任何强度都没有输出**；发送其他波形即切换，「归零」= 强度清零 + 切回静默。
* 强度修改采用官方 B0(0b11 绝对设定)+B1 应答流控，并带 **1 秒应答超时**防死锁；输出循环对单次蓝牙写入失败**容错继续**（连续 9 秒失败才停止），瞬时无线干扰不会中断波形。
* **OVC 负鼠**（`47L127000`）：`B0` 双通道振动强度段（0-100/25ms）；`B3` 强度设定（0xFF=保持该通道），**只接受 10 的倍数**（引擎侧 `set_strength` 自动取整；UI 侧负鼠卡片的强度上限/步长/直接设置输入非 10 倍数时自动四舍五入为 10 的倍数且最低 10，开火强度保留 0=跟随上限哨兵，取整逻辑见 `ui/live.clamp_ovc_strength`）；`B2` 屏显刷新（固定 21 字节体 + A/B 两字节）；`0x50 [颜色][01]` 使能按键 + LED；`D0` 通知携带 16 位按键位图（bit0-15，位定义见 `ui/live.py` 的 `OVC_BUTTON_BITS`），按下沿发 `ovc_button` 事件。可绑定动作（`Engine._OVC_BUTTON_ACTIONS`，与 `ui/live.py` 的 `BUTTON_ACTIONS` 一致，均为**单通道**操作）：`a_/b_` 前缀 × `strength_up` / `strength_down`（该通道加减，步长取该设备 `strength_step`）、`a_/b_` 前缀 × `wave_up` / `wave_down`（该通道波形步进，`Engine._step_device_wave(slot_id, channel, delta)` 按 `wave_order(family)` 循环，静默 -1 绕回波形表末尾）、`fire`、`estop`；旧版 `zap_a/zap_b` 已由强度±取代；`none`（无）保留为第一项，历史配置中的 `none` 如实显示为未绑定。按键绑定为**官方离线模式页复刻 + 交互改良**（`ControlPage._BV_*` 常量表）：坐标一律取自官方截图像素系（647×291），按 `_BV_SCALE=1.5` 缩放平移后画到 1040×340 Canvas（`_BV_OX=-30`，为左侧 OSC 输入框留出空间），固定黑色配色（底 #0B0B0B、标注米黄 #D9CFA6），不随应用主题变化。机身=切角八边形 (188,92)-(444,227) 三层描边（#8E8E8E/#585858/#414141），上下轮廓一致、无齿状凸起；屏显窗 (284,112)-(348,143)。全图以机身中心轴 x=316 严格对称：十字键中心 (246,165)（半长 38、臂厚 20=中心圆直径，整体 76×76 与右侧键组 76×74 一致；`_bv_rpoly` 折角圆角化双层描边 + 双层中心圆 r10/r6.5 + 四向箭头远离中心圆），右侧 G(386,141)/D(361,165)/B(411,165)/A(386,189) 双环菱形键（外环 r13/内环 r9.5+字母），两簇中心 246/386 互为镜像；SEL 行 ◁(291,202)/▢(316,202)/▷(341,202) 双层线稿，中心线 y=202 对齐菱形键组最下缘。指示线终点白色圆点（r1.9）落在键面内并**避开字符/箭头**：右簇键点右移 8（D 键因线从上方进入，点落在键面右上 (+7,-7)）、左簇键点左移 8（关于轴 316 镜像），键面字母最后绘制。**按下实时视觉反馈**：`_binding_blocks` 为 11 个键各预置一个半透明亮蓝发光圆（`_BV_GLOW` 位置表，十字键在臂端、菱形键罩住双环、小键在键心，默认 Collapsed，`view.button_glows`）；shell 订阅 `ovc_button`/`ovc_button_up` 事件经 `ui_queue` 转发到 `ControlPage.flash_button`（引擎线程安全入队）——真机是**边沿语义**（长按只发一次按下沿），故按下即点亮保持到抬起沿，抬起时保证最短点亮 0.25s（快按可见），8s 兜底熄灭防丢抬起沿，rebuild 时清空状态；`_refresh_glows` 在 tick（10ms）中熄灭到期按键。selftest 断言 `glow_flash_ok`（点亮→最短保持→到期熄灭全周期）。11 个键为纯静态线稿（不再有透明热区与圆圈问号标注），**改绑通过点击标注文字**：每条标注是原生默认样式 Button（模板不替换，但经控件级 Resources 覆写 `ButtonBackground/BorderBrush/Foreground` 及 PointerOver/Pressed 变体键固定深色系，见 `_BV_BTN_DARK`；OSC 输入框同理覆写 `TextControl*` 键，见 `_BV_BOX_DARK`——保证**亮色主题下画布内控件不翻浅**、米黄文字保持可读）内嵌米黄 TextBlock（`bindings`，selftest 断言其 `.Text`），点击弹 MenuFlyout 选择动作；**OSC 时按钮保持可见可再切换**，地址输入框与按钮错开摆放：左列在按钮左侧、右列在右侧、HOME（中间）在按钮下方（`_BV_BOX_W=130`，画布加高到 340 防裁切）。左右标注列 y=104/141/178/215（整体中心 159.5=机身高度中心，垂直居中），左列右缘 x=158、右列左缘 x=474（距机身边缘 30，轴对称），右列行序 行1→G、行2→D、行3→B、行4→A（D/B 行为应用户要求上下调换，D 指示线自 G 键下方穿过 G/B 环间隙落到 D 面右上）；底部归0标签 y=240，左缘 234 / 右缘 398（±82 轴对称），引导线同款镜像折线；HOME 键竖线 (316,202)→(316,248) 直通底部居中的 bit2 改绑按钮（x=316 居中），官方原文「连点5下，可被插槽搜索连接」改作该按钮 ToolTip。默认绑定=官方映射：bit8/9=A 强度±10、bit10/11=A 波形上/下一个、bit15/13=B 强度±10、bit14/12=B 波形上/下一个、bit0/1=A/B 通道强度归0（新增 `a_/b_strength_zero` 动作，走 `set_strength(channel, 0)`）、bit2=none。视觉验证方式：`.tmp_ref/visual_check.py` 宿主（模拟设备+停留控制页+AppWindow.Resize 大窗）+ PIL 窗口截图与官方图拼接比对，用后即删。（`Flyout` 属性仅 Button 投影有，Border 不可用）。绑定写入统一走 `ControlPage._set_binding`。**OSC 传参绑定**：动作表末项「发送 OSC 参数…」→ 存储值 `osc:<地址>`，选中后键帽下方出现地址输入框（`binding_inputs`）；按下沿发 1、抬起沿发 0——BLE `_handle_buttons` 新增抬起沿事件 `ovc_button_up`，引擎 `_on_ovc_button_up` 经 `OscBridge.send_value` 直发（桥未运行则日志提示）。注意 UI 线程外勿实例化 WinUI 控件（探针/脚本只能在应用内操作）。按键绑定块与 LED 颜色下拉**仅蓝牙后端显示**（`backend_kind == "ble"`，Socket 通道无 0x50 指令；郊狼官方蓝牙协议也没有 LED 指令，故郊狼卡片永无 LED 项）。负鼠卡片的强度上限/步长在无历史设定时默认取 10 的倍数（步长最低 10），`Engine._device_step` 对 OVC 槽位同样强制 ≥10。连接页「已接入设备」行可点击选中（与扫描列表共用选中态），重连后未重新扫描的设备也能选中后单独断开；设备行按 (在线集合, 选中态, 电量) 签名去重，避免每 0.5s 重建吞掉点击。
* **BMTR 灵猫**：连接后设备先回 6 字节 `0x53` 问候帧、每次使能回 4 字节 `0x51` 应答帧；`1500` 电量特征订阅会 Access Denied（读取可用）。使能指令每秒经 150A 重发保活。
* **写模式无关紧要**（真机 A/B 实测：停止指令后数据流归零，无响应写/带响应写启动均恢复 10Hz）；写失败先试无响应写再试带响应写（`_write`）。

**自动重连设计**

* bleak 3.x 的 `BleakClient(disconnected_callback=…)` 签名为 `Callable[[BleakClient], None]`；回调里清除僵尸会话、把地址加入 `BleClient.dropped`，主动断开前先置 `session._deliberate = True` 以示区分。
* `Engine._reconnect_loop` **只重连 `dropped` 里的地址**（每地址 5 秒节流）——绝不遍历 saved_devices 盲连：否则会把正在被手机 App/Socket 占用的设备反复 BLE 抢连并刷 `BleakDeviceNotFoundError`。
* 从未在本程序连接过的设备不会被后台抢占；手动断开（用户点击）不进入重连队列。

---

## 实现内幕与调试记录

### Socket V4 波形供给：10 帧小批次 + 播放截止时间记账

App 会在波形数据流断流时**将通道强度归零**（真机日志确认），且对**过大批次会卡死输出**（真机实测 50 帧批次 → `channelAStatus=0`）。客户端因此以 **10 帧（1 秒）小批次**供给：内部按「已入队帧的预计播完时刻（播放截止时间）」记账，截止前 0.6 秒补一批，平均恰为实时速率——队列既不排空也不积压（稳定在 0.6~1.6 秒深度），强度状态全程有效。

切换波形用 `im` 替换在途批次（不发 clear，强度不清零）；**急停** = 停止发送 + 全队列清空 + 全通道强度清零。

### OSC 探测与设置页职责

`OscBridge._map` 给全部映射参数包一层收包记数（`last_rx`/`rx_count`），未映射地址走 default handler 同样计数；概览页探测卡以「监听端口 30 秒内收到数据」判定 VRChat 连接。设置页统一承载：V4/V3 中继地址与本地中继开关（`relay.v4_local/v3_local` 持久化，连接页卡片按此连接）、OSC 地址端口、写日志文件开关（`Engine.set_file_logging` 挂/摘 FileHandler）。联动页保留桥接开关、输出映射显示与输入映射编辑。

### 急停语义（引擎层，与「归零」一致）

`Engine.emergency_stop`：取消未结束的开火保持（`_fire_holds`，防止放开按钮时把强度恢复回去）→ 后端硬停（V4 停发+清队列、V3 清零+清脉冲、BLE 停发送+清零）→ 对每台输出设备逐通道 `set_strength(0)` + `set_wave(SILENT)`（会话保持，之后任选波形即恢复输出）→ `_selected_wave` 归静默（UI 波形下拉同步）。BMTR 传感器槽位跳过。

### 负鼠按键映射（配置组、动作集与键盘注入）

按键映射按 bit 存字符串，前两段前缀走特殊路径、其余为固定动作集：

* `osc:<地址>` —— 按下发 `1`、抬发 `0`，经 `OscBridge.send_value` 直发任意地址（不必在映射表中注册）。
* `key:<键名>` —— 键盘注入，按下 `keys.press`、抬发 `keys.release`（`dglab/keys.py`，Windows `SendInput`）。键名来自 `KEY_NAMES`（字母/数字/F1-F24/导航簇/符号键等），未收录的用 `VK<hex>`；导航簇与 Win 键带 `KEYEVENTF_EXTENDEDKEY`，否则部分程序不识别。绑定界面选中「模拟键盘」后点输入框按任意键即捕获键位。注入与捕获都会在日志记一行「按键 bitN → 键盘 X 按下/松开」并附**前台窗口标题**，便于确认是否打到了目标程序。
* 动作集 `_OVC_BUTTON_ACTIONS`：`none`、`a/b_strength_up|down|zero`、`a/b_wave_up|down`、`fire`、`estop`。`fire` 在**按下时 `fire_start`、抬起时 `fire_stop`**，即按住持续开火。

配置组：`ble.ovc_profiles`（配置名 → bit 映射）+ `ble.ovc_profile`（当前激活名）。`_migrate_ovc_profiles` 把旧版单一 `ovc_buttons` 迁成 `{"默认": {...}}`，`_ovc_bindings()` 读当前激活组并以旧字段兜底。新建配置以当前映射为模板，切换即时生效并落盘。

### OSC 发送目标：127.0.0.1 → 本机网卡 IP

真机结论：部分加速器（已实测小黑盒「模式二」，其内核过滤驱动 `heyboxfilter` / `HeyboxPF` 常驻）会**吞掉发往本机 9000 的回环 UDP**，无论目标是 `127.0.0.1` 还是网卡地址都不可达，且表现为「起初正常、中途突然断流」。因此 `OscBridge.start` 在 `out_ip` 为 `127.0.0.1`/`localhost`/`::1` 时改用 `_local_ip()`（UDP `connect("8.8.8.8", 80)` 查路由拿出口网卡地址，不发包），并打一行日志说明已改写。这只能绕开回环拦截，**无法对抗内核级 UDP 劫持**——遇到该情况仍需换加速模式或退出加速器。

### Socket V4 强度镜像：单边增量补丁导致的显示归零（已修复）

历史症状：强度被周期性错误归零、上限在 100/200/101 之间跳变。真机日志定位结论：**App 的增量补丁是单边的**——强度补丁只带 `props`、舒适上限补丁只带 `slotState`；旧版合并逻辑把缺失的一半当成「清空」，导致本地镜像被反复清掉，**显示归零但设备实际强度不变**。

现已修复为「缺失即保留」（附回归测试回放真机补丁序列）。App 端 comfortLimit/warmUp 特性会动态调整 `intensityMax`（100→101→102…），显示如实跟随。日志中出现 `… 强度回报为 0` 时为归零指令回显或 App 端自适应归零，可据此区分。

设计原则：Socket V4 下本程序**不自行维护强度与上限，一切以 App 实时回报为准**（`props.intensityA/B` 镜像显示、`slotState` 的 intensityMax 同步显示）。加减直接下发**相对增量**（`t=3` AddIntensity），直接设置则以 App 回报值为基准求差下发，由 App 自己执行其上限并回报新值。

### 一键开火的强度恢复

放开时的强度恢复会先等 App 回报抬升后的值（最多约 1 秒）再按其求差；若 App 始终未回报（卡顿/丢帧），则按发射时实际施加的增量**反向撤销**，保证爆发强度不会残留。触发式带 60 秒安全超时，防止参数卡在 True 导致长时间输出。两者在通道处于静默时会临时切「持续」波形以产生输出，结束后切回原本选定的波形。

### 实时输出图的数据来源

数据为按播放时刻投影的波形采样（每 100ms 四段，批次间按播放截止时间无缝衔接），Socket 与蓝牙模式均可用。频率图已移除。渲染为矢量柱状图（`widgets.wave_bars`），A/B 各一栏 0-100，每 250ms 重建。

### 灵猫 BMTR 气压诊断（排查记录）

灵猫是**纯传感器设备**——程序不向其下发任何波形/强度指令（Socket 与蓝牙链路均已在路由层屏蔽，输出类指令永远不会落到灵猫槽位）。

历史上「收不到气压」的真机定位结论：50 使能指令与写入模式均无误（A/B 实测：停止指令后数据流归零，无响应写/带响应写启动均恢复 10Hz），**真正根因是 bleak 同步调用通知回调，而回调曾是 `async def`，协程从未被执行**——通知帧一直在到达、只是从未被处理，现已改为同步回调。

每秒向日志输出一行 `D0接收统计(1s)`（如 `0000150B:10帧 最新… 气压 0.00 kPa`，完全无通知时明确记录）。气压为 150B 上 17 字节 `0xD0` 帧（100ms 一帧，第 8、9 字节 int16 LE ÷100 = kPa）；连接时设备先回 6 字节 `0x53` 问候帧、每次使能回 4 字节 `0x51` 应答帧，均可在日志看到。

现场排查可用 `_research/bmtr_probe.py`（转储 GATT、订阅全部通知、`--wwr/--ff01/--raw` 变体实验）。

### 郊狼 LED 灯色（未见于官方文档，探针实测）

官方 App 可切郊狼主机灯色但协议文档未写明。`_research/coyote_led_probe.py` 实测结论：**17 字节 `0x50 [色码][00] + 14×00`**（与灵猫 0x50 同构，mode 字节 0x00），经 150A 带响应写，设备回 4 字节 `0x51 [色码] 10 64` 逐字回显色码；负鼠式 3 字节短帧 `0x50 [色码][01]` 被拒绝（回 `E0 50 02`）。色码表与负鼠/灵猫一致（0=熄灭、1=黄、**2=红**（旧称 magenta，实机为红色）、3=紫、4=蓝、5=青、6=绿）。UI 的 LED 下拉用中文标签+色点（`ui/live.LED_OPTIONS`），色码以整数直传 `set_led`（`set_led` 同时接受字节与旧键名）。强调按钮（accent/急停/日志等级选中态）用 `widgets.solid_button_states` 在元素 Resources 里覆盖 `ButtonBackground/Foreground/BorderBrush` 的 PointerOver/Pressed 主题画刷——否则浅色主题下按下时主题灰底会盖掉本地深色底、白字不可见。连接页蓝牙卡片：已保存设备改为与已接入设备一致的行列表（点击选中），「重连」按钮取消（并入「连接选中设备」：先查扫描结果再查保存记录），「删除记录」移到「断开选中」之后并按选中地址操作。连接页 V4/V3 卡片的配对区状态机在 `_update_pairing`：后端切换/断开时主动折叠二维码并复位状态文案（`pairing_open` 标记 + `_last_qr` 清零，保证再次连接同一地址的二维码会重新展开）。强调按钮的按下反馈用 `theme.shade` 明暗梯度：PointerOver 90% 明度、Pressed 80%，前景色不变（修复浅色主题下主题画刷盖掉本地背景导致白字不可见，以及首版覆盖用同色导致反馈丢失）。实现见 `build_coyote_50` + `BleClient.set_led`（coyote_v3 已加入支持），UI 上郊狼卡片在蓝牙后端时显示 LED 下拉。

### 日志与诊断

所有日志同步写入 exe 同目录的 `dglab_osc.log`（带时间戳，`log_to_file` 可整体关闭）。Socket 模式默认记录收发的协议帧（`log_frames` 可关，ping/心跳已过滤），但**协议帧只写文件、不进 UI 日志**（`Engine.log_frame` 直走 `_file_only`）——UI 日志页只保留关键事件（连接/断开、波形与强度变更、开火、急停、错误），此前每秒几十条帧日志把日志页刷爆导致多设备时明显卡顿。Socket 模式下 App 不上报设备电量（已确认），电量显示为 `--`；蓝牙模式电量正常。

### 设备路由与传感器隔离

`Engine.resolve_slot(slot_id, family, output_only)`：显式 slot > 指定家族首个 > 首个设备；`output_only=True` 跳过 BMTR。灵猫是**纯传感器设备**，程序不向其下发任何波形/强度指令——V4 后端 `_check_output_slot` 对所有输出类指令兜底抛错，引擎路由（加减/直接设置/波形/归零/开火/OSC 输入回退）也全部 `output_only`；V4 波形供给循环跳过 BMTR 槽位。`_log_app_zero` 在强度回报为 0 时打日志（归零指令回显或 App 端自适应归零）。

### 强度控制入口（三条路径同源）

* **加减**：+/- 按钮（步长可设），V4 下发相对增量。
* **直接设置**：面板「A/B 直接设置」+「应用直接设置」，或 OSC `StrengthA/B` 绝对值——以 App 回报值为基准求差下发，统一受 `max_strength` 钳制。
* 「最大强度上限 / 开火强度 / 加减步长」在**每台输出设备卡片内独立设置**（`config.device_settings[slot_id]`，未设置的键回退全局 `max_strength`/`fire_strength`/`strength_step`），输入框绑定 `TextChanged` **改动即时生效**；引擎 `device_setting(slot_id, key)` 统一取值，`set_strength` 钳制、V4 加强度上限检查、`_fire_value` 开火强度均按设备取。

### OSC 桥接细节

* 输出 10 Hz 节流、**值变化才发送**；每设备独立参数组，家族前缀 + 同族序号（`device_osc_names`）。`/avatar/change` 清 `_last_sent` 全量重推（换头像场景）。
* `WaveA/B`（Int）为**直接跳变**：索引 `0=静默、1=持续、2+ 官方波形`，**按家族独立**（`wave_order(family)`，郊狼 26 项 / 负鼠 22 项）；`WaveStepA/B`（Int 非零）为步进：正=下一个、负=上一个，循环，0 无操作。步进从 `Engine._selected_wave` 读当前值。
* `Fire`（Bool）为**触发式**：True → `fire_start`，False → `fire_stop`（不是单次脉冲）。
* 输入定位各类型**首个**设备（`input_target_slot`），回退时跳过 BMTR。
* OSC server 在独立线程，`_spawn` 把协程投递回引擎 loop（loop 引用在 `start()` 捕获；loop 关闭时直接 `coro.close()` 防泄漏）。

### 归零语义

「归零A/B」= 该通道强度清零（V4 用 `t=7 v=0`）**并同时切回静默波形**，UI 波形下拉同步显示静默；波形会话持续发送，之后选择任意波形即恢复输出。`Engine._selected_wave` 是每通道波形记簿（引擎侧权威），UI 下拉框由 `_sync_wave_combos` 单向同步。

---


### 模块化重构：OSC 外置为插件 + 概览数据流视图（2026-09-30）

1. **模块宿主 `plugins.py`**：`PluginManager` 扫描 `modules/*/plugin.py`；`META` 用 AST 字面量提取（不执行模块代码即可列出元数据）；加载优先包导入（有 `__init__.py`），失败回落 `spec_from_file_location`（散文件模块，打包版 exe 旁投放也可加载）。启用状态：`osc_bridge` 沿用 `osc.enabled`（兼容旧配置），其余模块存 `config["modules"]["enabled"]`。`install/uninstall` = 写启用状态 + 加载/卸载；`start/stop` 走引擎循环。
2. **OSC 迁移**：`vrc/osc_bridge.py` → `modules/osc_bridge/{bridge,plugin}.py`；`wave_order` 下沉到 `dglab/waves.py`（设备层知识不留在插件里）。`Engine.osc` 变为 property（从模块实例取 bridge），`osc_start/osc_stop` 委托 PluginManager；引擎 `start()` 后 `submit(modules.autostart())`，shell 不再负责启动 OSC。`Config.DEFAULTS["osc"]` 改为空字典，桥接默认值由 `OscConfig` 在模块 `on_load` 时回填（引擎不 import 任何插件）。桥接每次 start 重建实例 →「改地址/端口 → 重新开关」真正生效（旧实现复用实例，改端口不生效）。桥接新增 `input_values` 记录最近收到的输入参数（`recent_inputs()` 供概览「输入数据值」），`close()` 解绑 action 事件；`StateEvents` 增加 `off(event, cb)`（模块卸载必须自行解绑）。
3. **强度参数公开 API（模块扩展面）**：`Engine.intensity_params(slot_id)` 返回强度/上限/探活/最大强度/步长/开火强度/开火时长/波形快照；`Engine.set_intensity_param(key, value, slot_id)` 写 max_strength/strength_step/fire_strength/fire_duration_s（设备级写 device_settings 覆盖，全局写顶层）与 wave_duration_s（仅全局），越界钳制、非法键 ValueError，写入后 emit `intensity_params` 事件。`_device_step→device_step`、`_ovc_bindings→ovc_bindings`、新增 `wave_selection()` 转公开。全部经 `ModuleContext` 暴露给模块（EXTENSIONS.md 有完整表格）。
4. **概览页重构（数据流视图）**：小卡=已连接设备 / 输出设备 / 已启用输入链路 / 已启用输出链路。链路语义（`live.link_counts`）：输入链路=进入应用的路径（OSC 输入运行中、负鼠按键存在非 none 绑定、每台灵猫传感），输出链路=向外下发的路径（OSC 输出运行中、每台已接入输出设备）。中卡 2×2：输入通道（含未启用项与启用提示）、输出通道（每设备 A/B 一行 + 探活胶囊，channel_status 0/2=正常、1=异常）、输入数据值（OSC recent_inputs + 灵猫气压/边缘）、输出数据值（每通道强度/上限/幅度/波形）。
5. **联动页**：输入/输出映射表统一为「名称|类型|OSC 参数|说明」四列同一列宽（`_TABLE_COLS`）。输出表按接入设备实时装配（tick 0.5s 刷新），**未使用参数隐藏**：Battery 仅设备回报电量时显示、ChannelOK 仅 channel_status 有非零上报时显示、无设备时只留全局 Action 行。输入表按家族过滤（无任何设备时显示全部便于预配置），值列是可编辑 TextBox（与旧实现一致即时生效）。OSC 地址/端口/前缀设置从设置页移回联动页卡片（`_settings_block`），设置页删 `_group_osc` 与「启动时恢复」开关（桥接开关本身即持久化恢复状态）。
6. **模块页**：卡片列出名称/版本/id/描述 + 状态胶囊（运行中/已加载/未安装）+ 安装并启动/启动/停止/卸载按钮；`_module_sig` 变化检测驱动 tick 重建；「扫描模块目录」热加载新放入模块。操作经 `engine.submit` + `add_done_callback` 回填日志与重建。
7. **改名 DGStudio**：窗口标题（xaml + AppWindow.Title）、关于页、`dgstudio.log`/`dgstudio_crash.log`、spec 改 `DGStudio.spec`（hiddenimports 增 `collect_submodules("modules")`）、`build_exe.py` 产物 `dist/DGStudio`。**dist/ 旧产物未重打包，下次构建自动变为 DGStudio**。

陷阱记录：

* `Spec.collections`：PyInstaller 打包动态加载的散文件模块无法静态收集——包形式模块（`modules/<id>/__init__.py`）必须走 `collect_submodules("modules")`；散文件模块运行时按路径加载，不进包。
* `Engine.osc` property 返回 None 时（模块未加载）调用方必须判空；`getattr(osc, "_running", False)` 模式保留兼容。
* 概览页 XAML 宿主从 2 个 ContentControl 变 4 个（InputChannels/OutputChannels/InputValues/OutputValuesHost），`_channels_card` 系列方法整体删除。
* **打包包含全部模块**：spec datas 显式逐文件收集 `modules/`（跳过 `__pycache__`/`*.pyc`）→ 落到 `_MEIPASS/modules`；`collect_submodules("modules")` 只兜底包形式模块（strength_logger 无 `__init__.py` 不进 PYZ，靠 datas 源文件路径加载）。`plugins.module_roots()` 双根扫描：冻结态 = `_MEIPASS/modules`（内置）+ exe 旁 `modules/`（用户投放，同 id 覆盖内置），开发态单根；`base_dir` 取最后一根（模块页提示投放位置）。已验证：`DGStudio.exe --selftest` 通过，冻结日志出现「模块已加载: VRChat OSC 联动」与桥接启动。


### 按键映射模块化 + 配置校验/改名（2026-09-30 第二轮）

1. **按键动作成为模块扩展点**：`plugins.ButtonAction`（key/label/argument_placeholder/on_press/on_release/owner）；`ModuleBase.button_actions()` 可选实现，`PluginManager` 持注册表——`load` 时 `_register_actions`（同名 key 先到先得，重复忽略并告警），`unload`/`register_instance(None)` 按 owner 撤下，`register_instance` 注入实例同步注册动作（对齐加载语义）。META 新增 `"actions": [...]` 静态声明（AST 可读，不执行代码），供模块未加载时把绑定反查到模块（`module_for_action`）。
2. **OSC「发送 OSC 参数」随模块装卸**：`OscModule.button_actions()` 注册 `key="osc"` 动作，按下/抬起经 ctx 直接走 bridge.send_value；`Engine._send_osc_binding` 删除，`_on_ovc_button/_on_ovc_button_up` 分发改道注册表（`binding.partition(":")` 取 key/参数；`key:` 键盘注入与 `_OVC_BUTTON_ACTIONS` 内置动作保持内建）。`live.BUTTON_ACTIONS` 不再含 osc/key 两项，新增 `button_actions(engine)` = 内置 + 键盘 + 模块注册动作；控制页绑定 flyout 与 `_binding_label_text` 改用动态表。
3. **配置校验**：`Engine.binding_missing_modules(bindings)` 找出引用未加载模块动作的绑定（内置/key: 恒可用）；`Engine._startup_modules` 在 autostart 后检查并 emit `binding_modules_missing {modules, bindings}`；shell 收到后存 `_missing_prompt`，`_on_tick` 等 `RootGrid.XamlRoot` 就绪弹 ContentDialog——「启用并加载」逐模块 `modules.install`；「拒绝加载」调 `Engine.reset_bindings` 把相关 bit 置 none 并存盘。窗口可能晚于引擎订阅事件，shell 启动时兜底再触发一次 `_check_missing_bindings`（事件早于订阅会丢）。控制页切换配置文件时同步校验：有缺失先还原下拉选中，弹「启用并加载/拒绝加载」，拒绝则不切换。
4. **配置文件改名**：`Engine.rename_ovc_profile(old, new)`（重排 dict 保序、同步 ovc_profile 激活名、重名/空名/缺失校验，返回错误串或 None）；控制页配置文件行新增「✎改名」按钮 → `ui/dialogs.prompt_text` ContentDialog 输入 → 引擎改名 + 卡片重建。`ui/dialogs.py` 提供确认/文本输入两个 ContentDialog 助手（XamlRoot 取 shell.RootGrid）。
5. **事件**：`modules_changed(module_id)` 在 load/unload 发出，shell 转发 notify 控制页/模块页（ControlPage 补 `on_notify`→rebuild）；EXTENSIONS.md 事件表补 `modules_changed`/`binding_modules_missing`。
6. 测试：test_modules 新增动作注册/撤下/META 反查/重复 key、缺失检测、reset_bindings、rename_ovc_profile（含保序）等 8 项；test_features 动作表断言改为 `BUTTON_ACTIONS == _OVC_BUTTON_ACTIONS`，OSC 透传测试改经注册表路径（FakeModule.button_actions + register_instance）。共 119 项。


### 模块装卸热重载修复（2026-09-30 第三轮）

问题：模块装卸只刷新当前激活页——`modules_changed` 的处理按 `tag == self._tag` 过滤，
控制页作为缓存页在导航回去时绑定选择框仍含已卸载模块的动作项（如「发送 OSC 参数」），
要等切换配置文件触发 rebuild 才消失；且卸载后映射校验不会自动重跑。

修复：
1. `shell._notify_all()`：遍历全部已构造页面调用 `on_notify`（异常逐页捕获），
   `_on_modules_changed` 与缺失绑定弹窗收尾均改走它——装卸即时刷新控制页
   flyout、模块页列表、概览页输入通道等，无需手动刷新。未构造过的页面首次
   导航时按当前状态新建，天然无旧态。
2. 卸载后立即重跑映射校验：`Engine` 订阅 `modules_changed`（`_on_module_change`），
   以「实例已被移除」判定为卸载（安装只会消除缺失、不会新增），触发
   `_check_missing_bindings` → 引用未启用模块的映射立即弹窗（启用并加载 / 拒绝加载）。
   `_modules_ready` 门闩保证 autostart 期间不产生过早弹窗，启动完成后统一检查一次。
3. 测试：`test_unload_triggers_missing_detection` 经引擎循环卸载，断言
   `binding_modules_missing` 携带 modules/bindings 载荷（137 项全过，含用户新增
   test_alice_cradle）。


### 模块配置从主配置拆分（2026-09-30 第四轮）

需求：模块相关配置不再混在主 config.json，由模块宿主自行维护。

1. **存储布局**：新增 `config/` 目录（与主 config.json 同目录，目录位置取
   `dirname(engine.config.path)`，selftest/打包态自动跟随）。`config/modules.json`
   存启用（自启动）状态；`config/<settings_key|id>.json` 存各模块私有设置
   （OSC 为 `config/osc.json`，alice 为 `config/alice_cradle.json`）。
2. **JsonDict（plugins.py）**：写穿字典——顶层 `__setitem__/__delitem__/update/
   pop/setdefault/clear` 立即保存 JSON；**嵌套字典就地修改不触发保存**（已知
   限制，需显式 `save()`）。`PluginManager.settings_for(id)` 懒加载 + 缓存，
   `ModuleContext.settings` 委托它；`_settings_stem` 解析文件名：实例
   settings_key → META settings_key → 模块 id。
3. **迁移**：`_migrate_from_main_config` 在 PluginManager 构造时执行——
   `modules.enabled` → modules.json；`modules.settings.<id>` → 各模块文件；
   顶层 `osc` 等键（含旧 `enabled` 混写）→ 拆为「enabled 进 modules.json +
   其余进设置文件」并从主配置删除；幂等（键不存在即跳过）。主配置 DEFAULTS
   移除 `osc`/`modules` 段，Config.load 删除 osc 特例分支。
4. **启用语义归一**：`is_enabled` 统一读 modules.json，META 新增
   `"default_enabled"`（无显式记录时的默认值，osc_bridge = true 保持全新安装
   自启动 OSC 的旧行为）；OSC 桥接内部的 `enabled` 门槛与 DEFAULTS 键删除
   （启用归属模块系统）。联动页开关改调 `modules.install/uninstall`，
   `flush_config` 不再写 enabled。`set_enabled` 对嵌套 enabled 的修改需显式
   `save()`（本轮修掉的落盘脱节 bug：冒烟发现文件与内存不一致）。
5. **配套**：build_exe.py 备份/恢复 `config/` 目录（copytree）；.gitignore/
   .zcodeignore 增 `config/`；测试 FakeEngine 配置带 path+save 且每实例独立
   临时目录（隔离 config/ 串扰——迁移测试写入的 9100 曾泄漏到其他用例）；
   test_alice_cradle 的 SyncTests 增加 10048 端口占用 skipTest（应用运行时
   跑套件属正常）。138 项全过（1 skip 环境性：用户运行中的实例占用 8920）。

### Alice in Cradle 联动：Hub 兼容服务 + Unity 模组（2026-09-30 第五轮）

需求：参考 [sllying/AliceInCradle_X_DGLAB](https://github.com/sllying/AliceInCradle_X_DGLAB)，
让《爱丽丝的摇篮》(Win ver030) 与本项目双向联动，且**不依赖 DG-Lab 官方 Hub 与手机 App**。

1. **两侧分工**：
   - `modules/alice_cradle/`（项目插件端）——`server.py` 用 asyncio `start_server`
     自己扮演 Game Hub，`plugin.py` 是模块入口（META id `alice_cradle`，
     settings_key `alice_cradle` → `config/alice_cradle.json`，
     `default_enabled` 未设即默认不自启）。
   - `mods/AliceInCradleLink/`（Unity 模组端）——BepInEx 5 插件，读玩家状态
     上报强度、拉引擎状态画 F9 面板。
2. **协议面**：v1 `/api/game/<clientId>/...` 与 v2 `/api/v2/game/<clientId>/...`
   两套前缀都吃；端点 `strength_config`（v2 别名 `strength`）、`pulse_id`（`pulse`）、
   `pulse_list`、`fire`（v2 在 `action/fire` 下）。响应统一 `{"status":1,"code":"OK",…}`，
   未知路径 `{"status":0,"code":"ERR::NOT_FOUND"}`。
   **`GET /api/game/all` 的响应额外挂 `dgosc` 字段**（官方 Hub 没有此字段）：
   connected / device_name / battery / max_strength / channels[{strength,limit,wave}]，
   模组据此在面板上显示引擎侧真实状态——这是「引擎数据向外传递」的通道。
3. **指令→设备**：`GameClient` 按 clientId 记账（add/sub/set + limit，即 Hub 的
   clientStrength 语义），`_sync_loop` 用脏标记按 `sync_interval`（默认 0.25 s）合并，
   取各客户端强度的最大值按比例换算到设备量程（`resolve_slot(family, output_only)` →
   `set_strength`，`_last_sync_value` 去重避免重复下发）；`fire` 走
   `ctx.zap`（`_spawn` 后台执行，先返回响应再持续 `time` 毫秒）。设备未接入时
   只缓存状态、不下指令，`dgosc.connected` 如实为 false。
4. **模组构建**：`mods/AliceInCradleLink/AliceInCradleLink.csproj` 是 SDK 风格
   netstandard2.1 工程，`<Reference>` HintPath 直接指向游戏的
   `AliceInCradle_Data/Managed`（`unsafeAssem.dll` 等）与 `_vendor/bepinex-core`
   （BepInEx.dll / 0Harmony.dll，从官方 zip 解出，`<Private>false</Private>` 不拷贝）。
   `dotnet build -t:Deploy` 把 dll 拷进游戏的 `BepInEx/plugins/AliceInCradleLink/`。
   BepInEx 5.4.23.5 发行包留在 `_vendor/BepInEx_win_x64_5.4.23.5.zip`。
   **坑：`-t:Deploy` 会替换默认目标，只执行 Copy 不编译**——改完 C# 必须
   先 `dotnet build`（默认 Build 目标）再 `-t:Deploy`，否则「0 警告 0 错误」
   是假象、游戏里仍是旧 dll；部署后用文件大小/mtime 与 `bin/Debug` 比对确认。
5. **验证结论**：双向均已实机截图确认——游戏 → 应用（`strength_config` POST 到达、
   模块日志与设备强度变化）、应用 → 游戏（改 `dgosc` 快照后 F9 面板的通道条/虚拟强度
   同步变化）。上游 HP/MP/EP→强度这条链路需要真实战斗场景才触发，本轮未驱动剧情/战斗，
   故面板「累计信号」仍为 0，映射规则见 `StatusWatcher` 注释。

### 配置声明驱动 + 联动页按模块分类（2026-09-30 第六轮）

核心决策：**配置文件为核心**——模块在 `META["config"]` 声明全部配置项，宿主自动装载补齐；
UI 稳定不随配置变化重写；联动大卡片按**联动模块**分类（模块内统一模板
输出映射表 → 输入映射表 → 模块设置），郊狼/负鼠/灵猫降级为 OSC 模块卡片内表格的通道分组。
存储维持 B 方案：`config/` 分文件（osc.json 等），`osc.` 前缀只是展示层。

1. **宿主（plugins.py）**：`spec_defaults(spec)` 由声明派生扁平缺省表；`ModuleBase.config_spec()`
   （实例优先，回落 META["config"]）；`discover()` 后 `ensure_all_configs()` 按声明补齐每个
   `config/<stem>.json`（只补缺失顶层键与 map 子键，不覆盖已有值）；`settings_for()` 装载时
   同样走 `_apply_spec_defaults`。迁移（`_migrate_from_main_config`）可能改 enabled 表，
   `__init__` 顺序 = discover → 迁移 → `_refresh_enabled_flags` → ensure。
   **陷阱**：discover 构建 meta 条目时若在字典字面量里调 `is_enabled`，此时
   `self._meta[module_id]` 尚未赋值，`default_enabled` 回落失效，全部模块显示「已停用」——
   enabled 标志必须整表构建完成后回填（`_refresh_enabled_flags`）。
2. **OSC 模块 v1.3.0**：META["config"] 26 项字面量声明（bridge 组、device_prefixes map、
   custom_inputs list、coyote_in/ovc_in 两组 param 项）；`OSC_CONFIG_DEFAULTS =
   spec_defaults(META["config"])` 成为唯一缺省源，`OscConfig(data, defaults=...)` 深拷贝注入；
   bridge 收包注册改为 handlers 表 + 声明键循环 + `custom_inputs` 额外地址注册
   （`{"param": 头像参数, "target": in_* 键}`，实现「[选项]预设目标」的自定义映射）。
3. **联动页（link_page 重写）**：顶层卡片 = 模块（OSC 特化 + 通用 `_module_card`：声明条目
   自动渲染，`config_init` 不出卡）。`_declared_row` 按 type 派发控件：int/float→滑块
   （量程≤1000，DragCompleted 提交防写盘风暴）或 NumberBox、str→文本框、bool→开关、
   choice→下拉（[选项]）、param→AutoSuggestBox（[参数] 可输入可联想，空文本不写回）。
   自定义映射行为列表式增删（添加映射/删除，写 `custom_inputs` 即落盘）。
   **win32more 陷阱**：AutoSuggestBox 无 `Suggestions` 属性，预定义项要用 `Items.Append`。
4. **初始化配置模块（config_init）**：`save_all`（主配置+全部缓存 JsonDict+modules.json）、
   `collect_bundle/export_to`（单文件导出包 `{"__bundle__","version","files"}`）、
   `load_from`（bundle 或纯主配置合并写盘 → 清缓存重新补齐 → 重载 modules.json）、
   `restart_running`（逐模块 stop+start 使新配置立即生效）。设置页新增「配置文件」组
   （保存到文件 / 从指定文件载入… / 导出全部配置…），文件选择器为 comdlg32
   GetOpen/SaveFileNameW 原生对话框（`ui/dialogs.py`）。

### 核心参数目录 + 双向映射表（2026-09-30 第七轮）

参数处理逻辑重构：**核心统一定义可操作参数，参数名固定不可改；模块只声明自己
一侧的参数；两者用两张映射表（双向表达式）连接**。常规模块与 OSC 走同一套模板，
OSC 的差别仅在参数表是动态建立的。

1. **核心参数目录（`dglab/params.py`）**：`core_inputs()` 19 项输入参数
   （郊狼 `in_*` / 负鼠 `in_ovc_*` 的 strength/wave/wave_step/zap A+B + 每家族 fire
   + 全局 `in_emergency`，带 `range` 钳制范围）；`OUTPUT_SIGNALS` 定义输出信号
   （OVC=COYOTE；BMTR=Pressure/EdgeState/Battery/Connected）；`output_key(family, index,
   signal)` → `家族.信号`（首台）或 `家族.序号.信号`（多台）；`build_dispatchers(api, specs)`
   统一构造派发执行器，OSC 与游戏模块共用；`device_state_values(state)` 把全部接入设备
   （含 BMTR 传感器）状态展开成 `家族.信号` 值空间，`core_aliases` 提供 Strength/Limit/
   max/Battery/Connected/Pressure 别名。**陷阱**：`Slot.is_output_device` 语义是「可命令」
   （非 BMTR），不是「可读遥测」——`device_state_values` 早期误用它做门控，导致 BMTR 气压
   永远进不了表达式值空间（缺变量 → ExprError → 输出行消失），已去掉该门控。
2. **映射引擎（`dglab/mapping.py`）**：`MappingEngine` 持 `armed` 门 + 两张表
   （`set_mappings` 输入 / `set_outputs` 输出），`pump()` 同时求值：输入用 `expr.eval_int`
   取整钳制后派发，输出用 `expr.evaluate` 再 `_typed` 归真/取整；`signal(name, value)`
   把外部命名值灌入信号空间；`last_values` / `out_values` / `errors` / `out_errors`
   供 UI 实时值与错误显示。`expr.py` 先做精确键匹配再 `ast.parse`，故 `{COYOTE.StrengthA}`
   这类带点 id 可用；缺变量抛 ExprError（对应 out 键不出现）。
3. **OSC 动态参数表（`modules/osc_bridge`）**：`META["dynamic_params"] = True`，
   `_track_input` 收到 `/avatar/parameters/<name>` 即以同名参数建表并 `engine.signal`，
   配置文件只存 `mappings` / `outputs` 两张表 + 桥接设置；`link_params()` 回报近期收到的
   参数名作变量池；`reload_config()` 让映射编辑热生效。**两个真实缺陷修复**：
   `_track_input` 旧签名 `(addr, args: tuple)` 与 pythonosc 默认处理器的
   `callback(address, *args)` 不符（收到裸 int 崩在 `args[0]`，静默杀掉全部 OSC 输入），
   改为 `*args`；`_send_param` 被 `_push_values` 调用却从未定义（每次推送 AttributeError），
   补上写 `/avatar/parameters/<name>`。
4. **AIC 模块（`modules/alice_cradle`）**：`META["params"]` 静态声明 HP/MP/Hurt 等命名数值，
   MOD 仅作数据发送端 `POST /data` 上报，模块按 `mappings` 求值驱动设备、按 `outputs`
   组装 `GET /data` 回传（字段名可重命名）；`link_params()` 返回声明参数；旧 `output_map`
   迁移到 `outputs` 行表。
5. **联动页统一模板（`ui/link_page.py` 重写）**：删除 OSC 特化分支，每张模块卡片都渲染
   输出映射表（核心来源下拉固定 + 可重命名参数名 + 表达式 + 实时值 + 删除）、
   输入映射表（核心输入下拉固定名称 + 表达式 + 实时值 + 删除）、模块设置（`_declared_row`
   按 type 派发）；`_var_pool` 汇总模块 `link_params` + 运行期 `engine.values()` + 核心输出 id
   作变量联想；「保存设置」写盘后对运行中模块逐个 `submit(reload_config())` 热重载。
   验证：`Ran 160 tests OK`、`main.py --selftest` EXIT=0、实机截图确认统一模板渲染。

### 映射表下拉插入 + OSC 直接定义变量 + AIC 模组纯数据化（2026-10-01 第八轮）

1. **模块可写 / 可读参数模型（`plugins.py` + AIC 模块）**：`ModuleBase.link_params()`
   语义明确为**可写参数**（模块喂进信号空间、输入表达式 `{变量}` 引用），
   新增 `read_params()` = **可读参数**（模块从核心读走回传对端的字段）；
   `META["reads"]` 按「核心输出信号名 → {label, name, type}」声明，宿主 discover
   的 meta 表新增 `reads` 键。AIC 声明 StrengthA/StrengthB/Battery/Connected/Pressure，
   `materialize_reads(settings)` 在 on_load 时给空 `outputs` 表按配置家族落地默认行
   （COYOTE 落 4 行、BMTR 落 Pressure/Battery/Connected；已有输出行则不动）。
   `link_page._var_pool` 补充：未加载实例也把 `meta["params"]` 计入变量池。
2. **表达式下拉插入（`ui/link_page.py`）**：输入 / 输出表的表达式格改为
   `_expr_field` = AutoSuggestBox + 「参数」「运算」两个 MenuFlyout 按钮
   （control_page 的 MenuFlyout 先例），点选即把 `{参数名}` / 运算符片段
   （`（ + - * / ）`，全角显示半角插入）**追加到表达式尾部**；AutoSuggestBox 的
   TextChanged 写穿落盘，故插入即保存。运算符 tokens 带空格（`" + "`）省得手敲。
3. **OSC「直接定义变量」（`modules/osc_bridge/quickmap.py` 新文件 + 联动页
   `_quickadd_block`）**：OSC 卡片顶部新增输入框 + 「生成映射表」按钮；输入
   头像参数名（逗号/分号/空白分隔）即生成映射行，匹配顺序 = 默认命名全名
   （`_input_name_table` 按设备前缀模板反推、输出用 `default_output_rows` 的名字表）
   → 信号后缀（`MyStrengthB` → `in_strength_b`，家族按名字里 ovc/bmtr/in 提示判定）
   → 自由输出字段（绑定第一个未占用核心输出参数）。命中默认命名的变量**双向各一行**
   （旧协议同名参数输入/输出共用）。生成的行追加进既有 `mappings`/`outputs`，
   同名参数/字段/来源已占用即跳过；纯逻辑独立成模块便于单测
   （`tests/test_osc_quickmap.py` 7 项）。
4. **联动页对齐（任务 3）**：核心坑是**表头行 auto 列塌缩**——表头行没有删除按钮，
   auto 列宽 0 → star 列变宽 → 「实时值」表头整体右移压到删除按钮上方。修复：
   `_head_row(tail_button=True)` 时放一个 `Opacity=0 + IsHitTestVisible=False` 的
   同款删除按钮占位（布局宽度参与测量），表头与数据行逐列对齐。其余：数据行控件
   全部 `_vcenter`（44px 行内垂直居中）、参数下拉/名称框/滑块/NumberBox/文本框改为
   stretch 填满列宽（`W.number_box` 支持 `width=None` 拉伸）、表头行高 26 + size 11。
   实机截图复核：表头「实时值」正对数值列，各行控件左右边缘对齐。
5. **AIC Unity 模组重写为纯数据发送端（v0.2.0）**：删除 Game Hub 协议与全部强度
   换算——`HubClient.cs`→`DataClient.cs`（后台线程周期 `POST /data` 上报最新值
   载荷 + `GET /data` 轮询回传字段，JObject 序列化/解析）、`StatusWatcher.cs`→
   `VitalSampler.cs`（每帧反射读 HP/MP/EP 与 `EpCon.getOrgasmedTotal()`，差分产生
   Hurt/Heal/MpLost/MpGain 事件值 + Orgasming 保持窗，数值口径与模块
   `META["params"]` 一致，**不做任何换算**——映射全部交给 DGStudio 表达式）；
   `LinkConfig.cs` 精简为 通信/信号口径/状态面板 三组（新增 ReportSeconds、
   MaxChange 防读档跳变、OrgasmHoldMs）；F9 面板改为显示回传字段网格（GET /data
   的 data 字段）+ 本地数值 + 上报/错误计数；场景切换重建 runner、`Application.quitting`
   收尾、后台线程 SafeLog 等 BepInEx 陷阱对策全部保留。`dotnet build -t:Deploy`
   0 警告通过并已部署到游戏 BepInEx/plugins。模组默认读取 GET /data 的
   StrengthA/StrengthB/Battery/Connected 字段（AIC 模块可读参数默认行恰好提供）。
6. **验证**：`Ran 171 tests OK`（新增 quickmap 7 项 + materialize/reads 4 项）、
   `main.py --selftest` EXIT=0、实机截图确认联动页渲染与快速生成链路
   （输入 `DGLabBattery, MyPulse` → 输出表新增 `DGLabBattery ← {COYOTE.Battery}`，
   未识别变量在无空闲输出参数时跳过），测试产生的映射行已从 config 清理还原。

### OSC 双向自定义参数名 + 设置区回归老样式（2026-10-01 第九轮）

用户复核第八轮后调整方向：**放弃「直接定义变量」批量生成**（quickmap.py 及其测试
已删除），改为「选核心参数即自动生成默认参数名与表达式」的逐行交互；OSC 输入表
也加「参数名」列实现**双向自定义参数名**；模块设置区块回归老版 field_row 样式。

1. **默认参数名生成（`modules/osc_bridge/bridge.py`）**：`default_input_name(config,
   param_id)` 按设备前缀模板反推（`in_ovc_wave_step_b` → `DGLabOvcInWaveStepB`，
   emergency → 全局前缀+Emergency）；`default_output_name` 按设备参数名+信号
   （`COYOTE.2.Battery` → `DGLab2Battery`，Action → 前缀+Action）。前缀兜底
   `DGLab{家族名}`。测试 `tests/test_osc_defaults.py`。
2. **输入表参数名列（`ui/link_page.py`）**：OSC（dynamic_params）卡片输入表改用
   `_IN_OSC_COLS`（核心参数 | 参数名（可自定义）| 表达式 | 实时值 | 操作），行结构
   变为 `{param, name, expr}`（引擎仍只消费 expr，name 为显示层 + 表达式生成锚点）。
   **选择核心参数或添加行即自动生成默认参数名与表达式** `{参数名}`；手动改名时若
   表达式为空或恰为 `{旧名}` 直传形式则同步改写。参数名框的提交挂
   `QuerySubmitted + LostFocus`（**不能挂 TextChanged**——同步表达式需要 rebuild，
   逐键提交会导致每敲一个字重建整页丢焦点）。通用模块（AIC）输入表保持无名称列。
3. **输出行自动生成**：选择核心来源参数即重置 `name`（默认名：META["reads"] 声明
   优先 → OSC 设备前缀命名 → 信号短名）、`expr` = `{参数id}`、`type` 按规格刷新；
   `_add_output` 同样走默认名。`_default_output_name(module_id, cfg, key)` 统一三态。
4. **materialize_names（osc 插件 on_load）**：存量输入行缺 `name` 时按模板补默认名
   （仅显示层，expr 不动）。
5. **模块设置区回归老样式（对照用户截图）**：`_settings_block` 弃用表格行，改
   `W.field_row(标签, 说明, 控件)` 全宽行（标签 14 号+说明 12 号 text3 居左、控件
   右对齐垂直居中、行间 divider）；标题「OSC 地址与端口（修改后重新开关上方桥接
   生效）」/ 通用「模块设置（修改后重新开关上方模块生效）」。控件定宽右对齐：
   文本框/下拉 170、滑块 170+当前值、NumberBox 130、参数联想 200、map 型自然宽度。
6. **验证**：`Ran 168 tests OK`（删 quickmap 8 项、增默认名/补名 6 项）、
   `--selftest` EXIT=0、实机截图确认老样式设置区与 OSC 输入表参数名列、
   `build_exe.py` 重建 dist（打包 selftest EXIT=0）。
7. **坑**：WinUI Button 的合成点击（CUA a11y 路径）不稳定——`click` 常常只给焦点
   不触发 Click，`AXPress` 也可能无效；实测「点击聚焦 + Return 键」才可靠触发。
   自动化验证 UI 写穿逻辑时优先用纯函数级断言替代控件点击。

### 映射行增删重置 + Bool 归一 + AIC 去家族化（2026-10-01 第十轮）

1. **映射行增删导致其他行被重置的根因（`ui/link_page.py`）**：添加 / 删除映射行都会
   `rebuild()` 重建整页，WinUI 在拆除旧 ComboBox 时可能以 `SelectedIndex = -1` 触发
   `SelectionChanged`，而联动页的下拉处理器直接 `choices[sender.SelectedIndex]`
   ——负索引在 Python 里合法，取到**末项（急停）**，于是那行映射的参数被改写、
   表达式被重新生成，表现为「其他已指定的映射被错误重置」。修复与控制页
   `_profile_selector` 同款守卫：`0 <= idx < len(choices)` 才处理（输入行 / 输出行 /
   choice 设置三处），并改用闭包捕获的控件读索引。**教训：WinUI 选择类事件的
   处理器必须防 -1，负索引不会抛 IndexError 而是静默选末项。**
2. **Bool 参数归一 + 类型标注**：`MappingEngine` 现在按核心参数声明区分类型——
   `type == "Bool"` 的目标用「非零即真（|x|>1e-9 → 1）」归一，不再 `round`
   （旧实现 `round(0.4)=0`、`round(0.5)=0`（银行家舍入）导致映射小数/比例值时
   开火、脉冲、急停「不生效」）；其余目标照旧取整钳制。联动页两张映射表的
   核心参数下拉全部带类型后缀（`郊狼开火（Bool）`），用户可一眼看出该映射 0/1。
3. **派发器边沿记忆（`dglab/params.py`）**：fire / zap / 急停派发器记录上一次
   0↔非零状态，仅真实跳变时动作——引擎重载、`reset()` 后首轮求值的重复派发
   不再重复触发（重复 1 不再二连发、重复 0 不再误停其他来源的开火保持）；
   strength / wave 保持原语义（值变化即下发）。
4. **AIC 移除「核心输出取值家族」配置**：`META["config"]` 删除 `family` 项，
   `server.device_vars` 改用 `params.core_alias_values()`——全家族给出
   `家族+短名` 别名（`COYOTEmax`），裸短名（`max` / `Strength` / `Pressure`…）
   按 郊狼 → 负鼠 → 灵猫 顺序取首个有该信号的家族（OSC 桥同款语义，
   `_device_vars` 已重构复用该助手）；`materialize_reads` 按信号跨家族解析
   （Pressure 落到 BMTR）；`drop_legacy_family` 在模块装载时清理存量配置的
   `family` 键。用户的 `{max}*({HPmax}-{HP})/{HPmax}` 表达式在任意设备接入时
   都能解析。v0.6.0。
5. **验证**：`Ran 172 tests OK`（新增 bool 归一、派发器边沿记忆、全家族别名、
   家族清理 6 项；改写 family 相关 2 项）、`--selftest` EXIT=0、
   `build_exe.py` 重建 dist（打包 selftest EXIT=0）。测试假件陷阱：派发器的
   `api.run(coro)` 会 `close()` 协程——fake 的 async 方法体不会执行，需在**调用时**
   （普通函数）记录并返回空协程。

### 爱丽丝的摇篮 MOD 注入实测结论（Unity / BepInEx）

以下都是 v030 + Unity 2022.3.62f2 + BepInEx 5.4.23.5 实测踩到的坑，写新模组前必读：

* **游戏会销毁 BepInEx 的 `BepInEx_Manager` GameObject**（启动约 1.5 秒后先
  `OnDisable` 再 `OnDestroy`）→ 插件本体上的 `Update`/`OnGUI` **从此不再触发**。
  解法：插件 `Awake` 里自造 `GameObject` + `LinkRunner : MonoBehaviour` 承载每帧逻辑，
  `DontDestroyOnLoad`；用 `System.Threading.Timer` 每秒经**主线程捕获的
  `SynchronizationContext.Post`** 回到主线程检查并重建 runner（该同步上下文由
  player loop 驱动，比 GameObject 生命周期可靠）。
* **`OnDestroy` 里绝不能收尾**：管理器被销毁 ≠ 进程退出，在那里 `Dispose()` 会把
  HTTP 轮询线程一起杀掉（表现为轮询恰好停三次）。收尾挂 `Application.quitting`。
* **`UnityEngine.Application.update` 在本作不存在**（只有 `focusChanged`/`quitting`/
  `wantsToQuit`，asmprobe 实测），别指望它做主线程心跳。
* **`JsonUtility.FromJson` 对运行时加载（BepInEx）程序集里声明的嵌套对象类型会静默
  留 null**——外层能解析、`dgosc`/`clientStrength` 全空，且不报错。改用游戏自带的
  `Newtonsoft.Json`（`Managed/Newtonsoft.Json.dll`，`JObject`/`JArray` + `(int?)root["x"]`
  安全转换），解析放在后台轮询线程即可。
* **玩家字段可见性**：`nel.PRNoel` 继承链 PRNoel → PRMain → PR → M2MoverPr →
  M2AttackableP → M2Attackable → MonoBehaviour；`hp/maxhp/mp/maxmp` 在 `unsafeAssem.dll`
  里是 `Family`（protected），C# 侧不可直读 → `VitalReader` 用字段缓存反射；
  `m2d.M2MoverPr.ep`、`nel.PR.EpCon`（`nel.EpManager`）与 `getOrgasmedTotal()` 公开可用。
* **后台线程里日志器也可能抛**（`_log.LogInfo` 一旦异常会静默终止整个轮询线程），
  跨线程日志统一包 `SafeLog`。
* **IMGUI 中文**：游戏默认 GUI skin 无中文字形，`Font.CreateDynamicFontFromOSFont`
  按 "Microsoft YaHei UI"/"Microsoft YaHei"/"SimHei" 顺序取系统字体再赋给样式。
* 游戏窗口失焦即暂停（`Time.timeScale` 归零），截图前必须 `activate_window` 置前。
* `_tools/asmprobe/` 是一次性元数据探针（反射游戏程序集，验证类型链/字段可见性/API 是否存在），
  游戏更新版本后可复用它先确认兼容性，再改模组。

## WinUI 3 / win32more 平台陷阱

* **两种 XAML 加载方式并存**：`XamlLoader.Load` 按内联字符串加载并把 `x:Name` 绑到 `self`（根元素不绑定，`self.ui` 是根）；**`XamlClass` 页面**改用 `LoadComponentFromFile`，从 `xaml/` 目录加载骨架文件，事件属性（如 `SelectionChanged="NavView_SelectionChanged"`）按方法名反射接线。前者已不再使用，新代码一律 XamlClass + 文件骨架。
* **win32more 构造函数不接受属性参数**：`Button(Content=…)` 这类会抛错，必须逐属性赋值——`widgets.py` 工厂全部如此。
* **ButtonBase 吞 `PointerPressed`**（类处理器把事件置为 Handled，实例处理器不触发）：按住/释放类交互**必须用 `Border` 等不消费指针事件的元素**挂 `PointerPressed/PointerReleased/PointerCaptureLost`——「按住持续开火」即此实现（鼠标拖出即停，自带死手开关）。
* **`ComboBox.SelectedItem` 返回裸 `IInspectable`，`ComboBoxItem.Tag` 不可读**（getattr 抛 AttributeError 且易被 except 吞掉）——需要携带数据时用 **Python 侧列表 + SelectedIndex 索引**（已保存设备 `_saved_list`、波形表 `_wave_values` 均如此）。
* **事件回调的 `sender` 也是裸 `IInspectable`**（`SelectionChanged`/`Toggled`/`TextChanged` 一律如此，访问 `sender.SelectedIndex/IsOn/Text` 抛 AttributeError）→ E_FAIL 抛回 WinUI → **进程直接崩溃**（波形步进按钮崩溃即此因）。铁律：**处理器一律忽略 sender，用闭包捕获的控件引用**（如 `_on_wave_combo(view, ch, combo)`、设置页 `_switch_row` 把值传给回调），先创建控件再挂事件（避免构造期触发）。
* 图片显示：PNG bytes → `InMemoryRandomAccessStream` + `DataWriter` → `BitmapImage.SetSourceAsync`（不用 URI 缓存）；二维码用 qrcode 生成后走同一管线。
* 主题：`RequestedTheme` 控制静态骨架（ThemeResource 自动适配），动态行用 `theme.py` 显式令牌取色，切换主题必须**重建全部已构造页面**，只重建当前页会让缓存页残留旧配色。
* `_updating` 守卫防止程序性改动触发 `SelectionChanged`/`Toggled` 回环（每页自带）；`ui_queue` 中的闭包在 UI 线程执行，探针/自检借它注入检查代码。
* 波形图用 **PIL 按 PNG 渲染 → `Image.Source` 交换**（`ui/charts.py` 的 `render_wave_live`，按时间轴逐帧绘制、右端为当前，深浅色两套调色板）——矢量固定柱方案实测会吞波形（按索引降采样跳过峰值帧）且逐柱改高度触发 32 列 Grid 反复重排；PNG 方案是旧 UI 以 10Hz 稳定运行的路线，单元素交换无布局抖动，位图更新管线统一走 `shell.set_image_bytes`（InMemoryRandomAccessStream + BitmapImage）。UI 侧日志（页面刷新失败、扫描提示、submit 错误等 shell.logs.append 的消息）通过 `LogBuffer.mirror` 同步写入文件日志（`shell` 挂 `engine._file_only`，受「写日志文件」开关控制）；引擎事件来的消息带 `from_engine=True` 跳过镜像（引擎已自行落盘），文件里不重复。WinRT 集合属性是 `Size` 不是 `Count`（连接页设备行签名守卫曾用 `.Count` 导致连接页 tick 周期性 AttributeError）。`WaveMonitor` 的采样队列由引擎/通知线程写入、UI 线程按 tick 读取，内部有 `threading.Lock` 保护——无锁时 `window()` 迭代 deque 期间被 append 会抛 "deque mutated during iteration"，表现为日志页周期性出现「页面刷新失败」。气压折线仍为矢量 Polyline，按 `(长度, 最新值)` 签名去重后重建。

---

## 测试

```bat
.venv\Scripts\python -m unittest discover -s tests
```

覆盖：波形帧编解码与官方波形校验（郊狼 24 组 + 负鼠 20 组）、频率映射、V3 反馈消息解析（`+`/`-` 分隔）、V4 设备快照/深合并/hello 流程、**单边补丁序列回放**（props/slotState 交替不丢状态）、B0/B3/B2/BF 与官方文档示例逐字节比对、PWM/XYZ 打包、负鼠 B0/B3/B2 与灵猫 50/66/D0 气压解析、开火（定时/触发/波形恢复/强度恢复兜底/强度取值）、OSC 直选与步进波形、触发式开火参数、**Game Hub 兼容服务**（v1/v2 路由与 404 包络、`dgosc` 引擎快照、强度累加器与合并下发、开火、HTTP 真回环、META 字面量与生命周期），以及 **V4/V3 本地中继全链路回环**（中继启动 → 客户端接入 → 模拟 App 配对 → 强度/波形/事件双向路由 → 断开通知）。

* **OSC 测试必须用临时 UDP 端口**（`free_udp_port()`）：运行中的 exe 会占用 9001，固定端口会偶发绑定失败。Hub 服务同理用临时端口，且应用运行中 8920 被占用时相关用例 skipTest。
* 蓝牙测试用 `FakeBleakClient`（构造签名需兼容 `disconnected_callback` kwarg），`simulate_drop()` 模拟意外掉线验证重连标记。
* UI 验证用探针模式：`--selftest` 启动后由守护线程向 `App.window.ui_queue.put(check)` 注入检查闭包（逐页构建 + 每页 tick + 注入模拟 EngineState 验证控制页数值装配 + 可视化绑定写入 + 主题切换）。**自检引擎固定使用临时配置文件**（`App.engine_factory` 注入，`%TEMP%\dglab_osc_selftest_cfg.json`，每次运行前重建）——历史教训：探针曾直接操作用户 config.json，每次自检都把 SEL_1(bit0) 的绑定覆写成下拉第 1 项，表现为「绑定每次开启后被重置」。探针关闭窗口仍会 `save_config`，但写的是临时副本。另外 `Engine._disconnect_backend` 现在会补发一次空状态事件——否则断开后 `shell.state` 停留在最后一帧（二维码区不折叠、状态停在「等待扫码配对」）。
* `--selftest` 报告写 `%TEMP%\dglab_osc_selftest.json`，崩溃栈写 `%TEMP%\dglab_osc_crash.log`。

---

## 打包为 exe

已附 [DGLabOSC.spec](DGLabOSC.spec)（PyInstaller onedir 模式）：

```bat
.venv\Scripts\pip install pyinstaller
.venv\Scripts\python -m PyInstaller DGLabOSC.spec --noconfirm
```

产物在 `dist\DGLabOSC\`：`DGLabOSC.exe` + `_internal\` 依赖目录（约 60 MB），共约 114 个文件。分发时把整个文件夹拷走即可。

* 打包要点：win32more 在运行期按类名动态导入投影模块，spec 里已 `collect_submodules` 收集 `win32more.Microsoft`、`Windows.Foundation/Graphics/UI`、`winrt`、`bleak` 等；`win32more/dll/x64` 的 Bootstrap DLL 与 `winui3/app.xaml` 按原包路径打进 `_internal`；**`xaml/` 页面骨架**由 spec 的 `datas` 段打进 `_internal\xaml`，运行时由 `ui/paths.py` 从 `_MEIPASS` 解析（新增页面 XAML 后无需改 spec）。
* 运行时依赖：目标机器需安装 [Windows App Runtime](https://learn.microsoft.com/windows/apps/windows-app-sdk/downloads)（win32more 为框架依赖部署，Bootstrap DLL 负责引导；未装会弹官方提示或写入 `%TEMP%\dglab_osc_crash.log`）。
* `DGLabOSC.exe --selftest`：启动窗口 3 秒后自动关闭，把检查结果写入 `%TEMP%\dglab_osc_selftest.json`（退出码 0 表示通过），用于验证打包产物。
* 配置文件 `config.json` 生成在 exe 同目录；崩溃日志在 `%TEMP%\dglab_osc_crash.log`。
* 如需单文件 exe（onefile），把 spec 中 `COLLECT` 段删掉并给 `EXE` 传 `a.binaries, a.datas` 即可，但启动会变慢且自解压目录可能与 DLL 引导冲突，不推荐。

### 构建产物入库的代价（维护须知）

`dist/`（约 60 MB / 114 个文件）已纳入版本控制，便于直接分发。注意：

* **每次重新构建并提交都会向历史追加约 60 MB**，仓库体积单调增长且无法通过删除文件回收；
* GitHub 单文件硬上限 100 MB（当前最大文件 `DGLabOSC.exe` 约 11.9 MB，安全），但仓库总量建议控制在 1 GB 内；
* 更新 exe 时若无必要，不要反复提交整目录；长期建议改用 GitHub Releases 附件分发，仓库内只保留源码。

### 打包必须用 build_exe.py（不要直接跑 PyInstaller）

PyInstaller 的 `COLLECT --noconfirm` 会**清空整个 `dist/DGLabOSC`**，连带删除用户运行时文件：`config.json`（已保存设备、强度阈值）与 `dglab_osc.log`——此坑已两次吃掉用户配置。`build_exe.py`（仓库根目录）会在构建前备份 `config.json` + `dglab_osc.log`（`--drop-log` 可不保留日志），构建后自动恢复。产物分发时仍拷整个文件夹。

### 波形数据与许可（删除注释时必须保留的出处）

`dglab/official_waveforms.py` / `official_waveforms_ovc.py` 的波形数据**逐字移植自** DG-Lab 官方开源项目 [dglab-kit-python](https://github.com/dungeonlab-open/dglab-kit-python)（**GPL-3.0**），仅限非商用（DG-Lab 协议文档条款）。文件头注释已移除，出处以本条为准，勿删。

补充协议事实：帧 = 16 hex 字符 / 8 字节 `[freq1..4][strength1..4]`，每帧约 100 ms；频率字节 10-240 ↔ 逻辑频率 10-1000 分段映射（≤100 原值；≤600 → (v-100)/5+100；否则 → (v-600)/10+200）；OVC 帧前 4 字节固定 `0x0A` 保留、后 4 字节为振动强度 0-100。伪波形 key：`CONTINUOUS`（最低频+满强度段）、`SILENT`（零强度帧，会话保活无输出，默认波形）；V3 波形包络 1950 字符、100 帧上限；V3 客户端在服务器未自动分配 ID 时发送注册请求（部分社区中继需要）。

---

## 代码风格约定（注释清洗后生效）

* 源码与测试**不含任何注释与 docstring**（含 XAML 内注释、`# type: ignore` 指令）；命名、测试名与结构自解释。
* 本文件是**唯一**文档：新增协议事实、平台陷阱、真机结论时，**先更新这里再写代码**。
* `_research/` 目录不参与清洗：为协议研究材料、官方仓库快照与历史补丁脚本。

---

## 参考

主要参考（协议部分禁止商用，授权见官方仓库）：

* 官方协议仓库 [dungeonlab-open/dglab-bluetooth-protocol](https://github.com/dungeonlab-open/dglab-bluetooth-protocol)、[dglab-websocket-simple](https://github.com/dungeonlab-open/dglab-websocket-simple)（已作为 submodule 引入）
* 官方 SDK [dglab-kit](https://github.com/dungeonlab-open/dglab-kit)、[dglab-kit-python](https://github.com/dungeonlab-open/dglab-kit-python)（V4/V3 线协议与波形数据的权威实现）
* [VRChat OSC 文档](https://docs.vrchat.com/docs/osc-overview)、[头像参数](https://docs.vrchat.com/docs/osc-avatar-parameters)
* 游戏联动：[sllying/AliceInCradle_X_DGLAB](https://github.com/sllying/AliceInCradle_X_DGLAB)（本模组的行为参照，Game Hub 协议用法见 `_research/`）、[BepInEx 5 文档](https://docs.bepinex.dev/)
* [win32more](https://pypi.org/project/win32more/)（WinUI 3 Python 绑定）、[python-osc](https://pypi.org/project/python-osc/)、[bleak](https://pypi.org/project/bleak/)