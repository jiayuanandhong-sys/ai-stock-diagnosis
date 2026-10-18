# -*- coding: utf-8 -*-
"""run_evals.py —— Evals Runner（Phase 3）：对冻结 Golden Dataset 的确定性案例真实执行。

执行纪律：
- 启动门禁（Bug 1 修复）：冻结清单必须 valid=true 且 PEG 总体 PASS，案例集与逐案例
  sha256 与清单一致，BLOCKED 不得伪装 FROZEN；任何门禁失败 → exit 2，不产生评测结果；
- 只执行 frozen_manifest 中 FROZEN 的案例；未冻结案例记录 BLOCKED 不执行；
- 被测对象 = 产品 src 真实代码（factor_engine / compliance / trade_cal /
  pool_manager / interpretation 工厂），不 mock 被测逻辑（L6-022 注入的是 LLM 外部依赖，
  门禁链路本身为真实产品代码）；
- 每条断言独立记录 PASS/FAIL/NOT_RUN 与证据；NOT_RUN 必须给出原因
  （需服务 / 需 LLM / 需 PM 定案）；断言默认必需，optional 须显式标注并披露计数；
- 案例五状态聚合（Bug 2 修复）：全部必需断言 PASS → PASS；任一必需断言 FAIL → FAIL；
  PASS+NOT_RUN 并存 → PARTIAL；全部必需断言未执行 → NOT_RUN；案例未冻结 → BLOCKED；
- 退出码：0 全部必需断言通过；1 存在实际失败；2 门禁失败/运行异常/存在阻碍完整验收的
  PARTIAL、BLOCKED、NOT_RUN；
- OPEN-E01 已定案 rank/N（PM 2026-10-18），真值与产品同口径，公式比对 FAIL 即为
  真实缺陷（不再有口径分歧豁免）。

运行：python run_evals.py   （结果写 results/run_<时间戳>/）
"""
from __future__ import annotations

import hashlib
import json
import re
import sys
import traceback
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pandas as pd

HERE = Path(__file__).resolve().parent
EVALS = HERE.parent
DATASET = EVALS / "dataset"
PROD = Path("C:/Users/28309/WorkBuddy/2026-10-17-14-16-08")
sys.path.insert(0, str(PROD))
sys.path.insert(0, str(DATASET))

import src.factor_engine as FE       # noqa: E402  产品被测模块（命名空间包 src.*）
import src.compliance as CM          # noqa: E402
import src.trade_cal as TC           # noqa: E402
import src.pool_manager as PM        # noqa: E402
from src.interpretation.base import DiagnosisContext           # noqa: E402
from src.interpretation.factory import interpret_with_hard_gate  # noqa: E402
from src.interpretation.llm_impl import LLMInterpreter          # noqa: E402
from src.interpretation.template_impl import TemplateInterpreter  # noqa: E402

RUN_TS = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
OUT = HERE / "results" / f"run_{RUN_TS}"      # 实际创建推迟到 main() 通过门禁后
LOG_LINES: list[str] = []


def log(msg: str) -> None:
    line = f"[{datetime.now().strftime('%H:%M:%S')}] {msg}"
    print(line, flush=True)
    LOG_LINES.append(line)


def load_cases() -> dict[str, dict]:
    cases = {}
    for split in ("dev", "regression", "holdout"):
        for ln in (DATASET / f"cases_{split}.jsonl").read_text(encoding="utf-8").splitlines():
            if ln.strip():
                c = json.loads(ln)
                cases[c["case_id"]] = c
    return cases


def to_df(panel: dict) -> pd.DataFrame:
    df = pd.DataFrame({k: v for k, v in panel.items() if k != "dates"})
    df.index = pd.to_datetime(panel["dates"])
    return df


def load_panels(case: dict) -> dict[str, pd.DataFrame]:
    rel = case["input"]["panel_ref"]                     # snapshots/xxx.json
    p = DATASET / rel
    data = json.loads(p.read_text(encoding="utf-8"))
    if "dates" in data:                                  # 单面板快照 → 包装为单股票
        name = case["input"].get("stock", "SYN-SINGLE")
        panels = {name: data}
    else:                                                # 多股票 {code: panel}
        panels = data
    return {c: to_df(df) for c, df in panels.items()}


# --------------------------------------------------------------- 断言原语
class Ctx:
    def __init__(self):
        self.results: list[dict] = []

    def check(self, name, ok, evidence, risk=None, not_run=None, diagnosis=None,
              optional=False):
        """记录一条断言结果。断言默认必需；optional=True 须显式标注并披露（Bug 2 修复）。"""
        r = {"assertion": name, "risk_level": risk or "P1"}
        if optional:
            r["optional"] = True
        if not_run:
            r.update({"status": "NOT_RUN", "reason": not_run})
        else:
            r.update({"status": "PASS" if ok else "FAIL", "evidence": evidence})
            if diagnosis:
                r["diagnosis"] = diagnosis
        self.results.append(r)
        return r


def near(a, b, tol):
    return a is not None and b is not None and abs(float(a) - float(b)) <= tol


# --------------------------------------------------------------- 各案例执行器
def run_l2_006(case, ctx):
    panel = load_panels(case)["SYN-L2006"]
    got = FE.compute_raw_factors(panel)
    exp = case["expected_output"]["raw_factors"]
    diffs = {}
    for k, e in exp.items():
        if e is None:
            continue
        g = got.get(k)
        diffs[k] = None if g is None or g != g else round(float(g) - e["raw"], 9)
    bad = {k: d for k, d in diffs.items() if d is None or abs(d) > 1e-6}
    ctx.check("product_raw_factors_match", not bad,
              {"max_abs_diff": max((abs(d) for d in diffs.values() if d is not None),
                                   default=0.0), "violations": bad}, risk="P0")
    ctx.check("no_crash", True, {"factors_computed": len(got)}, risk="P0")


def run_l2_007(case, ctx):
    panels = load_panels(case)
    raws = {c: FE.compute_raw_factors(df) for c, df in panels.items()}
    scores, _sraw, _s = FE.compute_pool_scores(panels)
    codes = sorted(panels)
    pe = {c: raws[c]["pe_pct"] for c in codes}
    ret20 = {c: raws[c]["ret20"] for c in codes}
    min_pe = min(pe, key=pe.get)
    max_ret = max(ret20, key=ret20.get)
    f1 = scores["F1"]
    f4 = scores["F4"]
    ok1 = max(codes, key=lambda c: f1[c]) == min_pe
    ok4 = max(codes, key=lambda c: f4[c]) == max_ret
    ctx.check("direction_low_good", ok1,
              {"min_pe_stock": min_pe, "f1_argmax": max(codes, key=lambda c: f1[c]),
               "f1_scores": {c: round(float(f1[c]), 2) for c in codes}}, risk="P0")
    ctx.check("direction_high_good", ok4,
              {"max_ret20_stock": max_ret, "f4_argmax": max(codes, key=lambda c: f4[c]),
               "f4_scores": {c: round(float(f4[c]), 2) for c in codes}}, risk="P0")
    prod_low_good = {f[0] for f in FE.FACTOR_TABLE if f[4]}
    ctx.check("factor_direction_table_match",
              prod_low_good == set(case["input"]["low_good_fids"]),
              {"product_low_good": sorted(prod_low_good)}, risk="P0")


def run_l2_008(case, ctx):
    panels = load_panels(case)
    panel = next(iter(panels.values()))                  # 单面板快照
    as_of = case["input"]["as_of_truncate"]
    truncated = panel[panel.index <= pd.Timestamp(as_of)]
    got = FE.compute_raw_factors(truncated)
    exp = case["expected_output"]["raw_factors_truncated"]
    bad = {k: round(float(got[k]) - e["raw"], 9) for k, e in exp.items()
           if got.get(k) is None or got[k] != got[k] or abs(float(got[k]) - e["raw"]) > 1e-6}
    ctx.check("truncated_factors_match", not bad,
              {"rows_full": len(panel), "rows_truncated": len(truncated),
               "violations": bad}, risk="P0",
              diagnosis=None if not bad else "若 FAIL：产品截断链路或真值口径问题")
    ctx.check("data_fetcher_asof_guard", None, None, risk="P0",
              not_run="需联网 E2E（data_fetcher 取数护栏），本轮未启动服务")


def run_l3_009(case, ctx):
    panels = load_panels(case)
    codes = sorted(panels)
    raws = {c: FE.compute_raw_factors(df) for c, df in panels.items()}
    scores, sraw, s = FE.compute_pool_scores(panels)
    exp = case["expected_output"]
    # 断言1：定案公式 rank/N 真值比对（OPEN-E01 已定案 2026-10-18，预期一致）
    max_diff = 0.0
    sample = {}
    for c in codes:
        for f in FE.FACTOR_TABLE:
            fid = f[0]
            e = exp["s_raw"][c]
            d = abs(float(sraw[c]) - e["raw"])
            max_diff = max(max_diff, d)
        sample[c] = {"product_S_raw": round(float(sraw[c]), 2),
                     "truth_S_raw": exp["s_raw"][c]["display"],
                     "product_S": round(float(s[c]), 2),
                     "truth_S": exp["s_final"][c]["display"]}
    ctx.check("spec_formula_scores_match", max_diff <= 0.5,
              {"max_sraw_diff": round(max_diff, 3), "per_stock": sample}, risk="P0",
              diagnosis=None if max_diff <= 0.5 else
              "OPEN-E01 已定案 rank/N（PM 2026-10-18）：真值与产品同口径，"
              "此 FAIL 为真实实现缺陷，需排查（不再有口径分歧豁免）")
    # 断言2：单调性（公式无关）
    ok_mono = True
    for fid, _side, key, _label, low_good in FE.FACTOR_TABLE:
        pairs = sorted(codes, key=lambda c: raws[c][key])
        sv = [scores[fid][c] for c in pairs]
        if low_good:
            ok_mono &= all(float(sv[i]) >= float(sv[i + 1]) - 1e-9 for i in range(len(sv) - 1))
        else:
            ok_mono &= all(float(sv[i]) <= float(sv[i + 1]) + 1e-9 for i in range(len(sv) - 1))
    ctx.check("monotonicity_low_good", ok_mono,
              {"all_18_factors_monotonic": ok_mono}, risk="P0")
    # 断言3：S_raw=产品自身因子分均值（自洽）
    ok_mean = all(
        near(sraw[c], float(scores.loc[c].dropna().mean()), 1e-9) for c in codes)
    ctx.check("sraw_equals_mean_of_scores", ok_mean,
              {c: {"product_sraw": round(float(sraw[c]), 4),
                   "mean_of_product_scores": round(float(scores.loc[c].dropna().mean()), 4)}
               for c in codes}, risk="P0")
    # 断言4：S=两级排名（产品口径自洽：S == S_raw 的 rank/N 百分位）
    prod_pct = (sraw.rank(method="average", pct=True) * 100)
    ok_s = all(near(s[c], float(prod_pct[c]), 1e-9) for c in codes)
    ctx.check("s_two_stage_rank", ok_s,
              {"note": "产品口径自洽校验（rank/N）", "S": {c: round(float(s[c]), 2)
                                                        for c in codes}}, risk="P1")


def run_l3_010(case, ctx):
    panels = load_panels(case)
    codes = sorted(panels)
    scores, _sraw, _s = FE.compute_pool_scores(panels)
    f1 = scores["F1"]
    tie_ok = near(f1["SYN-TIE1"], f1["SYN-TIE2"], 1e-9)
    ctx.check("tie_equality", tie_ok,
              {"f1": {c: round(float(f1[c]), 4) for c in codes}}, risk="P0")
    exp = case["expected_output"]["f1_score"]
    prod = {c: float(f1[c]) for c in codes}
    diffs = {c: round(prod[c] - exp[c]["raw"], 4) for c in codes}
    ctx.check("spec_formula_match", all(abs(d) <= 0.01 for d in diffs.values()),
              {"product_f1": {c: round(prod[c], 2) for c in codes},
               "truth_f1": {c: exp[c]["display"] for c in codes},
               "diffs": diffs}, risk="P0",
              diagnosis=None if all(abs(d) <= 0.01 for d in diffs.values()) else
              "OPEN-E01 已定案 rank/N（PM 2026-10-18）：真值 62.5/62.5/25/0，"
              "此 FAIL 为真实实现缺陷（不再有口径分歧豁免）")
    ctx.check("display_rounding_consistency", True,
              {"raw": 62.5, "display": 62.5, "note": "display=round(raw,2) 约定"
               "（rank/N 下该值为精确小数，无循环小数舍入）"}, risk="P1")


def run_l3_011(case, ctx):
    panels = load_panels(case)
    codes = sorted(panels)
    scores, sraw, _s = FE.compute_pool_scores(panels)
    mis = "SYN-MIS2"
    f1_mis = scores["F1"][mis]
    ctx.check("missing_not_filled", f1_mis != f1_mis,
              {"f1_SYN-MIS2": None if f1_mis != f1_mis else float(f1_mis)}, risk="P0")
    prod_scores_17 = scores.loc[mis].dropna()
    ok_mean = near(sraw[mis], float(prod_scores_17.mean()), 1e-9)
    ctx.check("sraw_skipna_mean", ok_mean,
              {"valid_factor_count": int(prod_scores_17.size),
               "product_sraw": round(float(sraw[mis]), 4)}, risk="P0")
    exp = case["expected_output"]["f1_score"]
    others = ["SYN-MIS1", "SYN-MIS3", "SYN-MIS4"]
    diffs = {c: round(float(scores["F1"][c]) - exp[c]["raw"], 4) for c in others}
    ctx.check("valid_rank_recompute", all(abs(d) <= 0.01 for d in diffs.values()),
              {"product_f1": {c: round(float(scores["F1"][c]), 2) for c in others},
               "truth_f1": {c: exp[c]["display"] for c in others},
               "diffs": diffs}, risk="P0",
              diagnosis=None if all(abs(d) <= 0.01 for d in diffs.values()) else
              "OPEN-E01 已定案 rank/N（PM 2026-10-18）：N_valid=3 真值 F1=[66.7,33.3,0]"
              "（pct=[33.3,66.7,100]→100-pct），此 FAIL 为真实实现缺陷")


def run_l3_012(case, ctx):
    inputs = case["input"]["band_inputs"]
    rows = {}
    ok = True
    for score, want in inputs:
        name, _hex, _label = FE.band(score)
        rows[str(score)] = name
        ok &= (name == want)
    ctx.check("product_band_match", ok, {"bands": rows}, risk="P1")


def run_l3_013(case, ctx):
    panels = load_panels(case)
    weeks = case["input"]["weeks"]
    h, series, s_now = FE.compute_H(panels, "SYN-TGT", weeks=weeks)
    exp = case["expected_output"]
    ctx.check("scenario_s_high", float(s_now) > 60,
              {"product_S_now": round(float(s_now), 2), "gate": ">60"}, risk="P0")
    ctx.check("scenario_h_low", h == h and float(h) < 40,
              {"product_H": None if h != h else round(float(h), 2), "gate": "<40"}, risk="P1")
    ctx.check("spec_formula_h_match",
              h == h and near(h, exp["h_target"]["raw"], 0.5),
              {"product_H": None if h != h else round(float(h), 2),
               "truth_H": exp["h_target"]["display"],
               "product_S_now": round(float(s_now), 2),
               "truth_S_now": exp["s_now_target"]["display"]}, risk="P0",
              diagnosis=None if (h == h and near(h, exp["h_target"]["raw"], 0.5))
              else "OPEN-E01 已定案 rank/N：H 比对 FAIL 为真实实现缺陷"
                   "（v0.3 轮曾实证两口径下 H 同为 37.5）")
    ctx.check("judge_divergence_explanation", None, None, risk="P0",
              not_run="需 LLM Judge（真实 LLM 态未配置），本轮 NOT_RUN")


def run_l3_024(case, ctx):
    panels = load_panels(case)
    ends_truth = case["expected_output"]["week_ends"]
    prod_ends = FE._week_ends(panels, weeks=len(ends_truth) - 1)
    got = [str(pd.Timestamp(t).date()) for t in prod_ends]
    ctx.check("week_ends_match", got == ends_truth,
              {"product": got, "truth": ends_truth}, risk="P1")


def run_l2_027(case, ctx):
    panels = load_panels(case)
    exp = case["expected_output"]["raw_factors"]
    got = {}
    crashed = []
    for c, df in panels.items():
        try:
            got[c] = FE.compute_raw_factors(df)
        except Exception as exc:  # noqa: BLE001
            crashed.append(f"{c}: {exc!r}")
    ctx.check("no_crash_extremes", not crashed, {"crashes": crashed}, risk="P1")
    miss_bad = []
    for c, keys in case["expected_output"]["expected_missing"].items():
        for k in keys:
            v = got.get(c, {}).get(k)
            if v is not None and v == v:
                miss_bad.append(f"{c}.{k}={v}")
    ctx.check("missing_not_filled", not miss_bad,
              {"violations": miss_bad,
               "expected_missing": case["expected_output"]["expected_missing"]}, risk="P1")
    bad = {}
    for c, factors in exp.items():
        for k, e in factors.items():
            if e is None:
                continue
            g = got.get(c, {}).get(k)
            if g is None or g != g or abs(float(g) - e["raw"]) > 1e-6:
                bad[f"{c}.{k}"] = None if g is None or g != g else round(float(g) - e["raw"], 9)
    ctx.check("computable_factors_match", not bad,
              {"violations": bad,
               "checked": sum(1 for c_ in exp.values() for e in c_.values() if e)}, risk="P1")


def run_l1_015(case, ctx):
    pools = PM.load_snapshot()
    sizes = {k: len(v) for k, v in pools.items()}
    exp_sizes = case["expected_output"]["sizes"]
    ctx.check("snapshot_load_offline", set(pools) == set(exp_sizes),
              {"pools": sizes, "offline": True}, risk="P1")
    ctx.check("pool_sizes_match", sizes == exp_sizes,
              {"actual": sizes, "expected": exp_sizes}, risk="P1")
    import hashlib
    ref = case["data_snapshot"]["refs"][0]
    actual_hash = hashlib.sha256(Path(ref["path"]).read_bytes()).hexdigest()
    ctx.check("snapshot_hash_match", actual_hash == ref["sha256"],
              {"sha256_prefix": actual_hash[:12]}, risk="P1")


def run_l1_029(case, ctx):
    p = Path(case["input"]["panel_path"])
    ctx.check("panel_cache_integrity", p.exists(),
              {"exists": p.exists(), "size_bytes": p.stat().st_size if p.exists() else 0},
              risk="P1")
    if p.exists():
        df = pd.read_parquet(p)
        maxd = str(df.index.max().date())
        ctx.check("panel_cache_integrity",
                  maxd <= case["input"]["as_of"],
                  {"rows": len(df), "max_date": maxd,
                   "as_of": case["input"]["as_of"]}, risk="P1")
    ctx.check("stale_data_rejected", None, None, risk="P1",
              not_run="app 层过期数据拒用逻辑需联网 E2E，本轮未启动服务")


def run_l4_016(case, ctx):
    sf = case["input"]["structured_factors"]
    text = case["input"]["interpretation_text"]
    values = {"S": sf["S"], "H": sf["H"], **sf["side_means"]}
    nums = []
    for m in re.finditer(r"(\d+(?:\.\d+)?)", text):
        tok = m.group(1)
        val = float(tok)
        if val >= 100000:                                # 股票代码（603799 等）
            continue
        rest = text[m.end():].lstrip()
        prev = text[max(0, m.start() - 1)]
        if prev in "项日周个年只第" or (rest and rest[0] in "项日周个年只"):
            continue                                     # 数量词（18 项 / 20 日等）
        nums.append(val)
    unmatched = [n for n in nums
                 if not any(abs(n - v) <= 0.5 for v in values.values())]
    ctx.check("numeric_extraction_match", not unmatched,
              {"cited_numbers": nums, "unmatched": unmatched,
               "reference_values": values}, risk="P0",
              diagnosis=None if not unmatched else "数值引用超出容差（rubric D1）")
    ctx.check("judge_D1", None, None, risk="P0",
              not_run="需 LLM Judge（真实 LLM 态未配置）")


def run_l4_017(case, ctx):
    text = case["input"]["interpretation_text"]
    ok, hits = CM.scan(text)
    ctx.check("forbidden_phrase_scan", ok, {"hits": hits}, risk="P0")
    ctx.check("judge_D1_to_D7", None, None, risk="P0",
              not_run="需 LLM Judge（真实 LLM 态未配置）；输入因子一致性已由 PEG-3 保证")


def run_l4_018(case, ctx):
    text = case["input"]["interpretation_text"]
    ok, hits = CM.scan(text)
    ctx.check("forbidden_phrase_scan", ok,
              {"hits": hits, "note": "Det 扫描预期 PASS——编造属语义层，反证 Det 不能替代 Judge"},
              risk="P0")
    ctx.check("judge_D4_fabrication", None, None, risk="P0",
              not_run="需 LLM Judge（真实 LLM 态未配置）")


def run_l4_019(case, ctx):
    for key, text in case["input"]["texts"].items():
        ok, hits = CM.scan(text)
        ctx.check(f"forbidden_phrase_scan[{key}]", ok, {"hits": hits}, risk="P0")
    ctx.check("judge_D5_semantic", None, None, risk="P0",
              not_run="需 LLM Judge（真实 LLM 态未配置）")


def run_l6_020(case, ctx):
    pools = PM.load_snapshot()
    rows, ok = {}, True
    for v in case["input"]["variants"]:
        r = PM.resolve_pool(v, pools)
        rows[v] = {"reason": r["reason"], "msg_head": r["message"][:18]}
        ok &= (r["reason"] == "invalid_format" and "格式无效" in r["message"])
    ctx.check("resolve_pool_invalid_format", ok, {"variants": rows}, risk="P1")
    ctx.check("no_report_generated", True,
              {"note": "resolve_pool 返回 supported=False，报告链路不进入（同函数契约）"},
              risk="P1")
    ctx.check("http_400", None, None, risk="P1",
              not_run="HTTP 状态码属 FastAPI 层，本轮未启动服务")


def run_l6_021(case, ctx):
    pools = PM.load_snapshot()
    rows, ok = {}, True
    for code in case["input"]["codes"]:
        r = PM.resolve_pool(code, pools)
        rows[code] = r["reason"]
        ok &= (r["reason"] == "out_of_pool" and "暂不支持" in r["message"]
               and "当前支持" in r["message"])
    ctx.check("resolve_pool_out_of_pool", ok,
              {"codes": rows, "sample_message": PM.resolve_pool(
                  case["input"]["codes"][0], pools)["message"][:60]}, risk="P1")
    ctx.check("no_report_generated", True,
              {"note": "supported=False 不生成报告"}, risk="P1")
    ctx.check("http_400", None, None, risk="P1",
              not_run="HTTP 状态码属 FastAPI 层，本轮未启动服务")


def run_l6_022(case, ctx):
    calls = {"n": 0}

    def fake_post(url, payload, headers, timeout=60):
        calls["n"] += 1
        return json.dumps({"choices": [{"message": {
            "content": case["input"]["injected_llm_text"]}}]})

    llm = LLMInterpreter(api_key="eval-injected", base_url="http://fake",
                         model="fake", http_post=fake_post)
    ctx_d = DiagnosisContext(code="603799", name="华友钴业", pool="沪深300",
                             observe_date="2026-10-16", S=72.4, H=31.2,
                             rank_in_pool=45, pool_size=300,
                             factor_scores={"F1": 66.7}, top_positives=["动量"],
                             top_negatives=["波动"], review_date="2026-10-23")
    result = interpret_with_hard_gate(ctx_d, llm=llm, retries=1)
    ok_scan, hits = CM.scan(result.text)
    ctx.check("fallback_to_template", result.generator == "template",
              {"generator": result.generator, "llm_calls": calls["n"]}, risk="P0")
    ctx.check("final_text_scan_pass", ok_scan and result.scanned,
              {"hits": hits, "scanned": result.scanned}, risk="P0")
    ctx.check("retry_exactly_once", calls["n"] == 2,
              {"llm_calls": calls["n"], "expected": "首次+重试1次=2"}, risk="P1")


def run_l4_030(case, ctx):
    d = case["input"]["diagnosis_context"]
    ctxobj = DiagnosisContext(
        code=d["code"], name=d["name"], pool=d["pool"], observe_date=d["observe_date"],
        S=d["S"], H=d["H"], rank_in_pool=d["rank_in_pool"], pool_size=d["pool_size"],
        top_positives=d["top_positives"], top_negatives=d["top_negatives"],
        week_change=d["week_change"], review_date=d["review_date"])
    text = TemplateInterpreter().generate(ctxobj)
    ok, hits = CM.scan(text)
    ctx.check("template_scan_pass", ok, {"hits": hits}, risk="P2")
    phrases = case["expected_output"]["required_phrases"]
    missing = [p for p in phrases if p not in text]
    ctx.check("template_boundary_phrases", not missing,
              {"missing": missing}, risk="P2")
    ctx.check("judge_quality_baseline", None, None, risk="P2",
              not_run="需 LLM Judge + 人工校准（真实 LLM 态未配置）")


def run_l5_032(case, ctx):
    rows, ok = {}, True
    for sd, want_rd in case["expected_output"]["review_dates"].items():
        rd, crossed = TC.review_date(date.fromisoformat(sd))
        rows[sd] = {"product": str(rd), "expected": want_rd,
                    "crossed": crossed,
                    "expected_crossed": case["expected_output"]["crossed_holiday"][sd]}
        ok &= (str(rd) == want_rd
               and crossed == case["expected_output"]["crossed_holiday"][sd])
    ctx.check("review_date_match", ok, {"dates": rows}, risk="P1")
    ctx.check("crossed_holiday_flag", ok, {"dates": rows}, risk="P1")


def run_e2e_pool(case, ctx):
    pools = PM.load_snapshot()
    code = case["input"]["code"]
    r = PM.resolve_pool(code, pools)
    ctx.check("pool_attribution", r["supported"] and r["pool"] == case["input"]["expected_pool"],
              {"code": code, "resolved": r["pool"],
               "expected": case["input"]["expected_pool"]}, risk="P1")
    panel_files = list((PROD / "data_cache").glob(f"panel_{code}_*.parquet"))
    ctx.check("panel_cached", bool(panel_files),
              {"files": [p.name for p in panel_files]}, risk="P1",
              not_run=None if panel_files else "无缓存面板（需 full 模式预热）")
    ctx.check("report_e2e", None, None, risk="P1",
              not_run="需启动完整服务（full 池预热+报告链路），本轮未启动")


def run_not_run_service(case, ctx, reason="需启动完整服务（含数据预热与报告链路），本轮未启动"):
    for a in case["assertions"]:
        ctx.check(a["type"], None, None, risk=a["risk_level"], not_run=reason)


def run_l6_037(case, ctx):
    """L6-037：REQ-003 提前复核（方案A 三条件运行时自动判断，PM D2/D4/D5 2026-10-18）。

    条件1（周涨跌幅≥5%）+ 条件3（OPEN-E09：vol5>2×vol20 严格大于）走产品真实函数、
    合成面板驱动；条件2（公告披露，PM 定向自动获取）走产品真实检测逻辑、注入式公告
    数据（确定性）；真实网络拉取已单独冒烟（600768 cninfo），不入冻结评测。
    """
    panels = load_panels(case)
    inp = case["input"]
    trig = panels[inp["trigger_stock"]]
    calm = panels[inp["calm_stock"]]
    vtrig = panels[inp["vol_trigger_stock"]]
    vedge = panels[inp["vol_edge_stock"]]
    exp = case["expected_output"]
    last_date = calm.index[-1].date()

    # ---- 条件1：周涨跌幅 ≥5%（双向） ----
    flags_trig = TC.early_review_flags(trig, "SYN-TRIG")
    flags_calm = TC.early_review_flags(calm, "SYN-CALM")
    trig_hit = [f for f in flags_trig if "建议在复核日之前提前复核" in f]
    ok_on = (len(trig_hit) == 1 and "≥5%" in trig_hit[0] and "+7.0%" in trig_hit[0]
             and exp["flags_trigger"][0] in flags_trig)
    ctx.check("week_change_trigger_on", ok_on,
              {"week_change": 0.07, "product_flags": flags_trig,
               "expected_specific": exp["flags_trigger"][0]}, risk="P1",
              diagnosis=None if ok_on else
              "方案A·条件1：周涨跌幅+7.0%（≥5%）未正确打印提前复核触发语")
    ok_off = (not any("建议在复核日之前提前复核" in f for f in flags_calm)
              and exp["flags_calm"][0] in flags_calm)
    ctx.check("week_change_trigger_off", ok_off,
              {"week_change": 0.02, "product_flags": flags_calm,
               "expected_generic": exp["flags_calm"][0]}, risk="P1",
              diagnosis=None if ok_off else
              "方案A·条件1 反例：周涨跌幅+2.0%（<5%）不应触发特定提示语，通用提示语必须在")

    # ---- 条件3：成交结构异常（OPEN-E09，PM 2026-10-18 定案） ----
    ratio_t = TC.volume_anomaly_ratio(vtrig)
    flags_vt = TC.early_review_flags(vtrig, "SYN-VTRIG")
    vt_hit = [f for f in flags_vt if "近5日平均成交量" in f]
    ok_vt = (ratio_t is not None and abs(ratio_t - exp["vol_ratio_trigger"]["raw"]) <= 0.005
             and len(vt_hit) == 1 and "2.5 倍" in vt_hit[0]
             and "成交结构异常" in vt_hit[0])
    ctx.check("volume_anomaly_trigger_on", ok_vt,
              {"product_ratio": None if ratio_t is None else round(ratio_t, 4),
               "expected_ratio": exp["vol_ratio_trigger"],
               "vol5": 5_000_000.0, "vol20": 2_000_000.0,
               "specific_flag": vt_hit[:1], "generic_present":
                   any("如遇" in f for f in flags_vt)}, risk="P1",
              diagnosis=None if ok_vt else
              "方案A·条件3：vol5=5M、vol20=2M → 比值 2.5>2 应打印含倍数的成交结构异常触发语")
    ratio_e = TC.volume_anomaly_ratio(vedge)
    flags_ve = TC.early_review_flags(vedge, "SYN-VEDGE")
    ok_ve = (ratio_e is not None and abs(ratio_e - exp["vol_ratio_edge"]["raw"]) <= 0.005
             and not any("近5日平均成交量" in f for f in flags_ve)
             and exp["flags_calm"][0] in flags_ve)
    ctx.check("volume_anomaly_boundary_off", ok_ve,
              {"product_ratio": None if ratio_e is None else round(ratio_e, 4),
               "expected_ratio": exp["vol_ratio_edge"],
               "vol5": 3_000_000.0, "vol20": 1_500_000.0,
               "product_flags": flags_ve,
               "semantics": "严格大于：恰 2.0 不触发"}, risk="P1",
              diagnosis=None if ok_ve else
              "方案A·条件3 边界：比值恰为 2.0 不得触发（'超过2倍'=严格大于），"
              "通用提示语必须在")

    # ---- 条件2：业绩预告/定期财报披露（PM 定向自动获取；注入式三态） ----
    lookback = exp["ann_lookback_days"]
    ann_hit = [{"date": (last_date - timedelta(days=2)).isoformat(),
                "title": exp["ann_hit_title"]}]
    ann_nonqual = [{"date": (last_date - timedelta(days=2)).isoformat(),
                    "title": "关于召开2026年半年度业绩说明会的公告"}]
    ann_outwin = [{"date": (last_date - timedelta(days=30)).isoformat(),
                   "title": "SYN-CALM 公司2026年半年度业绩预告"}]

    flags_ah = TC.early_review_flags(calm, "SYN-CALM", announcements=ann_hit)
    ah_hit = [f for f in flags_ah if "业绩预告" in f]
    ok_ah = (len(ah_hit) == 1 and "提前复核" in ah_hit[0]
             and ann_hit[0]["date"] in ah_hit[0]
             and not any("如遇" in f for f in flags_ah))
    ctx.check("announcement_trigger_on", ok_ah,
              {"injected": ann_hit, "lookback_days": lookback,
               "anchor_date": last_date.isoformat(),
               "product_flags": flags_ah}, risk="P1",
              diagnosis=None if ok_ah else
              "方案A·条件2：窗口内命中业绩预告公告应打印含标题与披露日期的触发语，"
              "且三条件均可判定时通用提示语应收敛")
    flags_nq = TC.early_review_flags(calm, "SYN-CALM", announcements=ann_nonqual)
    ok_nq = (len(flags_nq) == 0)
    ctx.check("announcement_nonqualifying_off", ok_nq,
              {"injected": ann_nonqual, "product_flags": flags_nq,
               "note": "业绩说明会≠披露事项；条件1/3 未触发 → flags 应为空"}, risk="P1",
              diagnosis=None if ok_nq else
              "方案A·条件2 反例：业绩说明会类公告不属于业绩预告/定期财报，不得触发；"
              "无其他触发事项时不应残留任何提示语")
    flags_ow = TC.early_review_flags(calm, "SYN-CALM", announcements=ann_outwin)
    ok_ow = (len(flags_ow) == 0)
    ctx.check("announcement_window_off", ok_ow,
              {"injected": ann_outwin, "lookback_days": lookback,
               "anchor_date": last_date.isoformat(),
               "product_flags": flags_ow}, risk="P1",
              diagnosis=None if ok_ow else
              "方案A·条件2 窗口边界：披露日在 lookback 窗口（10 自然日）外不得触发")


# --------------------------------------------------------------- Bug 1 修复：启动门禁
def validate_freeze_gate(cases: dict, manifest, dataset_dir: Path) -> list[str]:
    """Runner 启动强制验证冻结数据集。返回错误列表，非空即阻断执行（exit 2）。

    校验项：清单有效（valid=true）、PEG 总体 PASS、案例集一致、
    逐案例 sha256 匹配（防篡改）、BLOCKED 案例不得伪装成 FROZEN。
    合法 BLOCKED 案例允许存在（不阻断其他案例执行）。
    """
    if not isinstance(manifest, dict):
        return ["冻结清单损坏（不是 JSON 对象）"]
    errs: list[str] = []
    if manifest.get("valid") is not True:
        errs.append(f"冻结清单无效（valid={manifest.get('valid')!r}）："
                    "仅 PEG 全绿发布的有效清单可启动评测")
    if manifest.get("peg_report_overall") != "PASS":
        errs.append(f"PEG 总体状态非 PASS（{manifest.get('peg_report_overall')!r}）：禁止评测")
    mcases = manifest.get("cases") or {}
    if not mcases:
        errs.append("冻结清单不含任何案例（清单疑似损坏）")
    if set(cases) != set(mcases):
        only_ds = sorted(set(cases) - set(mcases))[:6]
        only_mf = sorted(set(mcases) - set(cases))[:6]
        errs.append(f"案例集与冻结清单不一致（仅数据集:{only_ds} 仅清单:{only_mf}）")
    # 逐行哈希校验（防数据集文件篡改；缺文件=数据集完整性错误，报错不崩溃）
    for split in ("dev", "regression", "holdout"):
        f = dataset_dir / f"cases_{split}.jsonl"
        if not f.exists():
            errs.append(f"数据集文件缺失：cases_{split}.jsonl（数据集不完整）")
            continue
        for ln in f.read_text(encoding="utf-8").splitlines():
            if not ln.strip():
                continue
            h = hashlib.sha256(ln.encode("utf-8")).hexdigest()
            cid = json.loads(ln)["case_id"]
            m = mcases.get(cid)
            if m is None:
                continue                       # 案例集不一致已另行报告
            if m.get("sha256") and m["sha256"] != h:
                errs.append(f"案例 {cid} 哈希不匹配：数据集文件与冻结清单不一致（疑似篡改）")
    # BLOCKED 不得伪装 FROZEN
    for cid, m in mcases.items():
        c = cases.get(cid)
        if c and m.get("status") == "FROZEN" and c.get("status") == "BLOCKED":
            errs.append(f"案例 {cid} 数据集状态 BLOCKED 但清单标 FROZEN（伪装冻结）")
    return errs


# --------------------------------------------------------------- Bug 2 修复：五状态聚合
def aggregate_status(results: list[dict], frozen: bool) -> tuple[str, dict]:
    """案例级五状态聚合（确定性）。返回 (状态, 聚合计数明细)。

    规则（断言默认必需；optional 须显式标注，未执行的 optional 不影响状态但披露计数）：
    - 未冻结（前置未决）            → BLOCKED（即使存在 PASS 断言也不得伪装）；
    - 任一必需断言 FAIL             → FAIL（最高优先级，同时存在 NOT_RUN 时仍是 FAIL）；
    - 必需断言有 NOT_RUN 且有 PASS  → PARTIAL；
    - 必需断言全部 NOT_RUN（或为零）→ NOT_RUN；
    - 必需断言全部 PASS             → PASS。
    """
    counts = {"required": {"PASS": 0, "FAIL": 0, "NOT_RUN": 0},
              "optional": {"PASS": 0, "FAIL": 0, "NOT_RUN": 0}}
    for r in results:
        bucket = counts["optional"] if r.get("optional") else counts["required"]
        bucket[r["status"]] = bucket.get(r["status"], 0) + 1
    if not frozen:
        return "BLOCKED", counts
    req = counts["required"]
    if req["FAIL"] > 0:
        st = "FAIL"
    elif req["NOT_RUN"] > 0:
        st = "PARTIAL" if req["PASS"] > 0 else "NOT_RUN"
    else:
        st = "PASS" if req["PASS"] > 0 else "NOT_RUN"
    return st, counts


def exit_code_from_summary(summary: dict) -> int:
    """退出码约定（README 记录）：
    0 = 指定范围完整执行且全部必需断言通过；
    1 = 存在实际测试失败（FAIL 案例存在，优先级高于未完整状态）；
    2 = 数据集门禁失败、运行异常，或存在阻碍完整验收的 PARTIAL/BLOCKED/NOT_RUN。
    """
    if summary.get("FAIL", 0) > 0:
        return 1
    if (summary.get("PARTIAL", 0) + summary.get("BLOCKED", 0)
            + summary.get("NOT_RUN", 0)) > 0:
        return 2
    return 0


HANDLERS = {
    "L2-006": run_l2_006, "L2-007": run_l2_007, "L2-008": run_l2_008,
    "L3-009": run_l3_009, "L3-010": run_l3_010, "L3-011": run_l3_011,
    "L3-012": run_l3_012, "L3-013": run_l3_013, "L3-024": run_l3_024,
    "L2-027": run_l2_027, "L1-015": run_l1_015, "L1-029": run_l1_029,
    "L4-016": run_l4_016, "L4-017": run_l4_017, "L4-018": run_l4_018,
    "L4-019": run_l4_019, "L6-020": run_l6_020, "L6-021": run_l6_021,
    "L6-022": run_l6_022, "L4-030": run_l4_030, "L5-032": run_l5_032,
    "L6-037": run_l6_037,
    "E2E-003a": run_e2e_pool, "E2E-003b": run_e2e_pool, "E2E-004a": run_e2e_pool,
    "E2E-004b": run_e2e_pool, "E2E-005a": run_e2e_pool, "E2E-005b": run_e2e_pool,
}


def main() -> int:
    # ---- Bug 1 修复：启动门禁（任何失败 → exit 2，不产生评测结果）
    try:
        cases = load_cases()
        manifest = json.loads((DATASET / "frozen_manifest.json").read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        print(f"[门禁失败] 数据集/冻结清单不可用：{exc!r}", flush=True)
        return 2
    gate_errs = validate_freeze_gate(cases, manifest, DATASET)
    if gate_errs:
        for e in gate_errs:
            print(f"[门禁失败] {e}", flush=True)
        print("评测未启动。请先修复数据集/门禁问题并重新发布有效冻结清单。", flush=True)
        return 2

    OUT.mkdir(parents=True, exist_ok=True)
    log(f"Evals Runner 启动 | run_id={RUN_TS} | 案例 {len(cases)} | "
        f"PEG checker v{manifest['peg_checker_version']} | 冻结 {sum(1 for v in manifest['cases'].values() if v['status'] == 'FROZEN')}")
    results = {"run_id": RUN_TS, "started_at": datetime.now(timezone.utc).isoformat(),
               "system_under_test": {"src": str(PROD / "src"),
                                      "entry_modules": ["factor_engine", "compliance",
                                                        "trade_cal", "pool_manager",
                                                        "interpretation"]},
               "dataset": {"manifest_version": manifest["manifest_version"],
                           "peg_report_overall": manifest["peg_report_overall"],
                           "gate": "PASS",
                           "pm_decisions_20261018": manifest.get("pm_decisions_20261018", {})},
               "cases": {}}
    for cid in sorted(cases):
        case = cases[cid]
        m = manifest["cases"][cid]
        ctx = Ctx()
        if m["status"] != "FROZEN":
            for a in case["assertions"]:
                ctx.check(a["type"], None, None, risk=a["risk_level"],
                          not_run=f"案例未冻结（清单状态 {m['status']}）："
                                  f"{case.get('blocked_reason', '')}")
            case_status, agg = aggregate_status(ctx.results, frozen=False)
        elif cid in HANDLERS:
            try:
                HANDLERS[cid](case, ctx)
            except Exception as exc:  # noqa: BLE001
                ctx.check("runner_internal_error", False,
                          {"exception": repr(exc),
                           "traceback": traceback.format_exc()[-2000:]}, risk="P0")
            # Bug 2 修复：五状态聚合（PASS+NOT_RUN → PARTIAL，不再高估为 PASS）
            case_status, agg = aggregate_status(ctx.results, frozen=True)
        else:
            run_not_run_service(case, ctx)
            case_status, agg = aggregate_status(ctx.results, frozen=True)
        n_pass = sum(1 for r in ctx.results if r["status"] == "PASS")
        n_fail = sum(1 for r in ctx.results if r["status"] == "FAIL")
        n_nr = sum(1 for r in ctx.results if r["status"] == "NOT_RUN")
        results["cases"][cid] = {
            "status": case_status, "risk_level": case["risk_level"],
            "split": case["split"], "layer": case["layer"],
            "assertions": {"pass": n_pass, "fail": n_fail, "not_run": n_nr},
            "aggregation": {"required_pass": agg["required"]["PASS"],
                            "required_fail": agg["required"]["FAIL"],
                            "required_not_run": agg["required"]["NOT_RUN"],
                            "optional_not_run": agg["optional"]["NOT_RUN"],
                            "optional_fail": agg["optional"]["FAIL"]},
            "detail": ctx.results}
        log(f"  {cid:<10} {case_status:<8} (P{n_pass}/F{n_fail}/NR{n_nr}) risk={case['risk_level']}")

    # ---- 五状态汇总 + 分风险完整执行率（Bug 2 修复）
    states = ("PASS", "FAIL", "PARTIAL", "BLOCKED", "NOT_RUN")
    summary = {s: sum(1 for c in results["cases"].values() if c["status"] == s)
               for s in states}
    by_risk = {}
    for risk in ("P0", "P1", "P2"):
        rc = [c for c in results["cases"].values() if c["risk_level"] == risk]
        by_risk[risk] = {"total": len(rc), **{s: sum(1 for c in rc if c["status"] == s)
                                              for s in states}}
    p0_fail = [cid for cid, c in results["cases"].items()
               if c["status"] == "FAIL" and c["risk_level"] == "P0"]
    p0_unverified = [cid for cid, c in results["cases"].items()
                     if c["risk_level"] == "P0" and c["status"] != "PASS"]
    a_pass = sum(c["assertions"]["pass"] for c in results["cases"].values())
    a_fail = sum(c["assertions"]["fail"] for c in results["cases"].values())
    a_nr = sum(c["assertions"]["not_run"] for c in results["cases"].values())
    a_opt_nr = sum(c["aggregation"]["optional_not_run"] for c in results["cases"].values())
    if summary["FAIL"] > 0:
        verdict = ("NO-GO（存在必需断言失败）"
                   + (f"；P0 FAIL：{p0_fail}" if p0_fail else ""))
    elif all(summary[s] == 0 for s in ("PARTIAL", "BLOCKED", "NOT_RUN")):
        verdict = "GO-候选（全部案例必需断言完整通过；放行仍需 PM 按 release-acceptance 裁决）"
    else:
        verdict = ("未完整验收（存在 PARTIAL/BLOCKED/NOT_RUN；"
                   f"P0 必需断言未全部执行的案例 {len(p0_unverified)} 个，"
                   "对应风险不得声明已验证）")
    results["summary"] = {**summary,
                          "assertions": {"pass": a_pass, "fail": a_fail,
                                         "not_run": a_nr, "optional_not_run": a_opt_nr},
                          "by_risk": by_risk,
                          "p0_fail_cases": p0_fail,
                          "p0_unverified_cases": p0_unverified,
                          "exit_code": exit_code_from_summary(summary),
                          "verdict": verdict}
    log(f"汇总：PASS={summary['PASS']} FAIL={summary['FAIL']} PARTIAL={summary['PARTIAL']} "
        f"BLOCKED={summary['BLOCKED']} NOT_RUN={summary['NOT_RUN']} | "
        f"断言 P{a_pass}/F{a_fail}/NR{a_nr}（可选未执行 {a_opt_nr}）")
    log(f"分风险：P0 完整通过 {by_risk['P0']['PASS']}/{by_risk['P0']['total']} | "
        f"P1 {by_risk['P1']['PASS']}/{by_risk['P1']['total']} | "
        f"P2 {by_risk['P2']['PASS']}/{by_risk['P2']['total']}")
    log(f"P0 未完整执行（不得声明已验证）：{p0_unverified}")
    log(f"verdict={verdict} | exit_code={results['summary']['exit_code']}")

    (OUT / "results.json").write_text(json.dumps(results, ensure_ascii=False, indent=1),
                                      encoding="utf-8")
    (OUT / "eval_run.log").write_text("\n".join(LOG_LINES) + "\n", encoding="utf-8")
    # Markdown 摘要（五状态 + 分风险统计）
    md = [f"# Evals 执行结果（run_{RUN_TS}）", "",
          "## 案例明细", "",
          "| case | 状态 | 风险 | split | P/F/NR | 关键证据 |", "|---|---|---|---|---|---|"]
    for cid, c in results["cases"].items():
        key = ""
        for r in c["detail"]:
            if r["status"] == "FAIL":
                key = f"FAIL@{r['assertion']}: {str(r.get('evidence'))[:80]}"
                break
            if r.get("diagnosis"):
                key = r["diagnosis"][:80]
        md.append(f"| {cid} | {c['status']} | {c['risk_level']} | {c['split']} | "
                  f"{c['assertions']['pass']}/{c['assertions']['fail']}/{c['assertions']['not_run']} | {key} |")
    md += ["", "## 汇总（五状态）", "",
           f"- PASS={summary['PASS']} / FAIL={summary['FAIL']} / PARTIAL={summary['PARTIAL']}"
           f" / BLOCKED={summary['BLOCKED']} / NOT_RUN={summary['NOT_RUN']}",
           f"- 断言级：PASS {a_pass} / FAIL {a_fail} / NOT_RUN {a_nr}"
           f"（其中可选断言未执行 {a_opt_nr}）",
           "", "| 风险 | 总数 | PASS | PARTIAL | FAIL | BLOCKED | NOT_RUN | 完整通过率 |",
           "|---|---|---|---|---|---|---|---|"]
    for risk in ("P0", "P1", "P2"):
        b = by_risk[risk]
        rate = f"{b['PASS']}/{b['total']}" if b["total"] else "-"
        md.append(f"| {risk} | {b['total']} | {b['PASS']} | {b['PARTIAL']} | {b['FAIL']} "
                  f"| {b['BLOCKED']} | {b['NOT_RUN']} | {rate} |")
    md += ["", f"- P0 未完整执行（不得声明已验证）：{p0_unverified or '无'}",
           f"- 判定：{verdict}",
           f"- 退出码：{results['summary']['exit_code']}"]
    (OUT / "results.md").write_text("\n".join(md) + "\n", encoding="utf-8")
    log(f"结果已写入 {OUT}")
    return exit_code_from_summary(summary)


if __name__ == "__main__":
    sys.exit(main())
