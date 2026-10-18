# Evals 工程目录（AI 量化个股诊断 MVP）

产物版本（2026-10-18）：v0.3 工程实施 → v1.0 PM 裁决复跑 → v1.1 基础设施 BugFix →
**v1.2 PM D4/D5 裁决落实（REQ-003 三条件全自动判断）**

```
peg/       案例契约 Schema v1.1 + PEG-1~6 检查器 v1.1.0 + 单元测试（18）+ 已知错误样本
dataset/   独立真值库 truth_lib + 41 案例 + PEG 校验 + 严格冻结门禁（清单 v1.2）
runner/    Evals Runner：启动门禁 + 五状态聚合 + 退出码约定
tests/     基础设施回归套件（freeze_gate 8 + runner_gate 10 + aggregation 13）
judge/     LLM Judge Rubric / Prompt 模板 / 人工校准流程（未运行）
logs/      各阶段执行日志（含 *_v12 复跑日志）
reports/   实施报告 v0.3 / v1.0 / v1.1（BugFix）/ v1.2（D4/D5 落实）
```

## 工程约定（BugFix 后强制生效）

- **PEG 不通过 → 不得冻结、不得评测**：`build_dataset.py` 在 PEG overall≠PASS 时拒绝发布
  有效清单（写 `frozen_manifest.rejected.json`，既有有效清单原样保留，退出码 3）；
- **Runner 启动门禁**：清单必须 valid=true 且 PEG 总体 PASS、案例集与逐案例 sha256 一致、
  BLOCKED 不得伪装 FROZEN；任何门禁失败 → 退出码 2，不产生评测结果；
- **五状态聚合**：PASS（全部必需断言通过）/ FAIL（任一必需断言失败）/ PARTIAL（PASS 与
  NOT_RUN 并存）/ BLOCKED（案例未冻结）/ NOT_RUN（必需断言均未执行）。断言默认必需，
  optional 须显式标注并在报告中披露；PARTIAL 不计入完整通过率；
- 真值只能来自 truth_lib 独立实现 / 手算 / Spec 定义（PEG-4）；BLOCKED 不伪造预期答案；
  NOT_RUN 必须注明原因。OPEN-E01 已定案 rank/N，公式比对 FAIL 即为真实缺陷；
- **来源层级四级**（Schema v1.1）：spec / implementation_convention / eval_suggestion /
  **pm_decision**（PM 裁决定案的真值来源，如 OPEN-E01/E08/E09）。

## 退出码约定

| 脚本 | 0 | 1 | 2 | 3 |
|---|---|---|---|---|
| `runner/run_evals.py` | 指定范围完整执行且全部必需断言通过 | 存在实际测试失败（FAIL） | 门禁失败/运行异常，或存在阻碍完整验收的 PARTIAL、BLOCKED、NOT_RUN | — |
| `dataset/build_dataset.py` | 构建成功且 PEG 全绿、清单已发布 | — | — | PEG 校验失败，冻结被拒绝 |

## 复现

```bash
# Python：需与产品一致的锁定版依赖（本项目用
# C:/Users/28309/.workbuddy/binaries/python/envs/default/Scripts/python.exe）
cd tests  && python test_freeze_gate.py          # 8/8（含真实 PEG 集成负向测试）
cd tests  && python test_runner_gate.py          # 10/10（门禁拒绝 + 合法 BLOCKED 可运行）
cd tests  && python test_status_aggregation.py   # 13/13（五状态参数化 + 退出码）
cd peg    && python test_peg_checker.py          # 18/18（v1.1.0 含 vol_ratio + pm_decision）
cd dataset && python build_dataset.py            # PEG 全绿 → 发布 v1.2 清单，exit 0
cd runner  && python run_evals.py                # 五状态统计，exit 2（存在 PARTIAL/BLOCKED/NOT_RUN）
cd runner  && python run_e2e.py                  # E2E 真实 Web 端到端（需联网；自动拉起/停止服务进程）
# 产品侧新增单测（REQ-003 条件2/3 自动判断，16 用例）：
cd <产品根> && python tests/test_trade_cal.py    # 16/16（离线确定性）
```

## v1.3 变更（E2E 真实 Web 端到端，2026-10-18）

- **新增 `runner/run_e2e.py`（E2E Runner）**：以真实服务进程（uvicorn + demo 配置，
  与 deploy/start_server.bat 一致）执行离线 Runner 中全部「需服务/需联网」断言
  （15 案例：E2E-001/002/003a/b/004a/b/005a/b、L6-020/021/023、L2-008、L1-029、
  L5-031、L6-038）；真实 HTTP + 真实网络取数（新浪日线/东财备援/百度估值，
  两演示池 41 成员首轮实拉）；与最近离线运行按断言名去重合并计算案例级合并状态
  （未冻结案例保持 BLOCKED，不得 launder）。
- **v1.3 结果（run_20261018T061931Z）**：E2E 14 PASS / 1 PARTIAL（L6-038
  cross_day_reset 需跨自然日窗口）/ 0 FAIL，断言 22/0/1；
  合并 **25 PASS / 0 FAIL / 8 PARTIAL / 8 BLOCKED / 0 NOT_RUN**，退出码 2。
  案例级 NOT_RUN 清零；产品零缺陷发现。
- **披露 5 项**（详见 reports/Evals实施报告_v1.3_E2E真实Web端到端.html）：
  ① 冻结案例 http_method=POST 与产品 GET 契约不一致（候选 PEG 勘误）；
  ② akshare 东财源名称含空格（"五 粮 液"，在线/docx 同源一致）；
  ③ E2E-004/005 为 demo 边界行为（full 模式链路 M2）；
  ④ L6-038 cross_day_reset 单日不可验证；⑤ Runner 自身 3 次基础设施缺陷
  被五状态纪律拦截（未污染产品判定）。

## v1.2 变更（PM D4/D5 裁决落实，2026-10-18 下午）

- **产品侧补齐**（PM 显式定向，唯一一次产品代码变更）：
  - 新增 `src/announcement_source.py`：cninfo 公告自动拉取+缓存+可注入；
    真实网络冒烟 600768 实测 42 条公告，非披露类（业绩说明会等）正确不命中；
  - `src/trade_cal.py`：新增 `volume_anomaly_ratio`（OPEN-E09：近5日均量>2×近20日均量，
    严格大于；两窗口均以最新交易日为端点）；`early_review_flags` 三条件全自动判断
    （向后兼容：公告数据不可得时降级通用提示语）；新增 `tests/test_trade_cal.py`（16 用例）；
  - 既有产品测试 4 套（compliance/factor_engine/interpretation/pool_manager）零回归。
- **Evals 侧**：L6-037 断言 4→7（条件2 注入式三态 + 条件3 双向含恰 2.0 边界样本），
  全部真实执行；PEG 检查器 v1.1.0 新增 vol_ratio 检查类 + 多股票快照点路径；
  Schema v1.1 source_tier 新增 pm_decision。

## v1.2 复跑结果（run_20261018T051608Z）

- **PASS=12 / FAIL=0 / PARTIAL=16 / BLOCKED=8 / NOT_RUN=5**；断言级 63/0/39（可选未执行 0）；
  分风险完整通过率 P0=6/17、**P1=6/22**（+1，L6-037 转 PASS）、P2=0/2；退出码 2，判定「未完整验收」。
- 与 v1.1 对比：L6-037 由 PARTIAL（2 条 NOT_RUN）转 **PASS（7 断言全执行）**；
  断言级 +5 PASS / −2 NOT_RUN，其余 40 案例逐条不变。
- P0 必需断言未全部执行的 11 个案例（E2E-001/HO-033~035/L2-008/L3-013/L4-016~019/L6-023）
  不得声明对应风险已验证。

## 未运行 / 阻塞清单（放行前必须清零或 PM 显式处置）

- LLM Judge 全部断言（真实 LLM 未配置，IB-02 请批项）——v1.3 后唯一剩余执行类事项；
  L6-038 cross_day_reset（需跨自然日窗口）、E2E-004b panel_cached（可选预热）；
- BLOCKED 8 例：holdout×4（名单待 PM）、OPEN-E02/E03/E06、C-01、备源连通性证据；
- REQ-003 条件2/3 产品实现缺口已于 v1.2 清零（D4/D5 落实）；公告真实网络拉取为
  非确定性路径，以注入式确定性断言 + 真实冒烟日志（logs/）双重覆盖；
- E2E HTTP 层已于 v1.3 清零（15 案例真实执行，产品零缺陷）。

产品代码变更仅限 PM D4/D5 显式定向范围（announcement_source / trade_cal 条件2/3 /
对应单测）；Spec/Plan/Tasks/18 因子算法/业务规则零修改；放行判定归 release-acceptance
流程（PM 裁决）。
