# -*- coding: utf-8 -*-
"""T-01 合规检查器 —— AC-005 的机械化执行器（tasks.md v1.0）。

设计依据：
- AC-005：报告全文不出现操作动词（关键词扫描，机械可判定，不设语境例外）；
- AC-010：边界文案基线在位（"怎么读这个分数""风险与边界"章节 + 口径语句 + 免责声明）；
- ADR-003⑦：独立小工具，任何报告输出前强制执行；LLM 文本未经扫描禁止展示（运行时硬门禁）；
- Spike-2 实战案例：免责声明自身含"买入、卖出"亦判 FAIL（改文案不改标准）。

接口契约（DoD-2）：
- scan(text) -> (passed: bool, hits: list[str])  纯函数，无副作用，可单测、可生成链路内联调用；
- check_report(text) -> ReportCheck  报告级完整检查（禁词 + 必需文案存在性）；
- 禁词表与必需文案外置 src/config/forbidden_words.json（DoD-3），增删条目不改代码。
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from typing import List, Optional, Tuple

_CONFIG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                            "config", "forbidden_words.json")


def load_config(path: Optional[str] = None) -> dict:
    """读取外置配置。path 可注入（供测试与自定义部署）。"""
    with open(path or _CONFIG_PATH, encoding="utf-8") as f:
        cfg = json.load(f)
    words = cfg.get("forbidden_words", [])
    phrases = cfg.get("required_phrases", [])
    if not words:
        raise ValueError("禁词表为空：AC-005 扫描器拒绝空配置")
    return {"forbidden_words": list(words), "required_phrases": list(phrases)}


# 模块级默认配置（随包加载一次；测试与运行时均可通过参数注入覆盖）
_DEFAULT_CFG = load_config()
FORBIDDEN_WORDS: List[str] = _DEFAULT_CFG["forbidden_words"]
REQUIRED_PHRASES: List[str] = _DEFAULT_CFG["required_phrases"]


def scan(text: str,
         forbidden_words: Optional[List[str]] = None) -> Tuple[bool, List[str]]:
    """绝对禁词扫描（纯函数）。

    规则：命中任一禁词即 FAIL，不设语境例外（AC-005 机械可判定原则；
    Spike-2 实证：免责声明里"不构成买入、卖出……建议"同样拦截）。

    :param text: 待扫描全文（报告正文 / LLM 生成的解读文本 / 任意对外文案）
    :param forbidden_words: 可选注入禁词表（默认读外置 JSON）
    :return: (passed, hits) —— passed=True 表示零命中；hits 为命中的禁词列表
    """
    words = forbidden_words if forbidden_words is not None else FORBIDDEN_WORDS
    hits = [w for w in words if w and w in (text or "")]
    return (len(hits) == 0, hits)


@dataclass
class ReportCheck:
    """报告级检查结果（供报告生成器阻断输出、供测试断言）。"""
    passed: bool
    forbidden_hits: List[str] = field(default_factory=list)
    missing_phrases: List[str] = field(default_factory=list)

    def summary(self) -> str:
        if self.passed:
            return "合规检查 PASS：零禁词命中，口径文案齐备"
        parts = []
        if self.forbidden_hits:
            parts.append("禁词命中: " + "、".join(self.forbidden_hits))
        if self.missing_phrases:
            parts.append("缺失文案: " + "、".join(self.missing_phrases))
        return "合规检查 FAIL：" + "；".join(parts)


def check_report(text: str,
                 forbidden_words: Optional[List[str]] = None,
                 required_phrases: Optional[List[str]] = None) -> ReportCheck:
    """报告输出前强制执行的完整检查（T-05 报告生成器最后一步必须调用）。

    = 禁词扫描（绝对禁令）+ 必需口径文案存在性（免责声明 / 不代表上涨概率 /
      "怎么读这个分数" / "风险与边界"章节，AC-005 + AC-010）。
    FAIL 即阻断输出（调用方负责不落盘不展示）。
    """
    ok_words, hits = scan(text, forbidden_words)
    phrases = required_phrases if required_phrases is not None else REQUIRED_PHRASES
    missing = [p for p in phrases if p not in (text or "")]
    return ReportCheck(passed=bool(ok_words and not missing),
                       forbidden_hits=hits,
                       missing_phrases=missing)


def assert_compliant_or_raise(text: str, source: str = "报告") -> None:
    """硬门禁便捷入口：FAIL 抛异常，供生成链路最后一道闸使用。"""
    result = check_report(text)
    if not result.passed:
        raise ComplianceBlockError(source, result)
    return None


class ComplianceBlockError(Exception):
    """合规阻断异常：命中禁词或文案缺失时抛出，调用方必须停止输出。"""

    def __init__(self, source: str, result: ReportCheck):
        self.source = source
        self.result = result
        super().__init__(f"[{source}] {result.summary()}")


if __name__ == "__main__":
    # 独立运行自检：python src/compliance.py
    demo_bad = "建议买入并持有，跌破支撑止损。"
    demo_good = ("分数仅代表因子状态在同池中的相对强弱，不代表上涨概率，不构成任何证券交易类操作建议。"
                 "怎么读这个分数：高于 60 偏有利，低于 40 偏不利，40-60 中性。风险与边界：数据来自公开渠道。")
    print("scan(demo_bad) =", scan(demo_bad))
    print("check_report(demo_good) =", check_report(demo_good).summary())
