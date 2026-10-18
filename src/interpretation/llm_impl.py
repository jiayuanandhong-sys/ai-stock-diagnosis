# -*- coding: utf-8 -*-
"""LLM 实现（主实现，ADR-003④：需要大模型，留一个接口）。

- 密钥与端点走环境变量（SDD_LLM_API_KEY / SDD_LLM_BASE_URL / SDD_LLM_MODEL），不落库不入仓；
- OpenAI 兼容 chat/completions 协议（HTTP 直调，无重依赖）；
- 系统提示词内嵌合规约束，但合规不依赖提示词——**输出仍必须过工厂的禁词扫描硬门禁**；
- 未配置环境变量时由工厂自动降级模板实现（DoD-2）。
"""

from __future__ import annotations

import json
import os
import urllib.request
from typing import Callable, Optional

from .base import DiagnosisContext, InterpretationGenerator

ENV_KEY = "SDD_LLM_API_KEY"
ENV_BASE = "SDD_LLM_BASE_URL"
ENV_MODEL = "SDD_LLM_MODEL"
DEFAULT_BASE = "https://api.openai.com/v1"
DEFAULT_MODEL = "gpt-4o-mini"

SYSTEM_PROMPT = (
    "你是一名量化因子报告的解读撰写助手。根据输入的因子结构化数据，用中文撰写个股因子状态解读。"
    "硬性规则：1) 严禁出现任何操作动词（例如 买、卖、加仓、减仓、清仓、建仓、抄底、止盈、止损等），"
    "只描述因子状态与多空因素；2) 必须声明分数不代表上涨概率、收益预期，不构成任何建议；"
    "3) 语言克制、客观、面向散户可读；4) 输出 5 段以内，每段以序号开头，不要输出多余标题。"
)


class LLMInterpreter(InterpretationGenerator):
    """OpenAI 兼容接口的 LLM 解读生成器。http_post 可注入（测试用）。"""

    def __init__(self, api_key: Optional[str] = None, base_url: Optional[str] = None,
                 model: Optional[str] = None,
                 http_post: Optional[Callable[..., str]] = None):
        self.api_key = api_key or os.environ.get(ENV_KEY, "")
        self.base_url = (base_url or os.environ.get(ENV_BASE, DEFAULT_BASE)).rstrip("/")
        self.model = model or os.environ.get(ENV_MODEL, DEFAULT_MODEL)
        self._http_post = http_post or self._default_post

    @property
    def name(self) -> str:
        return "llm"

    @property
    def configured(self) -> bool:
        return bool(self.api_key)

    @staticmethod
    def _default_post(url: str, payload: dict, headers: dict, timeout: int = 60) -> str:
        req = urllib.request.Request(url, data=json.dumps(payload).encode("utf-8"),
                                     headers=headers, method="POST")
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.read().decode("utf-8")

    def generate(self, ctx: DiagnosisContext) -> str:
        if not self.configured:
            raise RuntimeError("LLM 未配置（缺 SDD_LLM_API_KEY）")
        data = {
            "code": ctx.code, "name": ctx.name, "pool": ctx.pool,
            "observe_date": ctx.observe_date, "S": ctx.S, "H": ctx.H,
            "rank": f"{ctx.rank_in_pool}/{ctx.pool_size}",
            "sides": ctx.side_scores,
            "factors": [{"id": k, "label": ctx.factor_labels.get(k, k), "score": v}
                        for k, v in ctx.factor_scores.items()],
            "positives": ctx.top_positives, "negatives": ctx.top_negatives,
            "week_change": ctx.week_change, "review_date": ctx.review_date,
        }
        user_prompt = ("基于以下因子诊断结构化数据撰写解读（遵守系统提示词全部规则）：\n"
                       + json.dumps(data, ensure_ascii=False))
        payload = {"model": self.model,
                   "messages": [{"role": "system", "content": SYSTEM_PROMPT},
                                {"role": "user", "content": user_prompt}],
                   "temperature": 0.2}
        headers = {"Content-Type": "application/json",
                   "Authorization": f"Bearer {self.api_key}"}
        raw = self._http_post(f"{self.base_url}/chat/completions", payload, headers)
        return json.loads(raw)["choices"][0]["message"]["content"].strip()
