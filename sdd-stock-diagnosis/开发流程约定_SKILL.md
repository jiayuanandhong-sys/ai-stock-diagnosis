---
name: sdd-implement-workflow
description: AI 量化个股诊断 MVP（工作区 sdd-stock-diagnosis 项目）的开发流程约定（conventions 层）。在此项目中执行任何编码、测试、调试、文档维护或 SDD 阶段推进（Implement/Verify、任务 T-01~T-10、spec/plan/tasks 更新）时使用。规定文档权威链（spec、plan、tasks 三层）、六步任务开发循环、范围冻结与合规硬纪律、以及本机数据源备援/代理绕过等环境备忘。SDD 通用五阶段方法论见用户级 skill spec-driven-development，本 skill 为其在本项目的落地约定，二者叠加使用。
agent_created: true
---

# SDD 个股诊断 MVP 开发流程约定（sdd-implement-workflow）

## 适用与定位
- 适用场景：在 `sdd-stock-diagnosis` 项目中执行任何开发工作（实现任务、修 bug、跑测试、更新三份活文档、推进 Gate）。
- 定位：本项目 **conventions 层**。SDD 通用方法论（五阶段/Gate/CR 机制）以用户级 skill `spec-driven-development` 为准；本 skill 只规定本项目的文档权威链、任务循环、硬纪律与本机环境备忘，不重复通用内容。

## 1. 文档权威链（每次开发会话第一步）
按序读取工作区 `sdd-stock-diagnosis/` 下的三份活文档，理解状态后再动手：

1. `spec.md` —— 产品契约（REQ/SCN/BR/AC/附表 A 因子清单）。**最高权威，任何内容变更必须走 CR 并经 PM 批准**（BR-005 范围冻结），禁止直接编辑。
2. `plan.md` —— 技术方案（ADR-001~004、技术问题地图 E-1~E-12、里程碑）。技术选型与架构决策的唯一权威。
3. `tasks.md` —— 任务清单（T-01~T-10，每任务含可测 DoD 与 AC 映射）。开发工作的唯一入口。

判定"当前该干什么"：取 tasks.md 中最早的 ⏳ 状态任务；Gate 进度看 spec.md 第 11 节阶段门记录；开放问题看 plan.md §6。

## 2. 任务开发循环（每个任务固定六步）
1. **认领**：tasks.md 中取最早 ⏳ 状态任务。
2. **读 DoD**：确认每条验收条件可测；若 DoD 不可测或发现范围缺口 → 停止实现，登记问题提请 PM 裁决（必要时走 CR），不得自行发挥。
3. **实现**：严格遵循 plan.md ADR-003 技术栈（Python 3.13 venv / pandas+numpy / matplotlib / FastAPI / python-docx / 可插拔解读接口 / 无数据库）；代码放 `src/`，不引入清单外重框架。
4. **自测**：DoD 逐条执行验证；测试代码放 `tests/`，测试输出/截图等证据存 `evidence/`。
5. **更新状态**：tasks.md 该任务置 ✅ 并附一行证据（日期 + 结果/evidence 路径）。
6. **留痕**：向 `.workbuddy/memory/YYYY-MM-DD.md` 追加当日工作日志（追加式，不覆盖）。

## 3. 硬纪律（优先级高于任何进度压力，违反即返工）
- **范围冻结（BR-005）**：实现中发现新需求/新功能，唯一合法动作是登记 CR 提请 PM 裁决；不得"顺手实现"，不得静默绕过或吸收进现有任务。
- **合规门禁（AC-005，全项目最高优先）**：一切对外文本（报告章节/解读/提示文案）必须经 `src/compliance.py` 扫描通过；LLM 生成的解读文本**未经扫描禁止展示**，命中禁词 → 重试 1 次 → 仍命中 → 回退模板实现（ADR-003④）。禁词表为绝对禁令，不设语境例外（Spike-2 实证：免责声明自身含"买入/卖出"也被判 FAIL，改文案不改标准）。
- **不编造**：测试未真正运行不得标 ✅；未实测的性能/效果结论不得写入任何文档；工期不硬编（SDD 纪律）。
- **Spec 不可直接改**：工程发现 Spec 缺口 → 发 CR；PM 批准后才可改 spec.md，并同步更新 CR 台账与版本号。
- **单人+AI 量级（ADR-003⑤）**：不引入微服务、前端框架、数据库等超出 ADR-003 的复杂度；确有必要先改 ADR 再动代码。

## 4. 本机环境备忘（实测沉淀，新会话直接照用）
- **运行时**：venv 解释器 = `C:/Users/28309/.workbuddy/binaries/python/envs/default/Scripts/python.exe`；所有包只装进该 venv；akshare **锁定 1.18.64**（升级前必须跑数据备援回归测试）。
- **网络（重要）**：沙箱内系统代理（127.0.0.1:6947）对本机数据源域名不可达 → 数据拉取脚本开头必须设置 `os.environ["NO_PROXY"] = "*"` 并清空 `http_proxy/https_proxy/HTTP_PROXY/HTTPS_PROXY` 四个变量；东财域名可能被阻断（主/备镜像均曾不通），日线备援用新浪 `stock_zh_a_daily`（自带换手率/成交额/流通股本），估值用百度股市通 `stock_zh_valuation_baidu`（指标名：总市值/市盈率(TTM)/市净率，period="近一年"），成分股名单走中证官网（ADR-002）。
- **接口漂移**：akshare 移除/更名接口是常态（乐咕 `stock_a_indicator_lg` 已在 1.18.64 被移除）；任何接口改动前先小样本探测，备援链路常备。因子分是截面排名，对数据口径级差天然稳健，但方向定义不能错。
- **渲染**：matplotlib 中文字体设 `Microsoft YaHei`；十六进制色值给 matplotlib/HTML 要带 `#` 前缀，给 python-docx 的 `RGBColor.from_string` 不带；分档着色遵循 BR-007（>60 红 / <40 绿 / 40–60 深灰，涨红跌绿）。
- **文件编辑**：Edit 工具会把 CRLF 行尾统一成 LF，改动含 CRLF 的旧文件后须校验行尾是否被意外改动。

## 5. 产物与状态约定
- 目录：`src/`（业务代码）、`tests/`（测试）、`evidence/`（验证证据）、`spikes/`（Spike 存档，保留不删）、`sdd-stock-diagnosis/`（三份活文档 + 附表 A）。
- 文档升版：spec/plan/tasks 每次实质变更升版本号并在头部注明日期与原因；纯笔误修正不升版。
- Gate 流程：Gate T（PM 审任务清单）通过前不写业务代码；Gate V 按 tasks.md §3 AC 覆盖矩阵逐格验收，证据全部落 `evidence/`。
- PM 责任项不代答：OPEN-P-003 付费问法、试用用户名单等 PM 输入项，只能提醒与预留，不得替 PM 编造。
