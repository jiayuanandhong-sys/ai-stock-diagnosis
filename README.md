# AI 量化个股诊断

> 输入一个 A 股股票代码，在线生成该股的**周频量化因子诊断报告**（分数 + 白话解读），支持 Word 文档下载。

本仓库同时是一个 **SDD（Spec-Driven Development，规格驱动开发）** 完整实践项目：Spec → Plan → Tasks → Implement → Verify 五阶段全程留痕，三份活文档与验收证据链全部随库提供（见下方[项目管理](#项目管理sdd)）。

---

## 功能特性

| 能力 | 说明 |
|---|---|
| **输入即诊断** | 唯一输入 = 6 位股票代码；无登录、无 Cookie、无 JS，零个人信息采集 |
| **四池归属判定** | 按中证指数成分归属四池：沪深300 / 中证500 / 中证1000 / 中证2000；池外 A 股（微盘、北交所等）拒绝并明确提示 |
| **18 项日线因子** | 覆盖估值特征 / 价格行为 / 交易行为 / 风险特征四个观察侧面，全部基于收盘后日线级公开数据 |
| **S/H 双分数** | **S** = 同池相对强弱（0–100 百分位，等权合成）；**H** = 该股 52 个完成周的历史位置分 |
| **双格式报告** | 在线 HTML 完整报告 + 同源同构 docx 下载（简要 + 详细合并版，17 章节） |
| **可插拔 AI 解读** | 配置 `SDD_LLM_API_KEY` 即接入大模型解读；未配置自动降级为模板解读 |
| **合规硬门禁** | 11 个绝对禁词运行时扫描，未过扫描的报告禁止展示；命中→重试 1 次→回退模板 |
| **防滥用限流** | 单 IP 每日请求上限（默认 50，可配），跨日自动重置 |
| **演示模式** | 分池演示样本（沪深300 样本 21 只 + 中证500 样本 20 只），开始页可点选 41 只演示股票快速体验 |

## 技术架构

服务端渲染（FastAPI + Jinja 无依赖纯字符串模板），无前端框架、无数据库：

```
浏览器 ──► app.py (FastAPI 路由/限流/错误路径)
              │
              ├─► pool_manager.py      四池名单快照 + 归属判定（池外拒绝）
              ├─► data_fetcher.py      日线行情拉取（新浪主源→东财备援）+ parquet 缓存
              ├─► factor_engine.py     18 因子计算 → S（同池百分位）/ H（52 周历史位）
              ├─► interpretation/      解读生成（base 接口 + LLM 实现 + 模板实现 + 工厂切换）
              ├─► compliance.py        合规禁词扫描（运行时硬门禁）
              ├─► trade_cal.py         交易日历（复核日 = 每 5 个交易日）
              └─► report_builder.py   报告组装（在线 HTML + docx 双格式，同源同构）
```

## 快速开始

**环境要求**：Python 3.11+（3.13 验证通过）

```bash
# 1. 安装依赖
pip install -r requirements.txt

# 2. 启动（演示模式：分池样本，即开即用）
python -m uvicorn src.app:app --host 127.0.0.1 --port 8000

# 3. 浏览器打开
#    http://127.0.0.1:8000
```

Windows 用户可直接运行 `deploy\start_server.bat`，运维细节见 `deploy\运维说明.md`。

> 提示：如本机配置了强制代理，请设置 `NO_PROXY=*` 以放行行情数据源。首次访问某只股票需在线拉取全池样本数据（演示池约 6–45 秒），当日重复访问走缓存（毫秒级）。

## 配置项（环境变量）

| 变量 | 默认值 | 说明 |
|---|---|---|
| `SDD_POOL_MODE` | `demo` | `demo` = 分池演示样本；`full` = 四池真实名单（需冷启动预热） |
| `SDD_DAILY_LIMIT` | `50` | 单 IP 每日请求上限（/diagnose 与 /download 共享计数） |
| `SDD_PORT` | `8000` | 服务端口 |
| `SDD_LLM_API_KEY` | 无 | 配置后启用大模型解读；缺省自动降级模板解读 |

## 分数体系（简版）

- **S（同池相对强弱）**：18 项因子统一方向后，按"平均并列名次百分位"折算 0–100 分，等权合成——分数只回答"该股的因子状态在其所属池中排第几"，**不代表上涨概率、可能性或收益预期**。
- **H（历史位置参考）**：该股自身 52 个完成周的分数百分位，回答"相对它自己的历史，当前因子状态处于什么位置"。

完整定义见 `sdd-stock-diagnosis/spec.md` 附表 A（18 项因子清单冻结版）。

## 测试

```bash
python -m unittest tests.test_compliance tests.test_data_fetcher \
  tests.test_pool_manager tests.test_factor_engine tests.test_interpretation
# 53 个用例，覆盖合规扫描/数据层/池管理/因子引擎/解读接口
```

## Evals 复现

评测工程位于 `evals/`（含 41 案例合成 Golden Dataset、PEG 检查器、五状态 Runner 与基础设施回归套件），完整复现命令与退出码约定见 `evals/README.md`：

```bash
cd evals/tests  && python test_freeze_gate.py        # 冻结门禁 8/8
cd evals/tests  && python test_runner_gate.py        # Runner 门禁 10/10
cd evals/tests  && python test_status_aggregation.py # 五状态聚合 13/13
cd evals/peg    && python test_peg_checker.py        # PEG 检查器 18/18
cd evals/dataset && python build_dataset.py          # PEG 全绿 → 发布清单，exit 0
cd evals/runner  && python run_evals.py              # 离线评测（五状态统计）
cd evals/runner  && python run_e2e.py                # E2E 真实 Web 端到端（需联网）
```

## 项目管理：SDD

本仓库按 **Spec-Driven Development** 方法论开发，五阶段产物全程可追溯：

| 文档 | 内容 |
|---|---|
| `sdd-stock-diagnosis/spec.md` | 需求规格：3 REQ / 9 BR / 11 AC / 附表 A 因子清单 / CR 变更台账 |
| `sdd-stock-diagnosis/plan.md` | 技术方案：12 项技术问题地图 + 4 个 ADR + Spike 结论 |
| `sdd-stock-diagnosis/tasks.md` | 任务分解：T-01~T-10 + DoD + AC 覆盖矩阵 |
| `sdd-stock-diagnosis/GateV验收报告.md` | 最终验收：AC-001~011 逐项独立实测记录 |
| `evidence/` | 各任务验收证据 + Gate V 独立复算对拍数据（四池真实成分股） |

## 目录结构

```
├── src/                    # 源码（9 个模块 + interpretation/ 解读包 + config/ 禁词表）
├── tests/                  # 单元测试 + DoD 验收脚本 + Gate V 独立验收脚本
├── evals/                  # Evals 工程（PEG / 合成 Golden Dataset / Runner / 回归套件 / 实施报告）
├── deploy/                 # 启动脚本 + 运维说明
├── sdd-stock-diagnosis/    # SDD 五阶段活文档（spec / plan / tasks / 验收报告 / spikes）
├── evidence/               # 验收证据链（T-01~T-10 + GateV）
├── docs/                   # PM 设计文档存档（产品 MVP 方案 / Evals 体系设计 v0.1–v0.2）
├── data_cache/             # 运行缓存（池名单快照随库；行情 parquet / 公告为运行产物）
├── requirements.txt
└── README.md
```

## 已知限制

- **evals 脚本含本机绝对路径常量**：`evals/dataset/build_dataset.py`、`evals/runner/run_evals.py`、`evals/peg/test_peg_checker.py` 中的 `PROD`/`SPEC`/`TASKS` 指向开发机路径；克隆到其他机器后需按注释改为本地路径（不影响算法与断言本身）。
- **Windows 启动脚本指向本机 venv**：`deploy/start_server.bat` 使用开发机托管 Python 解释器路径；跨机器请按「快速开始」用 `python -m uvicorn src.app:app` 通用方式启动。
- **LLM 解读未接真实密钥**：`SDD_LLM_API_KEY` 未配置时自动降级为模板解读（当前线上即模板兜底态）；LLM Judge 全部断言未运行（待提供密钥）。
- **Evals 当前判定为「未完整验收」**：v1.3 合并结果 25 PASS / 0 FAIL / 8 PARTIAL / 8 BLOCKED（holdout×4 与 OPEN 项待 PM 处置），放行判定归 release-acceptance 流程。
- **演示模式边界**：`full` 模式需冷启动预热；E2E-004/005 为 demo 边界行为；L6-038 跨自然日断言单日不可验证。
- 数据源为 akshare 免费公开接口（锁 1.18.64），接口漂移敏感；本机强制代理需设 `NO_PROXY=*`。

## 免责声明

本工具仅使用收盘后的日线级公开行情与估值数据，不收集任何个人信息（仅代码一个输入）。分数仅代表因子状态在同池中的相对强弱，不代表上涨概率、可能性或收益预期，**不构成任何证券交易类操作建议**。
