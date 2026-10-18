# -*- coding: utf-8 -*-
"""模板实现（兜底，DoD-4：输出与 Spike-2 演示报告文风一致）。"""

from __future__ import annotations

from typing import Optional

from .base import DiagnosisContext, InterpretationGenerator


def _fmt(x: Optional[float]) -> str:
    if x is None or x != x:          # None/NaN
        return "无数据"
    return f"{x:.0f}"


def _pct(x: Optional[float]) -> str:
    if x is None or x != x:
        return "无数据"
    return f"{x:+.1%}"


class TemplateInterpreter(InterpretationGenerator):
    """确定性模板生成（合规可控、文风与 Spike-2 基线一致）。"""

    @property
    def name(self) -> str:
        return "template"

    def generate(self, ctx: DiagnosisContext) -> str:
        pos = "、".join(ctx.top_positives[:3]) if ctx.top_positives else "暂无显著有利项"
        neg = "、".join(ctx.top_negatives[:3]) if ctx.top_negatives else "暂无显著不利项"
        head = (f"一句话结论：{ctx.name}（{ctx.code}）当前 18 项因子状态在 {ctx.pool} 同池中"
                f"综合分 S={_fmt(ctx.S)}（排名 {ctx.rank_in_pool}/{ctx.pool_size}），"
                f"历史位置参考分 H={_fmt(ctx.H)}。分数描述因子状态的同池相对强弱，"
                f"不代表上涨概率，不构成任何证券交易类操作建议。")
        para = [
            f"① 用的数据：截至 {ctx.observe_date} 的日线级行情（价格、成交量、成交额、振幅、换手率）与日线估值（市盈率、市净率、总市值）。",
            "② 用的分析工具：18 项量化观察因子，分估值特征、价格行为、交易行为、风险特征四个观察侧面，"
            "统一方向后在同池内做平均并列名次的百分位打分，等权合成综合分 S。",
            f"③ 合成一个数：S 是 18 项因子分的等权平均再放回同池重新排名（两级排名）；"
            f"H 是此前 52 个完成周 S 的相对位置（{_fmt(ctx.H)}）。",
            f"④ 是什么意思/不是什么意思：S={_fmt(ctx.S)} 高于 60 读取为偏有利、低于 40 读取为偏不利、"
            f"40–60 为中性区；它只描述当前因子状态在同池中的相对强弱，不代表上涨概率、收益预期，"
            f"也不构成任何操作指令。本期相对有利项：{pos}；相对拖累项：{neg}。",
            f"⑤ 什么时候会变：周频更新，每周收盘数据落地后重新计算即刷新；"
            + (f"近一周涨跌幅 {_pct(ctx.week_change)}，波动较大；" if ctx.week_change is not None else "")
            + f"建议复核日 {ctx.review_date}。",
        ]
        return "\n".join([head] + para)
