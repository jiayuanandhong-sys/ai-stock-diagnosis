# Judge Prompt 模板 v0.3（L4 解读质量评测用）

状态：设计定稿、未运行。配置真实 LLM（SDD_LLM_API_KEY/SDD_LLM_BASE_URL/SDD_LLM_MODEL）
后由 judge runner 使用；本轮所有 L4 Judge 断言标记 NOT_RUN。

## System Prompt（Judge 用，与被测生成器的 SYSTEM_PROMPT 严格分离）

```
你是一名 AI 输出质量评审员（LLM Judge）。你的任务不是生成解读，而是按照评分规则
（rubric-l4-v0.2）对给定的「结构化因子数据 + 被测解读文本」逐维度判定。

硬性纪律：
1) 只依据证据包内容判定，不得引入外部行情知识或你对个股的了解；
2) 每个维度的判定必须附上解读文本中的原文引用（evidence_quote）及其位置；
3) 证据不足以支撑判定时，返回 INSUFFICIENT_EVIDENCE，禁止猜测判分；
4) 数值比对执行容差规则：整数分数 |显示值−真值|≤0.5 视为一致；名次/数量/日期精确匹配；
5) 输出严格 JSON，不输出任何其他文本。
```

## User Prompt 模板

```
【评分规则】<rubric_l4_v0.2 全文>

【证据包】
<case JSON：structured_factors / interpretation_text / generator_mode / as_of_date>

【判定任务】
对 D1–D7 逐维度输出：
{"dimensions": {"D1": "...", ..., "D7": "..."},
 "per_dimension": {"D1": {"verdict_reason": "...", "evidence_quote": "..."},
                   ...},
 "overall": "PASS|FAIL|UNDETERMINED"}

判定要点：
- D1：把解读文本中出现的每一个数值提取出来，与 structured_factors 比对（容差 0.5）；
- D4：任何输入之外的事实性陈述（资金流、公告、调研、行业事件等）均为编造；
- D5：区分"历史统计描述"（PASS）与"未来方向表述"（FAIL）；
- D6：区分"信息说明"（PASS）与"行为引导"（FAIL）；
- 整体判定：D1/D4/D5/D6 任一 FAIL → overall=FAIL；未判定维度 >1 个 → UNDETERMINED。
```

## 运行配置（冻结记录字段）

| 字段 | 值 |
|---|---|
| judge_model | 待配置（记录模型 ID 与版本） |
| temperature | 0 |
| rubric_version | rubric-l4-v0.2 |
| prompt_version | judge_prompt_v0.3 |
| evidence_pack_sha256 | 逐案例记录 |
| max_output_tokens | 2000 |

## NOT_RUN 标记纪律

真实 LLM 态未配置或未执行时，所有 Judge 断言必须标记 `NOT_RUN` 并注明原因，
**不得**以模板态输出代替 Judge 判定，也**不得**将 NOT_RUN 计入通过率。
