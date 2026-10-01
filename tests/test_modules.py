"""模块宿主（plugins.py）与强度参数公开 API 回归测试。"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from plugins import ModuleBase, ModuleContext, PluginManager


class _FakeConfig(dict):
    def __init__(self, path):
        super().__init__()
        self.path = path
        self.saved = False

    def save(self):
        self.saved = True


class _FakeEngine:
    def __init__(self):
        from dglab.state import StateEvents

        self.events = StateEvents()
        # 每个实例独立临时目录，避免测试间共享 config/ 造成串扰
        self.config = _FakeConfig(
            os.path.join(tempfile.mkdtemp(prefix="dgstudio_test_"), "config.json"))
        self._logs: list[str] = []

    def _log(self, msg: str) -> None:
        self._logs.append(msg)

    def submit(self, coro):
        import asyncio

        return asyncio.run_coroutine_threadsafe(coro, asyncio.new_event_loop())


class ModuleDiscoveryTests(unittest.TestCase):
    def setUp(self):
        self.engine = _FakeEngine()
        self.manager = PluginManager(self.engine)
        self.engine.modules = self.manager

    def test_builtin_modules_discovered(self):
        ids = {m["id"] for m in self.manager.list_modules()}
        self.assertIn("osc_bridge", ids)
        self.assertIn("strength_logger", ids)

    def test_meta_has_name_and_version(self):
        meta = self.manager.meta("osc_bridge")
        self.assertEqual(meta["name"], "VRChat OSC 联动")
        self.assertTrue(meta["version"])
        self.assertFalse(meta["loaded"])

    def test_load_osc_module(self):
        instance = self.manager.load("osc_bridge")
        self.assertIsInstance(instance, ModuleBase)
        self.assertEqual(instance.id, "osc_bridge")
        self.assertTrue(self.manager.meta("osc_bridge")["loaded"])
        # on_load 回填模块设置文件默认值
        self.assertEqual(self.manager.settings_for("osc_bridge").get("out_port"), 9000)

    def test_load_duck_typed_module(self):
        instance = self.manager.load("strength_logger")
        self.assertIsNotNone(instance)
        self.assertEqual(instance.id, "strength_logger")

    def test_unknown_module_raises(self):
        with self.assertRaises(RuntimeError):
            self.manager.load("no_such_module")

    def test_enable_persist_split(self):
        self.manager.set_enabled("m1", True)
        self.assertTrue(self.manager._modules_state["enabled"]["m1"])
        self.assertTrue(self.manager.is_enabled("m1"))
        self.manager.set_enabled("m1", False)
        self.assertFalse(self.manager.is_enabled("m1"))

    def test_osc_default_enabled_from_meta(self):
        self.assertTrue(self.manager.is_enabled("osc_bridge"))
        self.assertFalse(self.manager.is_enabled("strength_logger"))
        self.manager.set_enabled("osc_bridge", False)
        self.assertFalse(self.manager.is_enabled("osc_bridge"))

    def test_list_meta_enabled_reflects_default(self):
        # discover 缓存的 enabled 必须回落到 META default_enabled，
        # 否则未显式记录的模块在模块页显示「已停用」
        self.assertTrue(self.manager.meta("osc_bridge")["enabled"])
        self.assertTrue(self.manager.meta("config_init")["enabled"])
        self.assertFalse(self.manager.meta("strength_logger")["enabled"])
        self.manager.set_enabled("osc_bridge", False)
        self.assertFalse(self.manager.meta("osc_bridge")["enabled"])

    def test_migration_from_main_config(self):
        engine = _FakeEngine()
        engine.config.update({
            "osc": {"enabled": False, "out_port": 9100, "in_strength_a": "X"},
            "modules": {"enabled": {"m1": True}, "settings": {"m1": {"k": 1}}},
        })
        manager = PluginManager(engine)
        self.assertNotIn("osc", engine.config)
        self.assertNotIn("modules", engine.config)
        self.assertTrue(engine.config.saved)
        self.assertFalse(manager.is_enabled("osc_bridge"))  # 旧 enabled=False → 显式关闭
        osc = manager.settings_for("osc_bridge")
        self.assertEqual(osc.get("out_port"), 9100)
        self.assertNotIn("enabled", osc)
        self.assertTrue(manager.is_enabled("m1"))
        self.assertEqual(manager.settings_for("m1").get("k"), 1)

    def test_register_instance_injection(self):
        class Fake:
            id = ""

            def is_running(self):
                return True

        fake = Fake()
        self.manager.register_instance("osc_bridge", fake)
        self.assertIs(self.manager.instance("osc_bridge"), fake)
        self.assertEqual(fake.id, "osc_bridge")
        self.manager.register_instance("osc_bridge", None)
        self.assertIsNone(self.manager.instance("osc_bridge"))


class ModuleContextApiTests(unittest.TestCase):
    def test_context_exposes_public_api(self):
        import app as app_module

        engine = app_module.Engine(
            config_path=os.path.join(tempfile.gettempdir(), "dgstudio_test_ctx.json"))
        try:
            ctx = ModuleContext(engine, _ProbeModule())
            self.assertEqual(ctx.wave_order("OVC")[0], "__SILENT__")
            self.assertEqual(ctx.wave_selection(), {"A": "__SILENT__", "B": "__SILENT__"})
            self.assertIn("strength_step", ctx.intensity_params())
            ctx.settings["probe_key"] = 7
            self.assertEqual(engine.modules.settings_for("probe").get("probe_key"), 7)
            with self.assertRaises(ValueError):
                ctx.set_intensity_param("nope", 1)
            ctx.set_intensity_param("max_strength", 180)
            self.assertEqual(ctx.intensity_params()["max_strength"], 180)
        finally:
            engine.stop()


class _ProbeModule(ModuleBase):
    id = "probe"
    name = "probe"


class ModuleButtonActionTests(unittest.TestCase):
    def setUp(self):
        self.engine = _FakeEngine()
        self.manager = PluginManager(self.engine)
        self.engine.modules = self.manager

    def test_osc_action_registered_on_load(self):
        self.manager.load("osc_bridge")
        actions = self.manager.button_actions()
        self.assertEqual([a.key for a in actions], ["osc"])
        self.assertEqual(actions[0].owner, "osc_bridge")
        self.assertTrue(actions[0].label)
        self.assertIsNotNone(actions[0].on_press)
        self.assertIsNotNone(actions[0].on_release)

    def test_unload_removes_actions(self):
        self.manager.load("osc_bridge")
        self.assertIsNotNone(self.manager.action("osc"))
        import asyncio

        asyncio.run(self.manager.unload("osc_bridge"))
        self.assertIsNone(self.manager.action("osc"))
        self.assertEqual(self.manager.button_actions(), [])

    def test_module_for_action_via_meta_without_load(self):
        self.assertEqual(self.manager.module_for_action("osc"), "osc_bridge")
        self.assertIsNone(self.manager.module_for_action("nope"))

    def test_duplicate_action_key_ignored(self):
        first = self.manager.load("osc_bridge")
        from plugins import ButtonAction

        second = self.manager.load("strength_logger")  # 无动作，占位验证注册表稳定
        self.manager._register_actions(
            "strength_logger",
            type("M", (), {"button_actions": lambda self: [
                ButtonAction("osc", "重复项")]})())
        self.assertIs(self.manager.action("osc").owner, "osc_bridge")
        self.assertEqual(len(self.manager.button_actions()), 1)


class BindingProfileCheckTests(unittest.TestCase):
    def setUp(self):
        import os
        import tempfile

        import app as app_module

        path = os.path.join(tempfile.gettempdir(), "dgstudio_test_bindings.json")
        if os.path.exists(path):
            os.remove(path)
        self.engine = app_module.Engine(config_path=path)

    def tearDown(self):
        self.engine.stop()

    def test_missing_detection_without_loaded_module(self):
        missing = self.engine.binding_missing_modules({
            "13": "osc:/avatar/parameters/X",
            "12": "key:F1",
            "15": "fire",
            "14": "none",
        })
        self.assertEqual(missing, {"13": "osc:/avatar/parameters/X"})
        self.assertEqual(self.engine.modules_for_bindings(missing), ["osc_bridge"])

    def test_no_missing_after_module_load(self):
        self.engine.modules.load("osc_bridge")
        missing = self.engine.binding_missing_modules({"13": "osc:/avatar/X"})
        self.assertEqual(missing, {})

    def test_unload_triggers_missing_detection(self):
        import asyncio

        self.engine.start()
        try:
            ble = self.engine.config["ble"]
            ble["ovc_profiles"] = {"默认": {"13": "osc:/avatar/X"}}
            ble["ovc_profile"] = "默认"
            events = []
            self.engine.events.on("binding_modules_missing",
                                  lambda payload: events.append(payload))
            self.engine.modules.load("osc_bridge")
            self.assertEqual(events, [])  # 加载只会消除缺失，不触发提示
            fut = asyncio.run_coroutine_threadsafe(
                self.engine.modules.unload("osc_bridge"), self.engine.loop)
            fut.result(timeout=10)
            self.assertEqual(len(events), 1)
            self.assertEqual(events[0]["modules"], ["osc_bridge"])
            self.assertEqual(events[0]["bindings"], {"13": "osc:/avatar/X"})
        finally:
            self.engine.stop()

    def test_reset_bindings_clears_bits(self):
        self.engine.config["ble"]["ovc_profiles"] = {"默认": {"13": "osc:/x",
                                                             "15": "fire"}}
        self.engine.config["ble"]["ovc_profile"] = "默认"
        self.engine.reset_bindings(["13"])
        self.assertEqual(self.engine.config["ble"]["ovc_profiles"]["默认"]["13"],
                         "none")
        self.assertEqual(self.engine.config["ble"]["ovc_profiles"]["默认"]["15"],
                         "fire")

    def test_rename_profile(self):
        ble = self.engine.config["ble"]
        ble["ovc_profiles"] = {"默认": {"13": "fire"}, "配置1": {"15": "estop"}}
        ble["ovc_profile"] = "配置1"
        self.assertIsNone(self.engine.rename_ovc_profile("配置1", "急停方案"))
        self.assertEqual(list(ble["ovc_profiles"]), ["默认", "急停方案"])
        self.assertEqual(ble["ovc_profiles"]["急停方案"], {"15": "estop"})
        self.assertEqual(ble["ovc_profile"], "急停方案")
        self.assertIn("已存在", self.engine.rename_ovc_profile("急停方案", "默认"))
        self.assertIn("不存在", self.engine.rename_ovc_profile("缺失", "x"))
        self.assertIsNone(self.engine.rename_ovc_profile("急停方案", "急停方案"))
        self.assertEqual(self.engine.rename_ovc_profile("急停方案", "  "),
                         "名称不能为空")


class ConfigDrivenTests(unittest.TestCase):
    """配置声明自动装载与自定义输入映射回归。"""

    def setUp(self):
        self.engine = _FakeEngine()
        self.manager = PluginManager(self.engine)
        self.engine.modules = self.manager

    def test_declared_defaults_auto_filled_on_discover(self):
        osc = self.manager.settings_for("osc_bridge")
        for key in ("out_ip", "rate_hz", "device_prefixes", "mappings",
                    "outputs"):
            self.assertIn(key, osc)
        self.assertEqual(osc["rate_hz"], 10)
        self.assertEqual(osc["mappings"], [])
        self.assertEqual(osc["outputs"], [])
        self.assertEqual(osc["device_prefixes"]["OVC"], "DGLabOvc")
        # 旧版逐参数名 / custom_inputs 不再是配置项
        self.assertNotIn("in_strength_a", osc)
        self.assertNotIn("custom_inputs", osc)
        path = os.path.join(self.manager.config_dir, "osc.json")
        self.assertTrue(os.path.isfile(path))

    def test_existing_values_not_overwritten(self):
        osc = self.manager.settings_for("osc_bridge")
        osc["rate_hz"] = 25
        osc.save()
        self.manager._settings_cache.clear()
        self.assertEqual(self.manager.settings_for("osc_bridge")["rate_hz"], 25)

    def test_alice_cradle_auto_filled(self):
        ac = self.manager.settings_for("alice_cradle")
        self.assertEqual(ac["port"], 8920)
        self.assertNotIn("family", ac)

    def test_config_spec_via_meta_without_load(self):
        spec = self.manager.config_spec_for("osc_bridge")
        self.assertEqual(spec["rate_hz"]["max"], 30)
        self.assertEqual(spec["mappings"]["type"], "list")
        self.assertEqual(spec["mappings"]["group"], "map")
        self.assertEqual(spec["mappings"]["rows"], "in")
        self.assertEqual(spec["outputs"]["rows"], "out")
        # 核心参数不再逐项出现在配置声明里
        self.assertNotIn("in_strength_a", spec)

    def test_dynamic_input_param_drives_core_dispatch(self):
        # OSC 头像参数动态建表：收到同名参数即进入信号空间参与运算
        from modules.osc_bridge.bridge import OscBridge, OscConfig
        from modules.osc_bridge.plugin import OSC_CONFIG_DEFAULTS

        cfg = OscConfig(
            {"mappings": [{"param": "in_strength_a", "expr": "{blood}"}]},
            defaults=OSC_CONFIG_DEFAULTS)
        bridge = OscBridge(cfg, lambda: None, None)
        try:
            bridge._track_input("/avatar/parameters/blood", 120)
            self.assertIn("blood", bridge.param_names())
            self.assertEqual(bridge.engine.last_values["in_strength_a"], 120)
        finally:
            bridge.close()

    def test_output_rows_rename_wins(self):
        # 输出表默认行：旧 output_map 的重命名优先，其余用前缀 + 信号名
        from dglab.state import EngineState, Slot
        from modules.osc_bridge.bridge import default_output_rows

        state = EngineState(connected=True, paired=True, slots={
            "1": Slot(slot_id="1", name="Coyote", type="COYOTE",
                      strength={"A": 80, "B": 0}, battery=66)})
        cfg = {"prefix": "DGLab", "device_prefixes": {"COYOTE": "DGLab"},
               "output_map": {"COYOTE.StrengthA": "myStrength",
                              "Action": "Btn"}}
        rows = default_output_rows(cfg, state)
        by = {row["param"]: row for row in rows}
        self.assertEqual(by["COYOTE.StrengthA"]["name"], "myStrength")
        self.assertEqual(by["COYOTE.StrengthB"]["name"], "DGLabStrengthB")
        self.assertEqual(by["COYOTE.Battery"]["name"], "DGLabBattery")
        self.assertEqual(by["Action"]["name"], "Btn")

    def test_expression_mixes_device_vars(self):
        # 用户口径示例：in_strength_a <- {Strength-max}*({HP}+{Hurt}/{HPmax})
        from dglab.state import EngineState, Slot
        from modules.osc_bridge.bridge import OscBridge, OscConfig
        from modules.osc_bridge.plugin import OSC_CONFIG_DEFAULTS

        state = EngineState(connected=True, paired=True, slots={
            "1": Slot(slot_id="1", type="COYOTE",
                      strength={"A": 300, "B": 0},
                      strength_limit={"A": 200, "B": 200})})
        cfg = OscConfig(
            {"mappings": [{"param": "in_strength_a",
                           "expr": "{Strength-max}*({HP}+{Hurt}/{HPmax})"}]},
            defaults=OSC_CONFIG_DEFAULTS)
        bridge = OscBridge(cfg, lambda: state, None)
        try:
            bridge.engine.signal("HP", 60)
            bridge.engine.signal("Hurt", 30)
            bridge.engine.signal("HPmax", 100)
            # (300-200)*(60+0.3)=6030 → 钳到 200
            self.assertEqual(bridge.engine.last_values["in_strength_a"], 200)
        finally:
            bridge.close()


class ConfigInitModuleTests(unittest.TestCase):
    """初始化配置模块：导出包 / 载入恢复。"""

    def setUp(self):
        self.engine = _FakeEngine()
        self.manager = PluginManager(self.engine)
        self.engine.modules = self.manager
        from modules.config_init.plugin import ConfigInitModule
        from plugins import ModuleContext

        self.inst = ConfigInitModule()
        self.ctx = ModuleContext(self.engine, self.inst)
        self.inst.on_load(self.ctx)

    def _tmp(self, name: str) -> str:
        import os
        import tempfile

        return os.path.join(tempfile.mkdtemp(), name)

    def test_export_bundle_contains_files(self):
        import json

        path = self._tmp("export.json")
        count = self.inst.export_to(path)
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        self.assertEqual(data["__bundle__"], "dgstudio-config-bundle")
        self.assertIn("config.json", data["files"])
        self.assertIn("config/osc.json", data["files"])
        self.assertGreaterEqual(count, 2)

    def test_load_bundle_restores_values(self):
        osc = self.manager.settings_for("osc_bridge")
        path = self._tmp("export.json")
        self.inst.export_to(path)
        osc["out_port"] = 9999
        applied = self.inst.load_from(path)
        self.assertGreaterEqual(applied, 1)
        self.assertEqual(self.manager.settings_for("osc_bridge")["out_port"], 9000)

    def test_load_plain_config_merges_main(self):
        import json

        path = self._tmp("plain.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump({"max_strength": 120}, f)
        self.inst.load_from(path)
        self.assertEqual(self.engine.config["max_strength"], 120)
        self.assertTrue(self.engine.config.saved)

    def test_load_plain_config_then_spec_refill(self):
        import json

        path = self._tmp("partial_osc.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump({"out_port": 9100}, f)
        # 作为主配置载入后，模块声明缺省仍在（osc 文件未被该文件覆盖）
        self.inst.load_from(path)
        self.assertEqual(self.engine.config["out_port"], 9100)
        self.assertEqual(self.manager.settings_for("osc_bridge")["rate_hz"], 10)


class GameModTests(unittest.TestCase):
    """模块携带游戏端模组：META 声明、mods/ 目录与一键释放安装。"""

    def setUp(self):
        self.engine = _FakeEngine()
        self.manager = PluginManager(self.engine)
        self.engine.modules = self.manager

    def test_meta_declares_game_mod(self):
        meta = self.manager.meta("alice_cradle")
        self.assertEqual(meta["mods"]["dest"],
                         "BepInEx/plugins/AliceInCradleLink")
        self.assertEqual(meta["mods"]["marker"], "AliceInCradle.exe")
        self.assertIsNone(self.manager.module_mods_dir("osc_bridge"))

    def test_module_mods_dir_carries_dll(self):
        mods = self.manager.module_mods_dir("alice_cradle")
        self.assertTrue(mods)
        self.assertTrue(os.path.isfile(
            os.path.join(mods, "AliceInCradleLink.dll")))

    def test_install_game_mod_to_bepinex_root(self):
        root = tempfile.mkdtemp(prefix="dgstudio_game_")
        os.makedirs(os.path.join(root, "BepInEx", "plugins"))
        count = self.manager.install_game_mod("alice_cradle", root)
        self.assertGreaterEqual(count, 1)
        self.assertTrue(os.path.isfile(os.path.join(
            root, "BepInEx", "plugins", "AliceInCradleLink",
            "AliceInCradleLink.dll")))

    def test_install_game_mod_rejects_non_bepinex_root(self):
        root = tempfile.mkdtemp(prefix="dgstudio_game_")
        with self.assertRaises(ValueError):
            self.manager.install_game_mod("alice_cradle", root)

    def test_scan_game_roots_finds_marker(self):
        root = tempfile.mkdtemp(prefix="dgstudio_game_")
        game = os.path.join(root, "Download", "Game Dir", "GameRoot")
        os.makedirs(game)
        with open(os.path.join(game, "AliceInCradle.exe"), "wb"):
            pass
        found = self.manager.scan_game_roots("aliceincradle.exe",
                                             roots=[root], max_depth=4)
        self.assertEqual(
            [os.path.normcase(os.path.realpath(g)) for g in found],
            [os.path.normcase(os.path.realpath(game))])


if __name__ == "__main__":
    unittest.main()
