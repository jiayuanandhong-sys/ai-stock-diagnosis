# -*- coding: utf-8 -*-
"""T-06 解读接口用例集：
DoD-1 接口冻结；DoD-2 未配置自动降级模板不中断；
DoD-3 硬门禁链路（注入禁词 → FAIL → 重试 1 次 → 仍 FAIL → 回退模板 → 最终 PASS）；
DoD-4 模板输出与 Spike-2 文风一致。
"""

import json
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from src.compliance import scan
from src.interpretation import (DiagnosisContext, TemplateInterpreter,
                                build_interpreter, interpret_with_hard_gate)
from src.interpretation.factory import interpret_with_hard_gate as gate
from src.interpretation.llm_impl import LLMInterpreter

os.environ.pop("SDD_LLM_API_KEY", None)          # 确保测试环境无 LLM 配置


def make_ctx() -> DiagnosisContext:
    return DiagnosisContext(
        code="603799", name="华友钴业", pool="沪深300",
        observe_date="2026-10-16", S=10.0, H=17.3, rank_in_pool=19, pool_size=20,
        side_scores={"估值特征": 15.0, "价格行为": 5.0, "交易行为": 20.0, "风险特征": 8.0},
        factor_scores={"F1": 15.0, "F2": 20.0, "F18": 8.0},
        factor_labels={"F1": "估值·定价水平", "F2": "估值·资产口径", "F18": "日内振幅"},
        top_positives=["交易活跃度", "量能扩张度"],
        top_negatives=["短周期价格动量", "区间回撤深度"],
        week_change=-0.073, review_date="2026-10-23",
    )


def make_llm(responses):
    """构造注入式 fake LLM：responses 为函数列表（每次调用取下一个）。"""
    calls = {"n": 0}

    def fake_post(url, payload, headers, timeout=60):
        i = min(calls["n"], len(responses) - 1)
        calls["n"] += 1
        r = responses[i]
        if isinstance(r, Exception):
            raise r
        return json.dumps({"choices": [{"message": {"content": r}}]})

    llm = LLMInterpreter(api_key="test-key", http_post=fake_post)
    llm.calls = calls          # type: ignore[attr-defined]
    return llm


class TestInterface(unittest.TestCase):
    """DoD-1：接口定义冻结。"""

    def test_context_fields(self):
        ctx = make_ctx()
        for f in ["code", "name", "pool", "observe_date", "S", "H", "rank_in_pool",
                  "pool_size", "side_scores", "factor_scores", "factor_labels",
                  "top_positives", "top_negatives", "week_change", "review_date"]:
            self.assertTrue(hasattr(ctx, f), f"DiagnosisContext 缺字段 {f}")

    def test_result_fields(self):
        from src.interpretation import InterpretationResult
        r = InterpretationResult(text="x", generator="template", scanned=True)
        self.assertEqual((r.text, r.generator, r.scanned), ("x", "template", True))


class TestFactory(unittest.TestCase):
    """DoD-2：未配置自动降级模板，服务不中断。"""

    def test_no_key_builds_template(self):
        interp = build_interpreter()
        self.assertEqual(interp.name, "template")

    def test_with_key_builds_llm(self):
        interp = build_interpreter(make_llm(["正常文本"]))
        self.assertEqual(interp.name, "llm")

    def test_unconfigured_returns_template_result(self):
        r = interpret_with_hard_gate(make_ctx())
        self.assertEqual(r.generator, "template")
        self.assertTrue(r.scanned)
        self.assertTrue(scan(r.text)[0])


class TestHardGate(unittest.TestCase):
    """DoD-3：硬门禁链路（LLM 命中禁词 → 重试 1 次 → 仍命中 → 回退模板）。"""

    def test_llm_forbidden_twice_falls_back_to_template(self):
        llm = make_llm(["建议买入该股", "可以考虑卖出"])     # 两次都含禁词
        r = gate(make_ctx(), llm=llm)
        self.assertEqual(llm.calls["n"], 2)                   # 调了 2 次（1 次 + 重试 1 次）
        self.assertEqual(r.generator, "template")            # 回退模板
        self.assertTrue(r.scanned)
        self.assertTrue(scan(r.text)[0])                     # 最终展示文本必 PASS

    def test_llm_fail_once_then_clean_uses_llm(self):
        llm = make_llm(["可以清仓", "该股因子状态在同池中偏弱，短期动量处于低位。"])
        r = gate(make_ctx(), llm=llm)
        self.assertEqual(llm.calls["n"], 2)
        self.assertEqual(r.generator, "llm")                 # 第二次干净 → 用 LLM 结果
        self.assertTrue(scan(r.text)[0])

    def test_llm_network_error_falls_back(self):
        llm = make_llm([ConnectionError("network down")])
        r = gate(make_ctx(), llm=llm)
        self.assertEqual(r.generator, "template")            # 故障降级，服务不中断
        self.assertTrue(r.scanned)

    def test_template_output_always_passes_gate(self):
        r = gate(make_ctx())
        ok, _hits = scan(r.text)
        self.assertTrue(ok)


class TestTemplateStyle(unittest.TestCase):
    """DoD-4：模板输出与 Spike-2 演示报告文风一致（基线保障）。"""

    def setUp(self):
        self.text = TemplateInterpreter().generate(make_ctx())

    def test_spike2_structure(self):
        self.assertIn("一句话结论", self.text)                # 第一层开头（Spike-2 同款）
        for n in "①②③④⑤":                                  # 第二层五段（Spike-2 同款）
            self.assertIn(n, self.text)
        self.assertIn("用的数据", self.text)
        self.assertIn("用的分析工具", self.text)
        self.assertIn("合成一个数", self.text)
        self.assertIn("是什么意思/不是什么意思", self.text)
        self.assertIn("什么时候会变", self.text)

    def test_compliance_baseline_statements(self):
        self.assertIn("不代表上涨概率", self.text)
        self.assertIn("不构成", self.text)
        self.assertIn("2026-10-23", self.text)                # 复核日


if __name__ == "__main__":
    unittest.main(verbosity=2)
