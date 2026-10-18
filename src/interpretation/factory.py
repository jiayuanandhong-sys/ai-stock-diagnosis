# -*- coding: utf-8 -*-
"""工厂 + 运行时硬门禁（AC-005，ADR-003④）。

链路（DoD-3）：LLM 文本 → 禁词扫描 → FAIL 重试 1 次 → 仍 FAIL → 回退模板实现；
任何返回给调用方的文本必然 scan 通过（ComplianceBlockError 除外——模板输出若仍命中禁词，
说明模板本身被污染，直接抛错阻断，绝不返回未扫描文本）。
"""

from __future__ import annotations

import os
from typing import Optional

from ..compliance import ComplianceBlockError, scan
from .base import DiagnosisContext, InterpretationGenerator, InterpretationResult
from .llm_impl import ENV_KEY, LLMInterpreter
from .template_impl import TemplateInterpreter


def build_interpreter(llm: Optional[LLMInterpreter] = None) -> InterpretationGenerator:
    """按环境变量选择实现：未配置 SDD_LLM_API_KEY → 自动降级模板（DoD-2）。"""
    if llm is not None and llm.configured:
        return llm
    if os.environ.get(ENV_KEY):
        cand = llm or LLMInterpreter()
        if cand.configured:
            return cand
    return TemplateInterpreter()


def interpret_with_hard_gate(ctx: DiagnosisContext,
                             llm: Optional[LLMInterpreter] = None,
                             retries: int = 1) -> InterpretationResult:
    """统一解读入口（含硬门禁）。

    - LLM 路径：scan FAIL → 重试 retries 次 → 仍 FAIL → 回退模板；
    - 模板路径：scan FAIL → 抛 ComplianceBlockError（模板静态可预审，运行时命中即视为污染事故）。
    """
    interp = build_interpreter(llm)
    if interp.name == "llm":
        for attempt in range(1 + retries):
            try:
                text = interp.generate(ctx)
            except Exception:                    # 网络/接口故障 → 降级模板，服务不中断
                break
            ok, hits = scan(text)
            if ok:
                return InterpretationResult(text=text, generator="llm", scanned=True)
            last_hits = hits                     # 命中禁词 → 重试
        fallback = TemplateInterpreter()
        text = fallback.generate(ctx)
        ok, hits = scan(text)
        if not ok:
            raise ComplianceBlockError("解读生成·模板兜底",
                                       type("R", (), {"passed": False,
                                                      "forbidden_hits": hits,
                                                      "missing_phrases": []})())
        return InterpretationResult(text=text, generator="template", scanned=True)
    # 模板实现
    text = interp.generate(ctx)
    ok, hits = scan(text)
    if not ok:
        raise ComplianceBlockError("解读生成·模板",
                                   type("R", (), {"passed": False,
                                                  "forbidden_hits": hits,
                                                  "missing_phrases": []})())
    return InterpretationResult(text=text, generator="template", scanned=True)
