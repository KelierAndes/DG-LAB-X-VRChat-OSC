"""表达式求值（dglab.expr）与共享映射引擎（dglab.mapping）回归测试。"""
from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dglab import expr
from dglab.mapping import MappingEngine, as_number


class ExprTests(unittest.TestCase):
    def test_basic_arith(self):
        vals = {"HP": 30.0, "HPmax": 100.0}
        self.assertAlmostEqual(expr.evaluate("{HP}/{HPmax}*200", vals), 60.0)
        self.assertAlmostEqual(expr.evaluate("({HP}+{HPmax})/2", vals), 65.0)
        self.assertAlmostEqual(expr.evaluate("-{HP}+10", vals), -20.0)
        self.assertAlmostEqual(expr.evaluate("2**3+{HP}%7", vals), 10.0)

    def test_user_example(self):
        # （输入，in_strength_a <- {Strength-max}*({HP}+{Hurt}/{HPmax}) 取整）
        vals = {"Strength": 120.0, "max": 200.0, "HP": 60.0,
                "Hurt": 12.0, "HPmax": 100.0}
        out = expr.eval_int("{Strength-max}*({HP}+{Hurt}/{HPmax})", vals, 0, 200)
        self.assertEqual(out, 0)   # 负值钳到 0
        vals["Strength"] = 300.0
        self.assertEqual(expr.eval_int("{Strength-max}*({HP}+{Hurt}/{HPmax})",
                                       vals, 0, 200), 200)

    def test_brace_subexpr(self):
        vals = {"a": 4.0, "b": 2.0}
        self.assertAlmostEqual(expr.evaluate("{a*b}+{a+b}", vals), 14.0)

    def test_unknown_var_is_zero(self):
        self.assertAlmostEqual(expr.evaluate("{nope}*5+2", {}), 2.0)

    def test_unknown_dotted_param_is_zero(self):
        # 核心输出参数 id 带点号：设备未接入（不在值表）时按 0，不报语法节点错误
        self.assertAlmostEqual(expr.evaluate("{COYOTE.StrengthA}+1", {}), 1.0)
        self.assertAlmostEqual(expr.evaluate("{COYOTE.2.Battery}", {}), 0.0)

    def test_fullwidth_normalize(self):
        self.assertAlmostEqual(
            expr.evaluate("（{a}＋{b}）／２", {"a": 6, "b": 4}), 5.0)

    def test_funcs(self):
        vals = {"a": 7.5, "b": 2.0}
        self.assertAlmostEqual(expr.evaluate("round({a})+max({a},{b})", vals), 15.5)
        self.assertAlmostEqual(expr.evaluate("abs(0-{a})+min(1,{b})", vals), 8.5)

    def test_errors(self):
        with self.assertRaises(expr.ExprError):
            expr.evaluate("{a}/0", {"a": 1})
        with self.assertRaises(expr.ExprError):
            expr.evaluate("", {})
        with self.assertRaises(expr.ExprError):
            expr.evaluate("{a", {"a": 1})
        with self.assertRaises(expr.ExprError):
            expr.evaluate("{a}+__import__('os')", {"a": 1})
        with self.assertRaises(expr.ExprError):
            expr.evaluate("9**99", {})

    def test_variables(self):
        self.assertEqual(expr.variables("{HP}/{HPmax}*200+{a}"),
                         {"HP", "HPmax", "a"})
        self.assertEqual(expr.variables("max(1,{x})"), {"x"})   # 函数名不算变量


class MappingEngineTests(unittest.TestCase):
    def setUp(self):
        self.sent: list[tuple[str, int]] = []
        self.engine = MappingEngine(
            lambda key, value: self.sent.append((key, value)),
            device_vars=lambda: {"StrengthA": 50, "LimitA": 200},
            ranges={"in_strength_a": (0, 200), "in_fire": (0, 1)},
            default_range=(0, 200))

    def test_signal_triggers_dispatch_on_change(self):
        self.engine.set_mappings({"in_strength_a": "{HP}/{HPmax}*200"})
        self.sent = []                          # set_mappings 首轮 pump 不算
        self.engine.signal("HP", 60)
        self.engine.signal("HPmax", 100)      # 60/100*200=120
        self.assertEqual(self.sent, [("in_strength_a", 120)])
        self.engine.signal("HP", 60)          # 同值不重复派发
        self.assertEqual(len(self.sent), 1)
        self.engine.signal("HP", 30)          # 60
        self.assertEqual(self.sent[-1], ("in_strength_a", 60))

    def test_device_vars_visible(self):
        self.engine.set_mappings({"in_strength_a": "{StrengthA}+{LimitA}"})
        self.engine.pump()
        self.assertEqual(self.sent, [("in_strength_a", 200)])   # 250 钳到 200

    def test_signal_overrides_device_var(self):
        self.engine.set_mappings({"in_strength_a": "{StrengthA}"})
        self.sent = []
        self.engine.signal("StrengthA", 9)
        self.assertEqual(self.sent, [("in_strength_a", 9)])

    def test_error_recorded_and_skips(self):
        self.engine.set_mappings({"in_strength_a": "{oops}/0"})
        self.engine.signal("oops", 5)
        self.assertEqual(self.sent, [])
        self.assertIn("in_strength_a", self.engine.errors)

    def test_empty_mapping_ignored(self):
        self.engine.set_mappings({"in_strength_a": "  ", "in_fire": "{x}"})
        self.assertEqual(list(self.engine.mappings), ["in_fire"])

    def test_bool_target_truthy_clamp(self):
        self.engine.set_mappings({"in_fire": "{flag}"})
        self.sent = []
        self.engine.signal("flag", 0.4)      # 非零即真（round(0.4)=0 会导致不生效）
        self.assertEqual(self.sent, [("in_fire", 1)])
        self.engine.signal("flag", 0)
        self.assertEqual(self.sent[-1], ("in_fire", 0))
        self.engine.signal("flag", 0.5)      # round(0.5)=0，同样必须归一为 1
        self.assertEqual(self.sent[-1], ("in_fire", 1))

    def test_reset(self):
        self.engine.set_mappings({"in_fire": "{b}"})
        self.sent = []
        self.engine.signal("b", 1)
        self.assertEqual(self.sent, [("in_fire", 1)])
        self.engine.reset()
        self.assertEqual(self.engine.signals, {})
        self.engine.signal("b", 1)            # 重置后重新派发
        self.assertEqual(self.sent[-1], ("in_fire", 1))

    def test_as_number(self):
        self.assertEqual(as_number(True), 1.0)
        self.assertEqual(as_number("2.5"), 2.5)
        self.assertIsNone(as_number("abc"))
        self.assertIsNone(as_number(None))


class DispatcherEdgeTests(unittest.TestCase):
    """fire / zap / 急停派发器的 0↔非零边沿记忆：重复派发不再重复动作。"""

    class _Api:
        def __init__(self):
            self.calls = []

        def run(self, coro):
            coro.close()

        def resolve_slot(self, family=""):
            return "s1"

        def fire_start(self, slot_id=None):
            self.calls.append(("fire", "start"))
            return _noop_coro()

        def fire_stop(self, slot_id=None):
            self.calls.append(("fire", "stop"))
            return _noop_coro()

        def zap(self, channel, seconds=1.0, slot_id=None):
            self.calls.append(("zap", channel))
            return _noop_coro()

        def emergency_stop(self):
            self.calls.append(("emergency",))
            return _noop_coro()

    def _actions(self):
        from dglab.params import build_dispatchers

        api = self._Api()
        return api, build_dispatchers(api)

    def test_fire_only_on_edges(self):
        api, actions = self._actions()
        actions["in_fire"](1)
        actions["in_fire"](1)
        actions["in_fire"](0)
        actions["in_fire"](0)
        self.assertEqual(api.calls, [("fire", "start"), ("fire", "stop")])

    def test_zap_and_emergency_dedupe(self):
        api, actions = self._actions()
        actions["in_zap_a"](1)
        actions["in_zap_a"](1)
        actions["in_emergency"](1)
        actions["in_emergency"](1)
        self.assertEqual(api.calls, [("zap", "A"), ("emergency",)])


async def _noop_coro():
    pass


if __name__ == "__main__":
    unittest.main()
