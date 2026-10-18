# -*- coding: utf-8 -*-
"""PEG 检查器单元测试（unittest，仅标准库）。

验证目标（Phase 1 退出条件）：
1. 已知正确样本 kg1（OPEN-E01 定案 rank/N 后的 L3-010，F1=62.5/62.5/25/0）全门 PASS；
2. 每个已知错误样本被目标门准确拦截（kb1/kb2 数学错误 = 本轮第二轮审查确认的真实历史错误；
   kb1 的 66.67 在 rank/N 定案口径下仍为错误值，正确=62.5）；
3. PEG-3 的 raw/display 双精度校验不会因舍入误报；
4. BLOCKED 状态强制 blocked_reason + truth_origin=pending_pm_decision。
"""
from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import peg_checker as pc  # noqa: E402

SPEC = Path("C:/Users/28309/WorkBuddy/2026-10-17-14-16-08/sdd-stock-diagnosis/spec.md")
TASKS = Path("C:/Users/28309/WorkBuddy/2026-10-17-14-16-08/sdd-stock-diagnosis/tasks.md")


def load_one(p: Path) -> dict:
    return json.loads(p.read_text(encoding="utf-8").strip())


class TestKnownGood(unittest.TestCase):
    def test_kg1_all_gates_pass(self):
        """v0.2 修正后的 L3-010 应通过全部单案例门（PEG-5 全局覆盖另测）。"""
        c = load_one(HERE / "known_good" / "kg1_l3_010_v02.jsonl")
        self.assertEqual(pc.peg1_structure(c), [])
        self.assertEqual(pc.peg2_fields(c), [])
        errs, mode = pc.peg3_math(c)
        self.assertEqual((errs, mode), ([], "RUN"))
        self.assertEqual(pc.peg4_truth(c), [])
        self.assertEqual(pc.peg6_refs(c, [HERE]), [])

    def test_display_precision_no_false_alarm(self):
        """修正 #7：raw=83.3333…/display=83.33 双精度不得因舍入误报。"""
        c = load_one(HERE / "known_good" / "kg1_l3_010_v02.jsonl")
        errs, _ = pc.peg3_math(c)
        self.assertEqual(errs, [], "修正后的双精度标准答案不应产生任何 PEG-3 误报")


class TestKnownBad(unittest.TestCase):
    def test_kb1_v01_l3_010_math_error_caught(self):
        """v0.1 历史错误 66.67（rank/N 定案后应为 62.5）必须被 PEG-3 拦截。"""
        c = load_one(HERE / "known_bad" / "kb1_l3_010_v01_math.jsonl")
        errs, mode = pc.peg3_math(c)
        self.assertEqual(mode, "RUN")
        self.assertTrue(any("pct_from_ranks" in e for e in errs),
                        f"PEG-3 未拦截并列名次错误: {errs}")

    def test_kb2_v01_l4_017_side_mean_error_caught(self):
        """v0.1 历史错误（估值均值 65.1，复算 61.0）必须被 PEG-3 拦截。"""
        c = load_one(HERE / "known_bad" / "kb2_l4_017_v01_math.jsonl")
        errs, mode = pc.peg3_math(c)
        self.assertEqual(mode, "RUN")
        self.assertTrue(any("side_mean" in e for e in errs),
                        f"PEG-3 未拦截侧面均值错误: {errs}")

    def test_kb3_missing_field(self):
        c = load_one(HERE / "known_bad" / "kb3_missing_field.jsonl")
        errs = pc.peg1_structure(c)
        self.assertTrue(any("evaluator_type" in e for e in errs))

    def test_kb4_bad_split_enum(self):
        c = load_one(HERE / "known_bad" / "kb4_bad_split_enum.jsonl")
        errs = pc.peg1_structure(c)
        self.assertTrue(any("split" in e for e in errs))

    def test_kb5_truth_violation(self):
        c = load_one(HERE / "known_bad" / "kb5_truth_violation.jsonl")
        errs = pc.peg4_truth(c)
        self.assertTrue(any("违规表述" in e for e in errs))

    def test_kb6_missing_ref(self):
        c = load_one(HERE / "known_bad" / "kb6_missing_ref.jsonl")
        errs = pc.peg6_refs(c, [HERE])
        self.assertTrue(any("不存在" in e for e in errs))

    def test_kb7_risk_mismatch(self):
        c = load_one(HERE / "known_bad" / "kb7_risk_mismatch.jsonl")
        errs = pc.peg2_fields(c)
        self.assertTrue(any("不一致" in e for e in errs))


class TestPeg3Semantics(unittest.TestCase):
    def test_band_boundaries_br007(self):
        """BR-007 边界：恰60灰、60.01红、恰40灰、39.99绿、None灰。"""
        for score, want in [(60, "gray"), (60.01, "red"), (40, "gray"),
                            (39.99, "green"), (None, "gray"), (59.99, "gray")]:
            errs, mode = pc.peg3_math({"status": "FROZEN", "peg3_checks": [
                {"check": "band_of", "score": score, "expected_color": want}]})
            self.assertEqual((errs, mode), ([], "RUN"), f"score={score} 应为 {want}")

    def test_blocked_case_skips_peg3_and_requires_pending_truth(self):
        c = {"status": "BLOCKED", "peg3_checks": [{"check": "band_of", "score": 61,
                                                   "expected_color": "red"}]}
        errs, mode = pc.peg3_math(c)
        self.assertEqual(mode, "SKIP")
        self.assertEqual(errs, [])
        self.assertTrue(pc.peg4_truth(c), "BLOCKED 案例必须使用 pending_pm_decision 真值来源")

    def test_peg5_unknown_requirement_and_coverage(self):
        c = {"case_id": "L3-094", "requirement_id": ["BR-999", "AC-001"],
             "input": {"code": "603799", "expected_pool": "沪深300"}}
        ids = pc._load_ids(SPEC, TASKS)
        errs = pc.peg5_traceability([c], ids, coverage_ac=False)
        self.assertTrue(any("BR-999" in e for e in errs))
        errs_cov = pc.peg5_traceability([c], ids, coverage_ac=True)
        self.assertTrue(any("无任何案例覆盖" in e for e in errs_cov))


class TestEndToEnd(unittest.TestCase):
    def test_run_peg_overall_fail_on_known_bad(self):
        rep = pc.run_peg([HERE / "known_bad" / "kb1_l3_010_v01_math.jsonl"],
                         SPEC, TASKS, base_dirs=[HERE], coverage_ac=False)
        self.assertEqual(rep["overall"], "FAIL")
        self.assertEqual(rep["cases"]["L3-010"]["PEG-3"]["status"], "FAIL")

    def test_run_peg_pass_on_known_good_without_coverage(self):
        rep = pc.run_peg([HERE / "known_good" / "kg1_l3_010_v02.jsonl"],
                         SPEC, TASKS, base_dirs=[HERE], coverage_ac=False)
        self.assertEqual(rep["overall"], "PASS", json.dumps(rep["cases"], ensure_ascii=False))


class TestV11VolRatioAndPmDecisionTier(unittest.TestCase):
    """v1.1.0 新增：vol_ratio 检查类（OPEN-E09）与 pm_decision 来源层级（Schema v1.1）。"""

    def test_vol_ratio_correct_passes(self):
        case = {"status": "FROZEN", "peg3_checks": [
            {"check": "vol_ratio",
             "params": {"series": [1e6] * 25 + [5e6] * 5},   # 5M / 2M = 2.5
             "expected": {"raw": 2.5, "display": 2.5, "dp": 2}}]}
        self.assertEqual(pc.peg3_math(case)[0], [])

    def test_vol_ratio_wrong_value_fails(self):
        case = {"status": "FROZEN", "peg3_checks": [
            {"check": "vol_ratio",
             "params": {"series": [1e6] * 25 + [5e6] * 5},
             "expected": {"raw": 2.0, "display": 2.0, "dp": 2}}]}   # 2.5 ≠ 2.0
        errs, _ = pc.peg3_math(case)
        self.assertTrue(any("vol_ratio" in e for e in errs))

    def test_vol_ratio_insufficient_series_raises_to_fail(self):
        case = {"status": "FROZEN", "peg3_checks": [
            {"check": "vol_ratio",
             "params": {"series": [1e6] * 19},
             "expected": {"raw": 2.5, "display": 2.5, "dp": 2}}]}
        errs, _ = pc.peg3_math(case)
        self.assertTrue(any("vol_ratio" in e and "重算异常" in e for e in errs))

    def test_pm_decision_tier_accepted(self):
        """Schema v1.1：source_tier=pm_decision 合法（OPEN-E09 类 PM 裁决真值来源）。"""
        case = {"status": "FROZEN", "peg3_checks": [],
                "assertions": [{"type": "x", "risk_level": "P1",
                                "source_tier": "pm_decision"}]}
        errs = pc.peg1_structure(case)
        self.assertEqual([e for e in errs if "source_tier" in e], [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
