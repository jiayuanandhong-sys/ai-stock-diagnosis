# -*- coding: utf-8 -*-
"""T-06 接口定义（DoD-1：接口冻结）。

输入 = 分数 + 因子结构化数据（S/H/四侧面/18 因子分/同池元信息）；
输出 = 解读文本 + 生成器标识。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Dict, List, Optional


@dataclass
class DiagnosisContext:
    """解读生成的全部结构化输入（不含任何个人信息，BR-004）。"""
    code: str
    name: str
    pool: str                                  # 同池名（如"沪深300"）
    observe_date: str                          # 观察日（最新交易日 YYYY-MM-DD）
    S: Optional[float] = None                   # 综合分（0-100）
    H: Optional[float] = None                   # 历史位置参考分
    rank_in_pool: Optional[int] = None
    pool_size: Optional[int] = None
    side_scores: Dict[str, Optional[float]] = field(default_factory=dict)   # 四侧面
    factor_scores: Dict[str, float] = field(default_factory=dict)           # F1..F18 → 分
    factor_labels: Dict[str, str] = field(default_factory=dict)             # F1..F18 → 匿名标签
    top_positives: List[str] = field(default_factory=list)                  # 最有利因子标签
    top_negatives: List[str] = field(default_factory=list)
    week_change: Optional[float] = None        # 近一周涨跌幅（如 -0.073）
    review_date: str = ""                      # 建议复核日


@dataclass
class InterpretationResult:
    """解读输出。scanned=True 表示已过禁词扫描（硬门禁后此值恒为 True）。"""
    text: str
    generator: str                              # "llm" | "template"
    scanned: bool = False


class InterpretationGenerator(ABC):
    """解读生成器接口（可插拔；实现类不得直接对外输出未扫描文本）。"""

    @abstractmethod
    def generate(self, ctx: DiagnosisContext) -> str:
        """生成解读文本（纯文本，多段以换行分隔）。"""

    @property
    @abstractmethod
    def name(self) -> str:
        """实现标识："llm" | "template"。"""


def get_interpretation(ctx: DiagnosisContext) -> InterpretationResult:
    """统一入口：按环境变量选择实现 + 硬门禁（见 factory.interpret_with_hard_gate）。"""
    from .factory import interpret_with_hard_gate
    return interpret_with_hard_gate(ctx)
