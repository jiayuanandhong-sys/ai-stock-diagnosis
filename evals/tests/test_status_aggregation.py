# -*- coding: utf-8 -*-
"""Bug 2 回归测试：案例五状态聚合（PASS/FAIL/PARTIAL/BLOCKED/NOT_RUN）。

聚合规则（断言默认必需，optional 须显式标注并披露）：
- 1 PASS + 1 NOT_RUN → PARTIAL；2 PASS + 1 NOT_RUN → PARTIAL；
- 全部必需断言 PASS → PASS；
- 任一必需断言 FAIL → FAIL（优先级最高，即使同时存在 NOT_RUN）；
- 全部必需断言 NOT_RUN → NOT_RUN；
- 案例未冻结（前置未决）→ BLOCKED，即使有 PASS 断言也不得伪装；
- optional 断言未执行/失败不影响案例状态，但必须在聚合明细中披露计数。
"""
import sys
import unittest
from pathlib import Path

TESTS = Path(__file__).resolve().parent
EVALS = TESTS.parent
sys.path.insert(0, str(EVALS / "runner"))

import run_evals as RE  # noqa: E402


def r(status, optional=False):
    return {"assertion": "a", "risk_level": "P1", "status": status,
            **({"optional": True} if optional else {})}


class TestAggregateStatus(unittest.TestCase):
    def test_one_pass_one_not_run_is_partial(self):
        st, c = RE.aggregate_status([r("PASS"), r("NOT_RUN")], frozen=True)
        self.assertEqual(st, "PARTIAL")
        self.assertEqual(c["required"]["NOT_RUN"], 1)

    def test_two_pass_one_not_run_is_partial(self):
        st, _ = RE.aggregate_status([r("PASS"), r("PASS"), r("NOT_RUN")], frozen=True)
        self.assertEqual(st, "PARTIAL")

    def test_all_required_pass_is_pass(self):
        st, c = RE.aggregate_status([r("PASS"), r("PASS")], frozen=True)
        self.assertEqual(st, "PASS")

    def test_any_required_fail_is_fail(self):
        st, _ = RE.aggregate_status([r("PASS"), r("FAIL")], frozen=True)
        self.assertEqual(st, "FAIL")

    def test_fail_beats_not_run(self):
        """FAIL 优先级最高：PASS+FAIL+NOT_RUN → FAIL（确定性聚合）。"""
        st, _ = RE.aggregate_status([r("PASS"), r("FAIL"), r("NOT_RUN")], frozen=True)
        self.assertEqual(st, "FAIL")

    def test_all_not_run_is_not_run(self):
        st, _ = RE.aggregate_status([r("NOT_RUN"), r("NOT_RUN")], frozen=True)
        self.assertEqual(st, "NOT_RUN")

    def test_no_required_assertions_executed_is_not_run(self):
        st, _ = RE.aggregate_status([], frozen=True)
        self.assertEqual(st, "NOT_RUN")

    def test_not_frozen_is_blocked_even_with_pass(self):
        """未冻结（前置未决）→ BLOCKED：即使断言有 PASS 也不得伪装。"""
        st, _ = RE.aggregate_status([r("PASS"), r("NOT_RUN")], frozen=False)
        self.assertEqual(st, "BLOCKED")

    def test_optional_not_run_keeps_pass_with_disclosure(self):
        """optional 断言未执行：必需全 PASS → PASS，但披露 optional NOT_RUN 计数。"""
        st, c = RE.aggregate_status([r("PASS"), r("NOT_RUN", optional=True)], frozen=True)
        self.assertEqual(st, "PASS")
        self.assertEqual(c["optional"]["NOT_RUN"], 1)

    def test_optional_fail_does_not_fail_case(self):
        """optional 断言失败不影响案例状态（仍 PASS），但披露 optional FAIL 计数。"""
        st, c = RE.aggregate_status([r("PASS"), r("FAIL", optional=True)], frozen=True)
        self.assertEqual(st, "PASS")
        self.assertEqual(c["optional"]["FAIL"], 1)

    def test_required_fail_with_optional_pass_is_fail(self):
        st, _ = RE.aggregate_status([r("FAIL"), r("PASS", optional=True)], frozen=True)
        self.assertEqual(st, "FAIL")

    def test_deterministic_same_input_same_output(self):
        """同一输入多次聚合结果一致（确定性）。"""
        inp = [r("PASS"), r("NOT_RUN"), r("FAIL", optional=True)]
        outs = [RE.aggregate_status(inp, True) for _ in range(5)]
        self.assertEqual(len({repr(o) for o in outs}), 1)


class TestExitCodeConvention(unittest.TestCase):
    """退出码约定：0 全部必需断言通过；1 存在实际失败；2 门禁失败或未完整验收。"""

    def test_exit_code_from_summary(self):
        f = RE.exit_code_from_summary
        self.assertEqual(f({"FAIL": 0, "PARTIAL": 0, "BLOCKED": 0, "NOT_RUN": 0}), 0)
        self.assertEqual(f({"FAIL": 1, "PARTIAL": 0, "BLOCKED": 0, "NOT_RUN": 0}), 1)
        self.assertEqual(f({"FAIL": 0, "PARTIAL": 3, "BLOCKED": 0, "NOT_RUN": 0}), 2)
        self.assertEqual(f({"FAIL": 0, "PARTIAL": 0, "BLOCKED": 8, "NOT_RUN": 5}), 2)
        self.assertEqual(f({"FAIL": 2, "PARTIAL": 3, "BLOCKED": 1, "NOT_RUN": 1}), 1,
                         "实际失败优先于其他未完整状态")


if __name__ == "__main__":
    unittest.main(verbosity=2)
