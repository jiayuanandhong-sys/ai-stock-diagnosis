# -*- coding: utf-8 -*-
"""T-01 合规检查器用例集（DoD-1：正例/反例全通过，含 Spike-2 实战案例）。"""

import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from src.compliance import (ComplianceBlockError, ReportCheck, assert_compliant_or_raise,
                            check_report, load_config, scan)

# ---- 用例文本基线 ----------------------------------------------------------

# Spike-2 修正后的合格边界文案（2026-10-17 实测通过版）
GOOD_TEXT = (
    "本报告为量化因子状态的客观描述工具。分数描述因子状态，不代表上涨概率、可能性或收益预期，"
    "亦不构成任何证券交易类操作建议或投资顾问服务。四个观察侧面仅用于拆解展示，不另设权重；"
    "风险维度得分高不等于绝对风险低。数据来自公开渠道，可能存在延迟或修正，请以交易所披露为准。"
    "怎么读这个分数：高于 60 读取为偏有利，低于 40 读取为偏不利，40-60 为中性区。"
    "风险与边界：本报告由量化管线自动生成。"
)

# Spike-2 实战反例：免责声明原文自身含"买入、卖出"（2026-10-17 被扫描器逮住，已改文案不改标准）
SPIKE2_ORIGINAL_DISCLAIMER = (
    "本报告为量化因子状态的客观描述工具。分数描述因子状态，不代表上涨概率、可能性或收益预期，"
    "亦不构成买入、卖出或任何操作建议。怎么读这个分数：略。风险与边界：略。"
)

# 11 个禁词逐词反例（对齐外置禁词表）
FORBIDDEN_WORDS = load_config()["forbidden_words"]


class TestScan(unittest.TestCase):
    """DoD-2：scan 纯函数接口。"""

    def test_pure_function_no_side_effects(self):
        text = GOOD_TEXT
        before = text
        r1 = scan(text)
        r2 = scan(text)
        self.assertEqual(r1, r2)              # 重复调用一致
        self.assertEqual(text, before)        # 不修改输入
        self.assertIsInstance(r1, tuple) and self.assertIsInstance(r1[0], bool)

    def test_good_text_passes(self):
        passed, hits = scan(GOOD_TEXT)
        self.assertTrue(passed)
        self.assertEqual(hits, [])

    def test_each_forbidden_word_hits(self):
        for w in FORBIDDEN_WORDS:             # 每个禁词单独命中即 FAIL
            passed, hits = scan(f"示例文本……{w}……示例")
            self.assertFalse(passed, f"禁词 {w} 未被拦截")
            self.assertIn(w, hits)

    def test_spike2_original_disclaimer_fails(self):
        """Spike-2 实战案例：免责声明含"买入、卖出"必须判 FAIL（DoD-1）。"""
        passed, hits = scan(SPIKE2_ORIGINAL_DISCLAIMER)
        self.assertFalse(passed)
        self.assertIn("买入", hits)
        self.assertIn("卖出", hits)

    def test_empty_and_none_input(self):
        self.assertEqual(scan(""), (True, []))
        self.assertEqual(scan(None), (True, []))  # None 安全（缺失项不填分语境）

    def test_injected_wordlist(self):
        passed, hits = scan("custom-word inside", forbidden_words=["custom-word"])
        self.assertFalse(passed)
        self.assertIn("custom-word", hits)


class TestCheckReport(unittest.TestCase):
    """报告级检查：禁词 + 必需口径文案存在性（AC-005 + AC-010）。"""

    def test_good_report_passes(self):
        r = check_report(GOOD_TEXT)
        self.assertIsInstance(r, ReportCheck)
        self.assertTrue(r.passed, r.summary())

    def test_spike2_original_disclaimer_fails_on_words(self):
        r = check_report(SPIKE2_ORIGINAL_DISCLAIMER)
        self.assertFalse(r.passed)
        self.assertIn("买入", r.forbidden_hits)

    def test_missing_required_phrases_fail(self):
        text = "干干净净的一段文字，没有任何禁词。"
        r = check_report(text)
        self.assertFalse(r.passed)
        self.assertEqual(r.forbidden_hits, [])
        self.assertTrue(len(r.missing_phrases) >= 4)

    def test_summary_contains_details(self):
        r = check_report("建议买入。")
        self.assertIn("买入", r.summary())
        self.assertIn("缺失文案", r.summary())


class TestExternalConfig(unittest.TestCase):
    """DoD-3：禁词表数据外置，增删禁词不改代码。"""

    def test_config_loadable_and_external(self):
        cfg = load_config()
        self.assertGreaterEqual(len(cfg["forbidden_words"]), 11)   # DoD ≥11 词
        self.assertIn("required_phrases", cfg)

    def test_custom_path_injection(self):
        """外置配置可替换：写临时 JSON → 注入加载 → 新禁词生效（零代码改动）。"""
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False,
                                          encoding="utf-8") as f:
            f.write('{"forbidden_words": ["新禁词A"], "required_phrases": []}')
            path = f.name
        try:
            cfg = load_config(path)
            self.assertEqual(cfg["forbidden_words"], ["新禁词A"])
            passed, hits = scan("文本包含 新禁词A 结束", cfg["forbidden_words"])
            self.assertFalse(passed)
            self.assertIn("新禁词A", hits)
        finally:
            os.unlink(path)

    def test_empty_wordlist_rejected(self):
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False,
                                          encoding="utf-8") as f:
            f.write('{"forbidden_words": [], "required_phrases": []}')
            path = f.name
        try:
            with self.assertRaises(ValueError):
                load_config(path)
        finally:
            os.unlink(path)


class TestHardGate(unittest.TestCase):
    """运行时硬门禁入口（供 T-05/T-06 调用）。"""

    def test_pass_text_no_raise(self):
        assert_compliant_or_raise(GOOD_TEXT, source="单测")

    def test_fail_text_raises_with_source(self):
        with self.assertRaises(ComplianceBlockError) as cm:
            assert_compliant_or_raise("建议买入", source="解读生成")
        self.assertIn("解读生成", str(cm.exception))
        self.assertIn("买入", str(cm.exception))


if __name__ == "__main__":
    unittest.main(verbosity=2)
