# -*- coding: utf-8 -*-
"""解读生成包（T-06，ADR-003④）：可插拔接口——LLM 实现为主、模板实现兜底。

硬门禁（AC-005 运行时强制）：LLM 生成文本未经禁词扫描禁止返回；
命中禁词 → 重试 1 次 → 仍命中 → 自动回退模板实现。
"""

from .base import DiagnosisContext, InterpretationGenerator, InterpretationResult, get_interpretation
from .factory import build_interpreter, interpret_with_hard_gate
from .template_impl import TemplateInterpreter

__all__ = ["DiagnosisContext", "InterpretationGenerator", "InterpretationResult",
           "get_interpretation", "build_interpreter", "interpret_with_hard_gate",
           "TemplateInterpreter"]
