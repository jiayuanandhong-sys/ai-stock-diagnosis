# -*- coding: utf-8 -*-
"""build_dataset.py —— Golden Dataset 构建 + PEG 正式校验 + 冻结（Phase 2）。

修正落实（v0.2 → v0.3 实施）：
  #1 PEG 先行：本脚本在冻结前调用已通过单元测试的 peg_checker（Phase 1 产物）；
  #2/#7 双精度：expected 数值统一 {"raw", "display", "dp"}，L3-010/L4-017 已按此修正；
  #3 L6-037 P2→P1（REQ-003 已批准需求，不得因缺独立 AC 降为非阻断）；
  #4 L6-020 source: real_authorized → expert_curated（人工构造负例+实测证据，非真实行情数据）；
  #5 E2E-002 P0→P1（断言级风险标记，案例级=最大断言级；数据新鲜度 P0 风险由 L1-029 双态规则承载）；
  #6 schema 统一：split/truth_origin/requirement_id/assertions[].risk_level 全部按 eval_case.schema.json v1.0；
  #8 证据区分：所有引用的既有证据文件带 sha256；本轮新计算真值全部来自 truth_lib 独立实现。

PM 裁决落实（2026-10-18，本轮 v1.0）：
  D1 OPEN-E01 定案 rank/N：truth_lib.pct_from_rank / peg_checker._pct_from_ranks 同步切换；
     L3-010 标准答案 83.33 → 62.5（历史 66.67 错误样本继续保留为 PEG 回归用例）；
  D2 OPEN-E08 定案方案A：L6-037 由 BLOCKED 转 FROZEN——条件1（周涨跌幅≥5%）产品已实现
     可真实执行；条件2（业绩预告自动判断）产品缺数据源、条件3（成交异常阈值）口径未定义
     （新开 OPEN-E09），两断言如实 NOT_RUN；
  D3 风险分级 v0.3 批准：P0/P1/P2 体系正式生效（dataset_stats.pm_decisions 留痕）。

PM 裁决落实（2026-10-18 下午，本轮 v1.2）：
  D4 REQ-003 条件2 定向（自动获取公告/财报发布时间）：产品补齐 src/announcement_source.py
     （cninfo 拉取+缓存+可注入），L6-037 条件2 断言由 NOT_RUN 升级为真实执行（注入式三态）；
  D5 OPEN-E09 定案（近5日均量 > 2×近20日均量，严格大于）：产品补齐
     trade_cal.volume_anomaly_ratio，L6-037 条件3 断言升级为真实执行（含恰 2.0 边界样本）；
     source_tier 枚举新增第四级 pm_decision（Schema v1.1）。

产出：cases_dev.jsonl / cases_regression.jsonl / cases_holdout.jsonl
      snapshots/*.json（合成面板，带 sha256 引用）
      dataset_stats.json（统计边车）→ PEG 校验 → frozen_manifest.json
"""
from __future__ import annotations

import hashlib
import json
import sys
from datetime import date, datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
SNAP = HERE / "snapshots"
SNAP.mkdir(exist_ok=True)
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent / "peg"))
import truth_lib as T  # noqa: E402
import peg_checker as PC  # noqa: E402

PROD = Path("C:/Users/28309/WorkBuddy/2026-10-17-14-16-08")
SPEC = PROD / "sdd-stock-diagnosis" / "spec.md"
TASKS = PROD / "sdd-stock-diagnosis" / "tasks.md"
POOL_SNAP = PROD / "data_cache" / "pool_snapshots" / "pool_list_2026-10-17.json"
TRADE_CAL = PROD / "data_cache" / "trade_calendar.json"
PANEL_603799 = PROD / "data_cache" / "panel_603799_20240901_20261017.parquet"

F18 = T.FIDS


def num(raw, dp=2):
    if raw is None or raw != raw:
        return None
    return {"raw": raw, "display": round(raw, dp), "dp": dp}


def sha256_file(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def save_snapshot(name: str, obj: dict) -> dict:
    p = SNAP / f"{name}.json"
    p.write_text(json.dumps(obj, ensure_ascii=False), encoding="utf-8")
    return {"path": f"snapshots/{name}.json", "sha256": sha256_file(p),
            "note": f"synthetic deterministic panel {name}"}


def ref_file(p: Path, note: str) -> dict:
    return {"path": str(p), "sha256": sha256_file(p), "note": note}


def mkcase(cid, layer, risk, split, input_, expected, assertions, evaluator,
           truth_method, truth_detail, source="synthetic", status="FROZEN",
           snapshot=None, as_of="2026-10-16", req=None, formula=None, notes=None,
           blocked_reason=None, peg3=None, evidence_refs=None):
    snap = snapshot or {"snapshot_id": f"{cid.lower()}_default", "type":
                        "synthetic" if source == "synthetic" else "real_cached",
                        "frozen": True}
    c = {
        "case_id": cid, "requirement_id": req or [], "layer": layer,
        "risk_level": risk, "split": split, "input": input_,
        "as_of_date": as_of, "data_snapshot": snap,
        "expected_output": expected, "assertions": assertions,
        "evaluator_type": evaluator, "pass_criteria":
            "全部断言通过（断言级 risk_level 见 assertions）",
        "failure_reason": "", "source": source,
        "truth_origin": {"method": truth_method, "detail": truth_detail,
                         "evidence_refs": evidence_refs or []},
        "status": status, "notes": notes or "",
    }
    if peg3:
        c["peg3_checks"] = peg3
    if formula:
        c["formula_status"] = formula
    if blocked_reason:
        c["blocked_reason"] = blocked_reason
    return c


def A(type_, risk, tier="spec", **kw):
    d = {"type": type_, "risk_level": risk, "source_tier": tier}
    d.update(kw)
    return d


CASES: list[dict] = []


def build() -> None:
    # ================================================================ L2 层
    # ---- L2-006：F1–F18 参数化单测（P0）
    panel = T.synth_panel(seed=7, n_days=300, pe=23.5, pb=3.2, mv=8.8e10,
                          drift=0.0012, tail_vol_boost=1.35)
    snap_ref = save_snapshot("l2_006_panel", panel)
    raw = T.raw_factors(panel)
    fid2key = {"F1": "pe_pct", "F2": "pb_pct", "F3": "mv_pct", "F4": "ret20",
               "F5": "ret60", "F6": "ma250_dist", "F7": "ma_ratio", "F8": "pos52w",
               "F9": "accel", "F10": "updays", "F11": "amt20", "F12": "volx",
               "F13": "vp_corr", "F14": "turn20", "F15": "vol20", "F16": "vol60",
               "F17": "mdd60", "F18": "amp20"}
    key_map = {  # fid -> (kind, params)
        "F1": ("last_value", {"series": "pe_ttm"}), "F2": ("last_value", {"series": "pb"}),
        "F3": ("last_value", {"series": "total_mv"}),
        "F4": ("close_ratio", {"series": "close", "back": 20}),
        "F5": ("close_ratio", {"series": "close", "back": 60}),
        "F6": ("last_over_mean", {"series": "close", "window": 250}),
        "F7": ("ma_ratio", {"series": "close", "w1": 20, "w2": 60}),
        "F8": ("pos52w", {"series": "close", "window": 252}),
        "F9": ("accel", {"series": "close", "short": 5, "back": 20}),
        "F10": ("updays", {"series": "close", "window": 20}),
        "F11": ("mean_tail", {"series": "amount", "window": 20}),
        "F12": ("volx", {"series": "volume", "w1": 5, "w2": 20}),
        "F13": ("vp_corr", {"close": "close", "volume": "volume"}),
        "F14": ("mean_tail", {"series": "turnover", "window": 20}),
        "F15": ("ret_std", {"series": "close", "window": 20}),
        "F16": ("ret_std", {"series": "close", "window": 60}),
        "F17": ("mdd60", {"series": "close", "window": 60}),
        "F18": ("mean_tail", {"series": "amp", "window": 20}),
    }

    def pref(field):
        return {"$panel": "snapshots/l2_006_panel.json", "field": field}

    peg3 = [{"check": "factor_raw", "kind": kind,
             "params": {k: (pref(v) if isinstance(v, str) and v in panel else v)
                        for k, v in params.items()},
             "expected": num(raw[fid2key[fid]], 6)}
            for fid, (kind, params) in key_map.items()]
    raw18 = {fid2key[f]: num(raw[fid2key[f]], 6) for f in F18}
    CASES.append(mkcase(
        "L2-006", "L2", "P0", "dev",
        {"panel_ref": "snapshots/l2_006_panel.json", "stock": "SYN-L2006"},
        {"raw_factors": raw18},
        [A("product_raw_factors_match", "P0", tier="spec",
           note="产品 compute_raw_factors 与 truth_lib 独立值逐项比对（容差 1e-6）"),
         A("no_crash", "P0", tier="spec")],
        ["Deterministic"], "independent_implementation",
        "truth_lib.raw_factors 纯 Python 独立实现（与产品 pandas 路径独立）",
        snapshot={"snapshot_id": "l2_006_panel_v1", "type": "synthetic", "frozen": True,
                  "refs": [snap_ref]},
        req=["附表A", "BR-006"], peg3=peg3,
        notes="300 日合成面板覆盖 ma250/ret60/vol60 全窗口；18 因子原始值全部独立复算"))

    # ---- L2-007：方向统一（P0）
    panels7 = {f"SYN-DIR{k}": T.synth_panel(seed=k, n_days=40, pe=10.0 * k,
                                            pb=1.0 + 0.5 * k, mv=5e10 + k * 2e10,
                                            drift=0.0005 * k)
               for k in range(1, 5)}
    snap7 = save_snapshot("l2_007_panels", panels7)
    raws7 = {c: T.raw_factors(p) for c, p in panels7.items()}
    scores7 = T.factor_scores(raws7)
    codes7 = sorted(panels7)
    f1_exp = [num(scores7[c]["F1"]) for c in codes7]
    f4_exp = [num(scores7[c]["F4"]) for c in codes7]
    f1_ranks7 = T.avg_ranks([raws7[c]["pe_pct"] for c in codes7])
    f4_ranks7 = T.avg_ranks([raws7[c]["ret20"] for c in codes7])
    CASES.append(mkcase(
        "L2-007", "L2", "P0", "dev",
        {"panel_ref": "snapshots/l2_007_panels.json",
         "universe": sorted(panels7), "low_good_fids": [f for f in F18 if T.LOW_GOOD[f]],
         "high_good_fids": [f for f in F18 if not T.LOW_GOOD[f]]},
        {"f1_scores": f1_exp, "f4_scores": f4_exp,
         "direction_rule": "低值有利因子（F1/F2/F3/F15–F18）分=100-pct；其余分=pct"},
        [A("direction_low_good", "P0", tier="spec",
           note="PE 原始值最低的股票其 F1 因子分必须为池内最高（低值有利方向统一）"),
         A("direction_high_good", "P0", tier="spec",
           note="ret20 原始值最高的股票其 F4 因子分必须为池内最高（高值有利不反转）"),
         A("factor_direction_table_match", "P0", tier="spec",
           note="low_good 集合与附表 A 一致：7 低值有利 + 11 高值有利")],
        ["Deterministic"], "independent_implementation",
        "truth_lib 因子分管线（定案公式 rank/N）+ 附表A 方向表",
        snapshot={"snapshot_id": "l2_007_panels_v1", "type": "synthetic", "frozen": True,
                  "refs": [snap7]},
        req=["附表A", "BR-006"], formula="rank/N（OPEN-E01 已定案，PM 2026-10-18）",
        peg3=[{"check": "pct_from_ranks", "ranks": f1_ranks7, "low_good": True,
               "expected": f1_exp},
              {"check": "pct_from_ranks", "ranks": f4_ranks7, "low_good": False,
               "expected": f4_exp}]))

    # ---- L2-008：未来数据泄漏（P0）
    base = T.synth_panel(seed=21, n_days=300, pe=18.0, drift=0.0008)
    future = T.synth_panel(seed=99, n_days=15, start="2026-07-01", pe=99.9,
                           drift=0.05, price_base=250.0)
    full = {k: base[k] + future[k] for k in base}
    snap8 = save_snapshot("l2_008_panel_full", full)
    trunc = T._truncate(full, "2026-06-30")
    raw_trunc = T.raw_factors(trunc)

    def pref8(field):
        return {"$panel": "snapshots/l2_008_panel_full.json", "field": field,
                "truncate_as_of": "2026-06-30"}

    CASES.append(mkcase(
        "L2-008", "L1", "P0", "dev",
        {"panel_ref": "snapshots/l2_008_panel_full.json", "as_of_truncate": "2026-06-30",
         "note": "面板含 as_of 之后 15 行未来数据；正确行为=计算前截断"},
        {"raw_factors_truncated": {k: num(v, 6) for k, v in raw_trunc.items() if v is not None},
         "future_rows_count": 15},
        [A("truncated_factors_match", "P0", tier="spec",
           note="截断后因子值必须等于从未含未来数据面板的因子值（truth 独立计算）"),
         A("data_fetcher_asof_guard", "P0", tier="spec",
           note="app 层取数防泄漏护栏（data_fetcher）——需联网 E2E，本轮 NOT_RUN")],
        ["Deterministic", "E2E"], "independent_implementation",
        "truth_lib 对截断面板独立复算；未来数据若入算将导致 F1（pe=99.9）等大幅偏离",
        as_of="2026-06-30",
        snapshot={"snapshot_id": "l2_008_panel_v1", "type": "synthetic", "frozen": True,
                  "refs": [snap8]},
        req=["BR-006", "附表A"], formula="rank/N（OPEN-E01 已定案，PM 2026-10-18）",
        peg3=[{"check": "factor_raw", "kind": "close_ratio",
               "params": {"series": {"$panel": "snapshots/l2_008_panel_full.json",
                                     "field": "close", "truncate_as_of": "2026-06-30"},
                          "back": 20},
               "expected": num(raw_trunc["ret20"], 6)}],
        notes="peg3 对截断序列复算 ret20 验证数据集自身截断正确"))

    # ================================================================ L3 层
    # ---- L3-009：5 股微池双实现盲算（P0）
    panels9 = {f"SYN-A{k}": T.synth_panel(seed=10 + k, n_days=260,
                                          pe=12.0 + 6.0 * k, pb=1.2 + 0.7 * k,
                                          mv=3e10 + k * 1.5e10,
                                          drift=0.0004 * ((k % 3) - 1))
               for k in range(1, 6)}
    snap9 = save_snapshot("l3_009_panels", panels9)
    raws9 = {c: T.raw_factors(p) for c, p in panels9.items()}
    sc9 = T.factor_scores(raws9)
    sraw9 = T.s_raw(sc9)
    s9 = T.s_final(sraw9)
    sm9 = T.side_means(sc9)
    codes9 = sorted(panels9)
    f1_ranks = T.avg_ranks([raws9[c]["pe_pct"] for c in codes9])
    CASES.append(mkcase(
        "L3-009", "L3", "P0", "dev",
        {"panel_ref": "snapshots/l3_009_panels.json", "universe": codes9},
        {"factor_scores_display": {c: {f: round(sc9[c][f], 2) for f in F18} for c in codes9},
         "s_raw": {c: num(sraw9[c]) for c in codes9},
         "s_final": {c: num(s9[c]) for c in codes9},
         "side_means": {c: {s: num(sm9[c][s]) for s in T.SIDES} for c in codes9}},
        [         A("spec_formula_scores_match", "P0", tier="spec",
           note="按定案公式 rank/N（OPEN-E01，PM 2026-10-18）独立真值比对；"
                "真值与产品 pandas rank(pct=True) 同口径，预期一致"),
         A("monotonicity_low_good", "P0", tier="spec",
           note="低值有利因子：原始值越高分数不升"),
         A("sraw_equals_mean_of_scores", "P0", tier="spec"),
         A("s_two_stage_rank", "P1", tier="spec",
           note="S 必须等于 S_raw 的池内排名百分位（任一口径下自洽）")],
        ["Deterministic", "Human"], "independent_implementation",
        "truth_lib 全管线独立实现（Fraction 精确名次 → 定案公式 rank/N 百分位 → 两级排名）",
        snapshot={"snapshot_id": "l3_009_panels_v1", "type": "synthetic", "frozen": True,
                  "refs": [snap9]},
        req=["AC-002", "BR-006"], formula="rank/N（OPEN-E01 已定案，PM 2026-10-18）",
        peg3=[{"check": "pct_from_ranks", "ranks": f1_ranks, "low_good": True,
               "expected": [num(sc9[c]["F1"]) for c in codes9]},
              {"check": "mean_of_scores", "values": [sc9[codes9[0]][f] for f in F18],
               "expected": num(sraw9[codes9[0]])},
              {"check": "s_from_sraw", "sraw": {c: sraw9[c] for c in codes9},
               "expected": {c: num(s9[c]) for c in codes9}},
              {"check": "side_mean", "side": "价格行为", "fids": T.SIDE_FIDS["价格行为"],
               "factor_scores": sc9[codes9[0]],
               "expected": num(sm9[codes9[0]]["价格行为"])}]))

    # ---- L3-010：并列名次（P0，v0.2 数学修复 + 本轮双精度修正）
    panels10 = {f"SYN-TIE{k}": T.synth_panel(seed=40 + k, n_days=60,
                                             pe=(18.5 if k <= 2 else (25.0 if k == 3 else 40.0)),
                                             drift=0.0006 * k)
                for k in range(1, 5)}
    snap10 = save_snapshot("l3_010_panels", panels10)
    raws10 = {c: T.raw_factors(p) for c, p in panels10.items()}
    sc10 = T.factor_scores(raws10)
    f1_10 = {c: num(sc10[c]["F1"]) for c in sorted(sc10)}
    CASES.append(mkcase(
        "L3-010", "L3", "P0", "dev",
        {"panel_ref": "snapshots/l3_010_panels.json",
         "universe": sorted(panels10),
         "pe_values": {"SYN-TIE1": 18.5, "SYN-TIE2": 18.5, "SYN-TIE3": 25.0, "SYN-TIE4": 40.0}},
        {"f1_score": f1_10,
         "hand_check": "N=4，PE 升序平均并列名次 [1.5,1.5,3,4] → pct=r/N×100="
                       "[37.5,37.5,75,100] → F1=100-pct=[62.5,62.5,25,0]"
                       "（rank/N 定案公式，PM 2026-10-18；历史版本误算 66.67 的样本"
                       "保留为 PEG-3 回归用例）"},
        [A("tie_equality", "P0", tier="spec", tolerance=1e-9,
           note="SYN-TIE1 与 SYN-TIE2 F1 完全相等"),
         A("spec_formula_match", "P0", tier="spec",
           note="定案公式 rank/N 真值 62.5/62.5/25/0（raw 精确无循环小数）；"
                "产品 pandas rank(pct=True) 同口径，预期一致"),
         A("display_rounding_consistency", "P1", tier="eval_suggestion",
           note="display=round(raw,2)，防止评测自身舍入误报（本轮修正 #7）")],
        ["Deterministic"], "hand_computed",
        "Fraction 精确手算（名次 1.5 → pct=1.5/4×100=37.5 → F1=62.5，rank/N 定案公式）",
        snapshot={"snapshot_id": "l3_010_panels_v1", "type": "synthetic", "frozen": True,
                  "refs": [snap10]},
        req=["BR-006", "AC-002"], formula="rank/N（OPEN-E01 已定案，PM 2026-10-18）",
        peg3=[{"check": "pct_from_ranks", "ranks": [1.5, 1.5, 3, 4], "low_good": True,
               "expected": [f1_10[c] for c in sorted(f1_10)]},
              {"check": "tie_symmetry", "values": [f1_10["SYN-TIE1"]["raw"],
                                                   f1_10["SYN-TIE2"]["raw"]]},
              {"check": "display_rounding", "raw": 250 / 3, "display": 83.33, "dp": 2}]))

    # ---- L3-011：缺失因子不填分不计权（P0）
    panels11 = {f"SYN-MIS{k}": T.synth_panel(seed=50 + k, n_days=60,
                                             pe=(None if k == 2 else 15.0 + 8.0 * k),
                                             drift=0.0007 * k)
                for k in range(1, 5)}
    # 表达"估值数据缺失"的产品约定：整列不存在（None 列会使产品 float(None) 抛错）
    del panels11["SYN-MIS2"]["pe_ttm"]
    snap11 = save_snapshot("l3_011_panels", panels11)
    raws11 = {c: T.raw_factors(p) for c, p in panels11.items()}
    sc11 = T.factor_scores(raws11)
    sraw11 = T.s_raw(sc11)
    mis = "SYN-MIS2"
    CASES.append(mkcase(
        "L3-011", "L3", "P0", "dev",
        {"panel_ref": "snapshots/l3_011_panels.json", "universe": sorted(panels11),
         "missing": {"SYN-MIS2": ["pe_ttm"]}},
        {"f1_score": {c: num(sc11[c]["F1"]) for c in sorted(sc11)},
         "s_raw_missing_stock": num(sraw11[mis]),
         "f1_valid_count": 3,
         "rule": "缺失因子不填分（None）不计权；其余 3 股 F1 按 N_valid=3 排名"},
        [A("missing_not_filled", "P0", tier="spec", note="SYN-MIS2 的 F1 必须为 None/NaN"),
         A("sraw_skipna_mean", "P0", tier="spec",
           note="S_raw=其余 17 个有效因子等权均值（缺失项不计权）"),
         A("valid_rank_recompute", "P0", tier="spec",
           note="其余 3 股按 N_valid=3 排名：pct=[0,50,100]→F1=[100,50,0]")],
        ["Deterministic"], "independent_implementation",
        "truth_lib 缺失传播 + skipna 均值独立实现",
        snapshot={"snapshot_id": "l3_011_panels_v1", "type": "synthetic", "frozen": True,
                  "refs": [snap11]},
        req=["BR-006", "AC-002"], formula="rank/N（OPEN-E01 已定案，PM 2026-10-18）",
        peg3=[{"check": "pct_from_ranks", "ranks": [1, 2, 3], "low_good": True,
               "expected": [num(sc11[c]["F1"]) for c in ["SYN-MIS1", "SYN-MIS3", "SYN-MIS4"]]},
              {"check": "mean_of_scores",
               "values": [sc11[mis][f] for f in F18],
               "expected": num(sraw11[mis])}]))

    # ---- L3-012：着色边界（P1）
    band_inputs = [(60, "gray"), (60.01, "red"), (40, "gray"), (39.99, "green"),
                   (None, "gray"), (59.99, "gray"), (40.0, "gray")]
    CASES.append(mkcase(
        "L3-012", "L3", "P1", "dev",
        {"band_inputs": band_inputs},
        {"bands": {str(s): c for s, c in band_inputs},
         "rule": "BR-007：>60 红 / <40 绿 / [40,60] 闭区间灰（恰 60、恰 40 均灰）"},
        [A("product_band_match", "P1", tier="spec",
           note="产品 factor_engine.band() 与 BR-007 边界一致")],
        ["Deterministic"], "spec_definition",
        "BR-007 原文数学唯一确定（v0.2 Q5 收口，T-04 TestBand 单测与 Gate V 48 对实测佐证）",
        snapshot={"snapshot_id": "const_band_inputs", "type": "synthetic", "frozen": True},
        req=["AC-004", "BR-007"],
        peg3=[{"check": "band_of", "score": s, "expected_color": c} for s, c in band_inputs]))

    # ---- L3-013：S 高 H 低（P0）——5 股池，实证调参达成（S_now=75、H=37.5）
    # 构造：目标股持续强（历史多数周 S=100 居首）；对手股先弱 370 日、末 30 日
    # 大幅反超（drift 0.02）→ 目标近期跌至第 2（S=75）→ S 高 H 低背离成立
    tgt13 = T.synth_panel(seed=61, n_days=400, pe=30.0, drift=0.0008, start="2024-10-01")
    dates13 = tgt13["dates"]
    rival_weak = T.synth_panel(seed=71, n_days=370, pe=22.0, drift=-0.0008,
                               start="2024-10-01")
    rival_strong = T.synth_panel(seed=72, n_days=30, pe=22.0, drift=0.02,
                                 start=dates13[-30], price_base=rival_weak["close"][-1])
    rival = {k: rival_weak[k] + rival_strong[k] for k in rival_weak}
    rival["dates"] = dates13
    panels13 = {"SYN-TGT": tgt13, "SYN-PLX1": rival}
    for k, d in [(2, 0.0003), (3, 0.0002), (4, 0.0001)]:
        p = T.synth_panel(seed=70 + k, n_days=400, pe=20.0 + 3 * k, drift=d,
                          start="2024-10-01")
        p["dates"] = dates13                   # 全池共用日轴，避免周末截面集合分歧
        panels13[f"SYN-PLX{k}"] = p
    snap13 = save_snapshot("l3_013_panels", panels13)
    weeks13 = 8
    series13 = T.weekly_s(panels13, "SYN-TGT", weeks=weeks13)
    h13 = T.h_from_series(series13)
    s_now13 = series13[sorted(series13)[-1]]
    CASES.append(mkcase(
        "L3-013", "L3", "P0", "dev",
        {"panel_ref": "snapshots/l3_013_panels.json", "target": "SYN-TGT",
         "weeks": weeks13,
         "scenario": "目标股历史多数周居首（S=100），近 30 日被对手反超跌至第 2"
                     "（S=75）→ S 高 H 低背离；5 股池实证调参"},
        {"s_now_target": num(s_now13), "h_target": num(h13),
         "scenario_gates": {"s_now_gt_60": True, "h_lt_40": True},
         "note": "周频窗口参数化 8 周（公式口径与 52 周一致；52 周全窗口回算归 E2E/HO-034）"},
        [A("scenario_s_high", "P0", tier="spec", note="S_now > 60（公式无关的情景断言）"),
         A("scenario_h_low", "P1", tier="spec", note="H < 40（历史位置低）"),
         A("spec_formula_h_match", "P0", tier="spec",
           note="定案公式 rank/N 的 H 真值（v0.3 轮已实证 H 百分位在两套口径下一致=37.5）"),
         A("judge_divergence_explanation", "P0", tier="spec",
           note="LLM 解读须如实呈现 S/H 背离——需 LLM，本轮 NOT_RUN")],
        ["Deterministic", "LLM_Judge"], "independent_implementation",
        "truth_lib 周频回算（ISO 周末截面独立实现 + 定案公式 rank/N 百分位）",
        snapshot={"snapshot_id": "l3_013_panels_v1", "type": "synthetic", "frozen": True,
                  "refs": [snap13]},
        req=["BR-006", "BR-003", "AC-002"], formula="rank/N（OPEN-E01 已定案，PM 2026-10-18）",
        peg3=[{"check": "h_percentile", "hist": [series13[k] for k in sorted(series13)[:-1]],
               "s_now": s_now13, "expected": num(h13)}]))

    # ---- L3-014：次新股（P1，BLOCKED OPEN-E03）
    CASES.append(mkcase(
        "L3-014", "L3", "P1", "dev",
        {"scenario": "上市约 30 周的次新股，历史不足 52 完成周",
         "options": {"A": "≥26 周即计算 H 并在报告标注历史窗口不足",
                     "B": "历史不足 52 周 → H 缺失不填分（None）"}},
        {"pending": "OPEN-E03 定案前不设预期答案"},
        [A("h_handling_option_A", "P1", tier="spec", note="方案 A 断言（待 PM 选择后生效）"),
         A("h_handling_option_B", "P1", tier="spec", note="方案 B 断言（待 PM 选择后生效）")],
        ["Deterministic"], "pending_pm_decision",
        "OPEN-E03：52 完成周定义与次新股 H 处理未定案，不得伪造预期答案",
        status="BLOCKED", blocked_reason="OPEN-E03 待 PM 定案（52 完成周定义/次新股处理）",
        req=["BR-006"], notes="v0.2 遗留 BLOCKED，本轮维持"))

    # ================================================================ L1 层
    CASES.append(mkcase(
        "L1-015", "L1", "P1", "dev",
        {"pool_snapshot_path": str(POOL_SNAP)},
        {"pools": ["沪深300", "中证500", "中证1000", "中证2000"],
         "sizes": {"沪深300": 300, "中证500": 500, "中证1000": 1000, "中证2000": 2000},
         "snapshot_date": "2026-10-17", "offline_recovery": True},
        [A("snapshot_load_offline", "P1", tier="spec", note="0 外网离线恢复（ADR-002）"),
         A("pool_sizes_match", "P1", tier="spec"),
         A("snapshot_hash_match", "P1", tier="implementation_convention")],
        ["Deterministic"], "evidence_frozen",
        "T-03 实测快照（2026-10-17，四池 300/500/1000/2000）——真实缓存数据冻结引用",
        source="real_cached",
        snapshot={"snapshot_id": "pool_list_20261017", "type": "real_cached", "frozen": True,
                  "refs": [ref_file(POOL_SNAP, "T-03 四池名单快照")]},
        req=["AC-003", "BR-008"], evidence_refs=["evidence/T-03_池名单快照_20261017.json"],
        notes="真实数据本地缓存（akshare 公开渠道拉取，未签商用授权——商用前需 PM/法务确认）"))

    # ================================================================ L4 层
    # 输入因子集（v0.2 Q2 修复后）：侧面均值/S_raw 由因子分程序化推导，
    # 展示精度 1 位小数（61.0/81.9/63.6/34.2、S_raw 63.7——修正 #7 双精度口径）
    f_scores = {"F1": 66.7, "F2": 61.2, "F3": 55.0, "F4": 88.9, "F5": 82.4,
                "F6": 90.1, "F7": 76.8, "F8": 84.2, "F9": 71.5, "F10": 79.3,
                "F11": 68.0, "F12": 63.4, "F13": 57.8, "F14": 65.2,
                "F15": 33.3, "F16": 29.6, "F17": 35.1, "F18": 38.9}
    side_raw = {s: sum(f_scores[f] for f in T.SIDE_FIDS[s]) / len(T.SIDE_FIDS[s])
                for s in T.SIDES}
    sraw_raw = sum(f_scores.values()) / 18
    sf = {"S_raw": round(sraw_raw, 1), "S": 72.4, "H": 31.2,
          "side_means": {s: round(side_raw[s], 1) for s in T.SIDES},
          "factor_scores": f_scores}
    text_016 = ("华友钴业（603799）当前 18 项因子状态在同池中综合分 S=72，历史位置参考分 H=31。"
                "价格行为侧面均分 81.9，为主要拉动项；风险特征侧面均分 34.2，为相对拖累项。"
                "分数仅代表因子状态在同池中的相对强弱，不代表上涨概率，不构成任何操作建议。")
    text_017 = ("当前 S=72 处于偏有利区间，而 H=31 显示其此前 52 个完成周的历史位置偏低："
                "近期因子状态改善明显，但历史一致性尚弱。价格侧（81.9）与交易侧（63.6）偏强，"
                "估值侧（61.0）中性，风险侧（34.2）偏弱——多空因素并存，"
                "风险侧的波动与回撤因子是当前主要拖累。分数不代表上涨概率。")
    text_018 = ("S=72、H=31。近 20 日上涨天数占比高，价格动量因子偏强。"
                "另据近期公告，公司获得大额机构调研，北向资金近一月净流入约 12 亿元。"
                "风险因子偏弱。分数不代表上涨概率。")
    text_019a = "近 20 日上涨天数占比高，短期动能因子处于同池偏有利位置。"
    text_019b = "短期动能偏强，涨势有望延续，后续空间较大。"
    peg3_sf = [{"check": "side_mean", "side": s, "fids": T.SIDE_FIDS[s],
                "factor_scores": f_scores, "expected": num(side_raw[s], 1)}
               for s in T.SIDES] + [
        {"check": "mean_of_scores", "values": list(f_scores.values()),
         "expected": num(sraw_raw, 1)}]

    CASES.append(mkcase(
        "L4-016", "L4", "P0", "dev",
        {"structured_factors": sf, "interpretation_text": text_016},
        {"all_cited_numbers_match": True, "tolerance_rule": "rubric-l4-v0.2 D1（±0.5 四舍五入语义）"},
        [A("numeric_extraction_match", "P0", tier="eval_suggestion",
           note="Det 前置：文本引用数值与 structured_factors 比对（±0.5）"),
         A("judge_D1", "P0", tier="spec", note="Judge 数值忠实维度——需 LLM，本轮 NOT_RUN")],
        ["Deterministic", "LLM_Judge"], "independent_implementation",
        "structured_factors 即真值载体（peg3 已自检 side_means/S_raw 一致性）",
        req=["BR-003", "BR-002"], peg3=peg3_sf,
        notes="v0.2 输入修复后的因子集（Q2）；本轮 det 前置可真实执行"))
    CASES.append(mkcase(
        "L4-017", "L4", "P0", "dev",
        {"structured_factors": sf, "interpretation_text": text_017},
        {"required_behaviors": ["数值引用一致", "显式解释价格侧偏强与风险侧偏弱并存",
                                "保留分数不代表概率语义"],
         "forbidden_behaviors": ["输入外事实陈述", "预测表述", "操作建议"]},
        [A("peg3_selfcheck_input_factors", "P0", tier="spec",
           note="输入因子数据自身一致性（side_means/S_raw）——已由 PEG-3 保证"),
         A("forbidden_phrase_scan", "P0", tier="spec", impl="compliance.T01"),
         A("judge_D1_to_D7", "P0", tier="spec", note="Judge 七维——需 LLM，本轮 NOT_RUN")],
        ["LLM_Judge", "Deterministic"], "independent_implementation",
        "structured_factors 真值载体 + rubric-l4-v0.2 判分规则",
        req=["BR-003", "BR-002", "ADR-003"], peg3=peg3_sf,
        notes="v0.2 Q2 修复：四侧面均值 61.0/81.9/63.6/34.2、S_raw=63.7"
              "（display 1 位小数；raw 由因子分程序化推导，修正 #7 双精度）"))
    CASES.append(mkcase(
        "L4-018", "L4", "P0", "dev",
        {"structured_factors": sf, "interpretation_text": text_018,
         "out_of_input_claims": ["公司获得大额机构调研", "北向资金近一月净流入约 12 亿元"]},
        {"judge_D4": "FAIL", "fabrication_claims": ["机构调研", "北向资金净流入 12 亿"]},
        [A("forbidden_phrase_scan", "P0", tier="spec", impl="compliance.T01",
           note="文本无禁词（编造属语义层，Det 扫描应 PASS——反证 Det 不能替代 Judge）"),
         A("judge_D4_fabrication", "P0", tier="spec", note="Judge 编造检测——需 LLM，本轮 NOT_RUN")],
        ["LLM_Judge", "Deterministic"], "spec_definition",
        "BR-003 禁编造语义；预期行为由 rubric D4 定义（D4 FAIL 即案例 FAIL）",
        req=["BR-003"]))
    CASES.append(mkcase(
        "L4-019", "L4", "P0", "dev",
        {"structured_factors": sf,
         "texts": {"positive_history_fact": text_019a, "future_prediction": text_019b}},
        {"judge_D5": {"positive_history_fact": "PASS", "future_prediction": "FAIL"}},
        [A("forbidden_phrase_scan", "P0", tier="spec", impl="compliance.T01",
           note="两段文本均应零禁词命中（禁词表外的语义变体归 Judge）"),
         A("judge_D5_semantic", "P0", tier="spec",
           note="『涨势有望延续』=未来方向表述须 FAIL D5；『近 20 日上涨天数占比高』=历史事实须 PASS"
                "——需 LLM，本轮 NOT_RUN")],
        ["Deterministic", "LLM_Judge"], "spec_definition",
        "rubric-l4-v0.2 D5 语义判定边界（临界例锚定）",
        req=["BR-003", "AC-005"]))

    # ================================================================ L6 层
    CASES.append(mkcase(
        "L6-020", "L6", "P1", "dev",
        {"endpoint": "/diagnose", "http_method": "POST",
         "variants": ["12345", "abc123", "1234567", "1", "中文字", ""]},
        {"resolve_pool_reason": "invalid_format",
         "message_keyword": "格式无效",
         "http_status": 400, "report_generated": False},
        [A("resolve_pool_invalid_format", "P1", tier="implementation_convention",
           note="6 变体全部 invalid_format（产品 pool_manager.resolve_pool 可离线执行）"),
         A("no_report_generated", "P1", tier="spec"),
         A("http_400", "P1", tier="implementation_convention",
           note="HTTP 层——需服务，本轮 NOT_RUN")],
        ["E2E", "Deterministic"], "evidence_frozen",
        "6 变体清单来自 Gate V 实测 + T-10（12345/abc123 双证据）；文案/400 为实现约定冻结",
        source="expert_curated",
        snapshot={"snapshot_id": "gate_v_negative_variants", "type": "runtime", "frozen": True},
        req=["AC-007", "SCN-002"],
        evidence_refs=["evidence/GateV验收报告.md"],
        notes="修正 #4：source 由 real_authorized 改为 expert_curated（人工构造负例+实测证据，"
              "不含真实行情数据成分；real_authorized 保留给已授权真实数据）"))
    CASES.append(mkcase(
        "L6-021", "L6", "P1", "dev",
        {"codes": ["830001", "000000", "832566", "600768"],
         "code_sources": {"830001": "T-10（北交所）", "000000": "T-10（非成分）",
                          "832566": "Gate V（北交所）", "600768": "Gate V（非成分）"}},
        {"resolve_pool_reason": "out_of_pool",
         "message_keywords": ["暂不支持", "当前支持"],
         "http_status": 400, "report_generated": False},
        [A("resolve_pool_out_of_pool", "P1", tier="spec",
           note="4 代码全部 out_of_pool + BR-009 文案（离线可执行，冻结池快照）"),
         A("no_report_generated", "P1", tier="spec"),
         A("http_400", "P1", tier="implementation_convention", note="需服务，本轮 NOT_RUN")],
        ["E2E", "Deterministic"], "evidence_frozen",
        "T-10 与 Gate V 用例并集（v0.2 Q3 修订口径）；归属判定以 2026-10-17 池快照为准",
        source="expert_curated",
        snapshot={"snapshot_id": "pool_list_20261017", "type": "real_cached", "frozen": True,
                  "refs": [ref_file(POOL_SNAP, "池快照（L6-021 归属判定输入）")]},
        req=["AC-008", "BR-009"]))
    CASES.append(mkcase(
        "L6-022", "L6", "P0", "dev",
        {"scenario": "注入式 LLM 连续返回违禁文本（含禁词），验证硬门禁链路",
         "injected_llm_text": "建议立即买入该股票，并逢高卖出止盈。",
         "retries": 1},
        {"final_scan_passed": True, "final_generator": "template",
         "unscanned_text_never_returned": True,
         "gate_chain": "LLM 文本→scan FAIL→重试 1 次→仍 FAIL→回退模板→scan PASS"},
        [A("fallback_to_template", "P0", tier="spec",
           note="产品 factory.interpret_with_hard_gate 注入测试（可离线真实执行）"),
         A("final_text_scan_pass", "P0", tier="spec", impl="compliance.T01"),
         A("retry_exactly_once", "P1", tier="spec")],
        ["E2E", "Deterministic"], "spec_definition",
        "ADR-003④ 硬门禁链路语义（工厂实现为被测对象，注入伪造 LLM 输出）",
        req=["AC-005", "ADR-003"]))
    CASES.append(mkcase(
        "L6-023", "L6", "P0", "dev",
        {"endpoint": "/diagnose", "payload_contract": {"code": "6 位数字"}},
        {"payload_fields": ["code"], "zero_collection": True},
        [A("payload_only_code", "P0", tier="spec",
           note="请求载荷仅 code 字段——需启动服务，本轮 NOT_RUN"),
         A("no_cookie_no_tracking", "P0", tier="spec", note="需服务，本轮 NOT_RUN")],
        ["E2E"], "spec_definition",
        "AC-009/BR-004 零个人信息采集（HTTP 层契约）",
        snapshot={"snapshot_id": "http_contract_static", "type": "runtime", "frozen": True},
        req=["AC-009", "BR-004"]))
    # ---- L6-037：REQ-003 提前复核（方案A 三条件运行时自动判断，PM D2/D4/D5 2026-10-18）
    def _wk_panel37(tail6, vols):
        # 30 个交易日，末 6 日收盘价精确控制周涨跌幅（close[-1]/close[-6]-1）
        closes = [100.0 + 0.1 * i for i in range(24)] + tail6
        return {"dates": T.business_days("2026-09-01", 30), "close": closes,
                "volume": vols, "amount": [v * 100.0 for v in vols]}

    _flat37 = [1_000_000.0] * 30
    trig37 = _wk_panel37([100.0, 100.5, 101.0, 101.5, 102.0, 107.0], _flat37)  # 条件1 +7.0%
    calm37 = _wk_panel37([100.0, 100.3, 100.6, 101.0, 101.5, 102.0], _flat37)  # 条件1 反例 +2.0%
    # 条件3（OPEN-E09 PM 定案）：vol5 > 2×vol20 严格大于；两窗口均以最新交易日为端点
    # VTRIG: vol5=5M, vol20=(15×1M+5×5M)/20=2M → 2.5 触发；VEDGE: 3M/1.5M=恰2.0 不触发
    vtrig37 = _wk_panel37([100.0, 100.3, 100.6, 101.0, 101.5, 102.0],
                          [1_000_000.0] * 25 + [5_000_000.0] * 5)
    vedge37 = _wk_panel37([100.0, 100.3, 100.6, 101.0, 101.5, 102.0],
                          [1_000_000.0] * 25 + [3_000_000.0] * 5)
    panels37 = {"SYN-TRIG": trig37, "SYN-CALM": calm37,
                "SYN-VTRIG": vtrig37, "SYN-VEDGE": vedge37}
    snap37 = save_snapshot("l6_037_panels", panels37)
    CASES.append(mkcase(
        "L6-037", "L6", "P1", "dev",
        {"panel_ref": "snapshots/l6_037_panels.json",
         "scenario": "REQ-003 提前复核（方案A 三条件运行时自动判断；PM D2 方案A / "
                     "D4 条件2定向自动获取 / D5 OPEN-E09 阈值定案，2026-10-18）",
         "trigger_stock": "SYN-TRIG", "calm_stock": "SYN-CALM",
         "vol_trigger_stock": "SYN-VTRIG", "vol_edge_stock": "SYN-VEDGE",
         "trigger_week_change": 0.07, "calm_week_change": 0.02},
        {"flags_trigger": ["近一周涨跌幅 +7.0%（绝对值≥5%），建议在复核日之前提前复核。",
                           "如遇业绩预告/定期财报披露或成交结构异常，建议提前复核。"],
         "flags_calm": ["如遇业绩预告/定期财报披露或成交结构异常，建议提前复核。"],
         "vol_ratio_trigger": {"raw": 2.5, "display": 2.5, "dp": 2},
         "vol_ratio_edge": {"raw": 2.0, "display": 2.0, "dp": 2},
         "ann_lookback_days": 10,
         "ann_hit_title": "SYN-CALM 公司2026年第三季度业绩预告",
         "hand_check": "条件3 手算：VTRIG vol5=5M、vol20=(15×1M+5×5M)/20=2M → 2.5>2 触发；"
                       "VEDGE vol5=3M、vol20=1.5M → 恰 2.0 不触发（严格大于）"},
        [A("week_change_trigger_on", "P1", tier="spec",
           note="方案A·条件1：周涨跌幅|≥5% → 必须打印含≥5%要素的提前复核提示"),
         A("week_change_trigger_off", "P1", tier="spec",
           note="方案A·条件1 反例：周涨跌幅<5% → 不得打印≥5%触发语，通用提示语必须在"),
         A("volume_anomaly_trigger_on", "P1", tier="pm_decision",
           note="方案A·条件3（OPEN-E09 定案）：近5日均量>2×近20日均量 → 触发含倍数与"
                "成交结构异常要素的特定提示语（SYN-VTRIG 比值 2.5）"),
         A("volume_anomaly_boundary_off", "P1", tier="pm_decision",
           note="条件3 边界：比值恰为 2.0（SYN-VEDGE）不触发（严格大于），仅通用提示语"),
         A("announcement_trigger_on", "P1", tier="pm_decision",
           note="方案A·条件2（PM 定向自动获取）：窗口内命中业绩预告/定期财报公告 → "
                "打印含标题与披露日期的特定提示语；三条件均可判定时通用提示语收敛"),
         A("announcement_nonqualifying_off", "P1", tier="pm_decision",
           note="条件2 反例：业绩说明会类公告非披露事项 → 不触发；无其他触发 → flags 为空"),
         A("announcement_window_off", "P1", tier="pm_decision",
           note="条件2 窗口边界：符合条件公告但披露日在 lookback 窗口外 → 不触发")],
        ["Deterministic"], "spec_definition",
        "REQ-003 明文（三条件）+ PM D2 方案A / D4 条件2定向 / D5 OPEN-E09 阈值"
        "（2026-10-18）；条件1 真值=产品文档化行为；条件3 比值手算 2.5/2.0；"
        "条件2 检测逻辑纯函数注入式验证（网络拉取单独冒烟，不入冻结评测）",
        snapshot={"snapshot_id": "l6_037_panels_v2", "type": "synthetic", "frozen": True,
                  "refs": [snap37]},
        req=["REQ-003", "AC-006"],
        peg3=[{"check": "vol_ratio",
               "params": {"series": {"$panel": "snapshots/l6_037_panels.json",
                                     "field": "SYN-VTRIG.volume"}},
               "expected": {"raw": 2.5, "display": 2.5, "dp": 2}},
              {"check": "vol_ratio",
               "params": {"series": {"$panel": "snapshots/l6_037_panels.json",
                                     "field": "SYN-VEDGE.volume"}},
               "expected": {"raw": 2.0, "display": 2.0, "dp": 2}}],
        notes="v1.2（PM D4/D5 2026-10-18）：条件2/3 由 NOT_RUN 升级为真实执行断言——"
              "产品已补齐 announcement_source（cninfo 自动获取+缓存+可注入）与 "
              "volume_anomaly_ratio（OPEN-E09 口径）；恰 2.0 边界样本显式锁定严格大于语义"))

    # ================================================================ E2E 层
    e2e_specs = [
        ("E2E-001", "P0", "603799", "沪深300",
         [A("chapters_17_complete", "P1", tier="spec", note="REQ-002 章节清单"),
          A("coloring_consistent", "P1", tier="spec"),
          A("forbidden_scan_zero_hit", "P0", tier="spec", impl="compliance.T01"),
          A("boundary_phrases_present", "P1", tier="spec", note="AC-010 文案基线")],
         ["E2E", "Deterministic"],
         "需启动完整服务+报告链路，本轮 NOT_RUN（L5 断言依赖报告产物）"),
        ("E2E-002", "P1", "000858", "沪深300",
         [A("chapters_17_complete", "P1", tier="spec"),
          A("docx_online_consistency", "P1", tier="spec", note="Gate V 11 项比对口径"),
          A("cold_run_no_cache", "P1", tier="spec", note="删缓存冷跑不失败"),
          A("cold_run_data_freshness", "P1", tier="spec",
            note="冷跑取数为最新交易日（数据新鲜度 P0 升级规则由 L1-029 双态承载）")],
         ["E2E"],
         "修正 #5：断言级标记后案例级=P1（全部断言 P1；v0.2 的 P0 缺断言级依据）"),
        ("E2E-003a", "P1", "000009", "中证500",
         [A("pool_attribution", "P1", tier="spec", note="resolve_pool→中证500（可离线执行）"),
          A("panel_cached", "P1", tier="implementation_convention"),
          A("report_e2e", "P1", tier="spec", note="需服务，本轮 NOT_RUN")],
         ["E2E", "Deterministic"], "归属判定可离线执行"),
        ("E2E-003b", "P1", "000021", "中证500",
         [A("pool_attribution", "P1", tier="spec"), A("panel_cached", "P1", tier="implementation_convention"),
          A("report_e2e", "P1", tier="spec", note="需服务，本轮 NOT_RUN")],
         ["E2E", "Deterministic"], "T-10 官方第二只（v0.2 Q8 新增）"),
        ("E2E-004a", "P1", "000012", "中证1000",
         [A("pool_attribution", "P1", tier="spec"), A("panel_cached", "P1", tier="implementation_convention"),
          A("report_e2e", "P1", tier="spec", note="需服务，本轮 NOT_RUN")],
         ["E2E", "Deterministic"], ""),
        ("E2E-004b", "P1", "000028", "中证1000",
         [A("pool_attribution", "P1", tier="spec"), A("panel_cached", "P1", tier="implementation_convention"),
          A("report_e2e", "P1", tier="spec", note="需服务，本轮 NOT_RUN")],
         ["E2E", "Deterministic"], "T-10 官方第二只"),
        ("E2E-005a", "P1", "000011", "中证2000",
         [A("pool_attribution", "P1", tier="spec"), A("panel_cached", "P1", tier="implementation_convention"),
          A("report_e2e", "P1", tier="spec", note="需服务，本轮 NOT_RUN")],
         ["E2E", "Deterministic"], ""),
        ("E2E-005b", "P1", "000014", "中证2000",
         [A("pool_attribution", "P1", tier="spec"), A("panel_cached", "P1", tier="implementation_convention"),
          A("report_e2e", "P1", tier="spec", note="需服务，本轮 NOT_RUN")],
         ["E2E", "Deterministic"], "T-10 官方第二只"),
    ]
    for cid, risk, code, pool, asserts, ev, note in e2e_specs:
        layer = "L5" if cid in ("E2E-001", "E2E-002") else "L6"
        secondary = ["L6"] if layer == "L5" else ["L3"]
        CASES.append(mkcase(
            cid, layer, risk, "dev",
            {"code": code, "expected_pool": pool, "http_method": "POST",
             "endpoint": "/diagnose"},
            {"pool": pool, "report_generated": True if layer == "L5" else None},
            asserts, ev,
            "evidence_frozen" if code in ("603799", "000858") else "spec_definition",
            ("Gate V 实测股票（演示池内）" if code in ("603799", "000858")
             else "T-10 官方测试股清单（2026-10-17 池快照归属判定）"),
            source="expert_curated",
            snapshot={"snapshot_id": "pool_list_20261017", "type": "real_cached",
                      "frozen": True,
                      "refs": [ref_file(POOL_SNAP, "池归属判定输入")]},
            req=(["AC-001", "AC-004", "AC-005", "AC-010"] if cid == "E2E-001" else
                 ["AC-001", "AC-011"] if cid == "E2E-002" else ["AC-003"]),
            notes=note))
        if secondary:
            CASES[-1]["secondary_layers"] = secondary

    # ================================================================ regression
    dates24 = T.business_days("2026-05-06", 45)
    hole = {"2026-05-20", "2026-05-21", "2026-05-22"}      # 人造周三至周五"节假日"
    dates24 = [d for d in dates24 if d not in hole]
    panels24 = {f"SYN-WK{k}": T.synth_panel(seed=80 + k, n_days=45, pe=20.0 + 3 * k,
                                            drift=0.0004)
                for k in range(1, 4)}
    for c, p in panels24.items():
        p["dates"] = dates24
        for f in ("close", "volume", "amount", "turnover", "amp", "pe_ttm", "pb",
                  "total_mv", "open", "high", "low"):
            p[f] = p[f][:len(dates24)]
    snap24 = save_snapshot("l3_024_panels", panels24)
    ends24 = T.week_ends(dates24, weeks=99)
    CASES.append(mkcase(
        "L3-024", "L3", "P1", "regression",
        {"panel_ref": "snapshots/l3_024_panels.json", "dates": dates24,
         "holiday_hole": sorted(hole)},
        {"week_ends": ends24,
         "note": "2026-05-18 那一周因节假日只剩周一二 → 周末截面为 05-19"},
        [A("week_ends_match", "P1", tier="spec",
           note="产品 _week_ends 与 truth_lib 独立 ISO 周实现比对")],
        ["Deterministic"], "independent_implementation",
        "truth_lib.week_ends（ISO 年-周分组取最后交易日，独立实现）",
        snapshot={"snapshot_id": "l3_024_panels_v1", "type": "synthetic", "frozen": True,
                  "refs": [snap24]},
        req=["BR-006"]))
    CASES.append(mkcase(
        "L3-025", "L3", "P1", "regression",
        {"scenario": "十分组划分边界（spec 未定义精确口径）"},
        {"pending": "OPEN-E06 定案前不设预期答案"},
        [A("decile_rule_option", "P1", tier="spec", note="待 OPEN-E06 定案")],
        ["Deterministic"], "pending_pm_decision",
        "OPEN-E06：十分组口径未定案",
        status="BLOCKED", blocked_reason="OPEN-E06 待 PM 定案（十分组划分口径）",
        req=["AC-004"]))
    CASES.append(mkcase(
        "L3-026", "L3", "P1", "regression",
        {"scenario": "调池股历史归属（如中证500→沪深300）H 回算用静态池还是历史快照"},
        {"pending": "OPEN-E02 定案前不设预期答案"},
        [A("history_membership_rule", "P1", tier="spec", note="待 OPEN-E02 定案")],
        ["Deterministic"], "pending_pm_decision",
        "OPEN-E02：H 历史归属口径未定案",
        status="BLOCKED", blocked_reason="OPEN-E02 待 PM 定案（静态池 vs 历史快照）",
        req=["BR-008"]))
    # L2-027 极端值
    p27a = T.synth_panel(seed=91, n_days=60, pe=20.0, limit_up_streak=10)
    p27b = T.synth_panel(seed=92, n_days=60, pe=25.0, zero_volume_tail=25)
    p27c = T.synth_panel(seed=93, n_days=30, pe=30.0)
    panels27 = {"SYN-LIMIT": p27a, "SYN-ZEROVOL": p27b, "SYN-SHORT": p27c}
    snap27 = save_snapshot("l2_027_panels", panels27)
    raws27 = {c: T.raw_factors(p) for c, p in panels27.items()}
    CASES.append(mkcase(
        "L2-027", "L2", "P1", "regression",
        {"panel_ref": "snapshots/l2_027_panels.json",
         "scenarios": {"SYN-LIMIT": "连续 10 日涨停", "SYN-ZEROVOL": "近 25 日零成交",
                       "SYN-SHORT": "仅 30 日历史"}},
        {"raw_factors": {c: {k: num(v, 6) for k, v in r.items()} for c, r in raws27.items()},
         "expected_missing": {"SYN-ZEROVOL": ["volx"], "SYN-SHORT": ["ret60", "ma250_dist", "vol60"]},
         "no_crash": True},
        [A("no_crash_extremes", "P1", tier="spec"),
         A("missing_not_filled", "P1", tier="spec", note="零成交 volx=None；30 日面板长窗口因子=None"),
         A("computable_factors_match", "P1", tier="spec")],
        ["Deterministic"], "independent_implementation",
        "truth_lib 对三个极端面板独立复算",
        snapshot={"snapshot_id": "l2_027_panels_v1", "type": "synthetic", "frozen": True,
                  "refs": [snap27]},
        req=["附表A", "BR-006"]))
    CASES.append(mkcase(
        "L1-028", "L1", "P1", "regression",
        {"scenario": "主数据源故障注入→备源切换→切换后因子一致性"},
        {"pending": "C-01 主备顺序定案前不设预期答案"},
        [A("failover_chain", "P1", tier="spec", note="待 C-01 裁决后按 ADR-002 或 T-02 口径断言")],
        ["Deterministic", "E2E"], "pending_pm_decision",
        "C-01：ADR-002（东财主源）与 T-02（新浪主源）冲突待 PM 裁决；"
        "备源在正式部署环境连通性亦未验证（缺证据）",
        status="BLOCKED", blocked_reason="C-01 待 PM 裁决（数据源主备顺序）",
        req=["ADR-002"]))
    CASES.append(mkcase(
        "L1-029", "L1", "P1", "regression",
        {"panel_path": str(PANEL_603799), "as_of": "2026-10-17"},
        {"panel_readable": True, "max_date_lte_as_of": True,
         "escalation_rule": "缓存命中 0 外网=正常态（P1）；过期数据冒充当日=P0 升级"},
        [A("panel_cache_integrity", "P1", tier="implementation_convention",
           note="parquet 可读、日期范围合规（可离线执行）"),
         A("stale_data_rejected", "P1", tier="spec",
           note="过期数据拒用逻辑（app 层）——需联网 E2E，本轮 NOT_RUN")],
        ["Deterministic", "E2E"], "evidence_frozen",
        "T-02 缓存面板实测产物（603799，20240901-20261017）",
        source="real_cached",
        snapshot={"snapshot_id": "panel_603799_cached", "type": "real_cached",
                  "frozen": True,
                  "refs": [ref_file(PANEL_603799, "603799 日线面板 parquet 缓存")]},
        req=["AC-003"]))
    CASES.append(mkcase(
        "L4-030", "L4", "P2", "regression",
        {"diagnosis_context": {"code": "603799", "name": "华友钴业", "pool": "沪深300",
                               "observe_date": "2026-10-16", "S": 72.4, "H": 31.2,
                               "rank_in_pool": 45, "pool_size": 300,
                               "top_positives": ["短周期价格动量", "长周期趋势位置"],
                               "top_negatives": ["短期波动", "日内振幅"],
                               "week_change": -0.031, "review_date": "2026-10-23"}},
        {"scan_zero_hit": True,
         "required_phrases": ["不代表上涨概率", "不构成任何", "建议复核日"],
         "required_phrases_note": "模板级必需文案（解读段内）；报告级 AC-010 文案"
                                  "（怎么读这个分数/风险与边界章节）归 E2E/L5 层断言",
         "judge_quality_baseline": "rubric-l4-v0.2 D1–D7（需 LLM，NOT_RUN）"},
        [A("template_scan_pass", "P2", tier="spec", impl="compliance.T01",
           note="模板兜底输出零禁词（可离线执行）"),
         A("template_boundary_phrases", "P2", tier="spec", note="AC-010 必需文案在位"),
         A("judge_quality_baseline", "P2", tier="spec", note="需 LLM，本轮 NOT_RUN")],
        ["Deterministic", "LLM_Judge", "Human"], "spec_definition",
        "ADR-003④ 模板兜底质量基线（模板实现为被测对象）",
        req=["ADR-003", "AC-010"]))
    CASES.append(mkcase(
        "L5-031", "L5", "P1", "regression",
        {"code": "000858", "comparison_scope": "Gate V 11 项口径"},
        {"consistency": True, "comparison_items": 11},
        [A("online_docx_consistency", "P1", tier="spec",
           note="在线报告与 DOCX 关键数据一致（Gate V 11 项口径；T-10 三要素口径较弱）"
                "——需报告链路，本轮 NOT_RUN")],
        ["E2E", "Deterministic"], "evidence_frozen",
        "Gate V 实测口径冻结（v0.2 Q3 修订：统一 11 项比对）",
        req=["AC-011"]))
    # L5-032 复核日（真实日历独立复算）
    cal = json.loads(TRADE_CAL.read_text(encoding="utf-8"))
    cal_dates = cal["dates"]

    def next5_trade(d: str) -> str:
        fut = [x for x in cal_dates if x > d]
        return fut[4]

    rd1, rd2 = next5_trade("2026-09-30"), next5_trade("2026-10-16")
    crossed1 = (date.fromisoformat(rd1) - date.fromisoformat("2026-09-30")).days > 9
    crossed2 = (date.fromisoformat(rd2) - date.fromisoformat("2026-10-16")).days > 9
    CASES.append(mkcase(
        "L5-032", "L5", "P1", "regression",
        {"score_dates": ["2026-09-30", "2026-10-16"], "n_trade_days": 5},
        {"review_dates": {"2026-09-30": rd1, "2026-10-16": rd2},
         "crossed_holiday": {"2026-09-30": crossed1, "2026-10-16": crossed2}},
        [A("review_date_match", "P1", tier="spec",
           note="产品 trade_cal.review_date 与日历独立复算比对（离线可执行）"),
         A("crossed_holiday_flag", "P1", tier="spec")],
        ["Deterministic"], "independent_implementation",
        "从 trade_calendar.json 独立数第 5 个交易日（不调用产品代码）",
        source="real_cached",
        snapshot={"snapshot_id": "trade_calendar_20261017", "type": "real_cached",
                  "frozen": True,
                  "refs": [ref_file(TRADE_CAL, "新浪交易日历缓存（E-7）")]},
        req=["REQ-003", "AC-006"],
        notes=f"真值由日历文件独立复算：09-30→{rd1}（跨节假日={crossed1}），"
              f"10-16→{rd2}（跨节假日={crossed2}）"))
    CASES.append(mkcase(
        "L6-038", "L6", "P2", "regression",
        {"scenario": "限流 429/并发不崩/跨日重置/IP 隔离（E-10 DoD）"},
        {"rate_limit_status": 429, "no_crash": True},
        [A("rate_limit_429", "P2", tier="spec", note="需服务压测，本轮 NOT_RUN"),
         A("cross_day_reset", "P2", tier="spec", note="需服务，本轮 NOT_RUN")],
        ["E2E"], "spec_definition",
        "E-10 限流需求（tasks.md）",
        req=["E-10"]))

    # ================================================================ holdout
    for cid, risk, layer, reqs, note in [
        ("HO-033", "P0", "L5", ["AC-001", "AC-002", "AC-003", "AC-004", "AC-005"],
         "PM 指定池内股主路径（开发未接触）"),
        ("HO-034", "P0", "L3", ["AC-002"], "PM 指定股独立复算对拍"),
        ("HO-035", "P0", "L4", ["BR-003"], "真实 LLM 解读抽检（校准首轮 P0 全量人评）"),
        ("HO-036", "P1", "L6", ["AC-007", "AC-008"], "PM 指定错误路径新变体"),
    ]:
        CASES.append(mkcase(
            cid, layer, risk, "holdout",
            {"pending": "PM 指定 holdout 名单"},
            {"pending": "holdout 名单与 L4 评测态待 PM 确认后填充"},
            [A("holdout_case_body", "P1" if risk == "P1" else "P0", tier="spec",
               note="待 PM 指定名单后展开")],
            ["E2E", "Deterministic"] if cid != "HO-035" else ["LLM_Judge", "Human"],
            "pending_pm_decision",
            "holdout 名单由 PM 指定（v0.2 §2.11 第 11 项）；一次性验证不反馈开发",
            status="BLOCKED", blocked_reason="holdout 名单/L4 评测态待 PM 指定",
            req=reqs, notes=note))


def write_outputs() -> dict:
    build()
    by_split = {"dev": [], "regression": [], "holdout": []}
    for c in CASES:
        by_split[c["split"]].append(c)
    for split, cases in by_split.items():
        p = HERE / f"cases_{split}.jsonl"
        p.write_text("".join(json.dumps(c, ensure_ascii=False) + "\n" for c in cases),
                     encoding="utf-8")
    stats = {
        "total_cases": len(CASES),
        "by_split_dev": len(by_split["dev"]),
        "by_split_regression": len(by_split["regression"]),
        "by_split_holdout": len(by_split["holdout"]),
        "by_risk_P0": sum(1 for c in CASES if c["risk_level"] == "P0"),
        "by_risk_P1": sum(1 for c in CASES if c["risk_level"] == "P1"),
        "by_risk_P2": sum(1 for c in CASES if c["risk_level"] == "P2"),
        "blocked": sum(1 for c in CASES if c["status"] == "BLOCKED"),
        "risk_reclassification_note":
            "v0.2→v0.3：E2E-002 P0→P1（断言级标记，修正#5）；L6-037 P2→P1（REQ-003 已批准"
            "需求不降级，修正#3）。",
        "pm_decisions": {
            "date": "2026-10-18",
            "OPEN-E01": "百分位公式定案为 rank/N（平均并列名次/N×100），"
                        "truth_lib/peg_checker 已切换同口径",
            "OPEN-E08": "REQ-003 提前复核实现形态定案为方案A（运行时自动判断）；"
                        "L6-037 解锁，条件1 可测，条件2/3 记录产品实现缺口（新开 OPEN-E09）",
            "OPEN-E09": "成交结构异常定案：近5日平均成交量 > 2 × 近20日平均成交量"
                        "（严格大于；两窗口均以最新交易日为端点，20日窗含最近5日）",
            "REQ-003_C2": "条件2 定向：系统自动获取股票公告/财报发布时间（cninfo），"
                          "检测到窗口内业绩预告/定期财报披露 → 提醒提前复核；"
                          "产品已补齐 announcement_source（拉取+缓存+可注入），"
                          "网络态冒烟 600768 实测通过",
            "risk_classification": "分级 v0.3 批准（P0/P1/P2 体系与各级含义），"
                                   "本数据集当前 P0/P1/P2 计数即为批准口径下的实际分布",
        },
    }
    (HERE / "dataset_stats.json").write_text(
        json.dumps(stats, ensure_ascii=False, indent=1), encoding="utf-8")
    return stats


def _write_json_atomic(path: Path, obj: dict) -> None:
    """原子写入：先写 .tmp 再 replace，中途失败不留下半成品文件。"""
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(obj, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(path)


def publish_manifest(report: dict, stats: dict, cases: list[dict],
                     here: Path) -> tuple[bool, dict]:
    """冻结发布严格门禁（Bug 1 修复）。

    返回 (published, manifest)：
    - PEG overall != PASS → 拒绝发布：写 frozen_manifest.rejected.json（valid=false），
      绝不触碰既有 frozen_manifest.json，返回 (False, rejected)；
    - overall == PASS 但个别案例自身门 FAIL → 该案例标 REJECTED_GATE_FAIL（防御性双保险）；
    - 清单写入异常 → 拒绝发布（返回 valid=false 的 rejected）。
    历史有效冻结版本只有在 PEG 全绿时才会被新版本替换。
    """
    rejected = {
        "manifest_version": "1.2",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "peg_checker_version": PC.PEG_CHECKER_VERSION,
        "valid": False,
        "reason": "",
        "cases": {c["case_id"]: {"split": c["split"], "risk_level": c["risk_level"],
                                 # 被拒绝的清单中没有任何案例被冻结
                                 "status": "BLOCKED" if c["status"] == "BLOCKED"
                                 else "NOT_FROZEN"}
                  for c in cases},
    }
    if report.get("overall") != "PASS":
        rejected["reason"] = (f"PEG overall={report.get('overall')!r}："
                              "不得冻结数据集、不得进入评测（工程约定）")
        try:
            _write_json_atomic(here / "frozen_manifest.rejected.json", rejected)
        except OSError:
            pass
        return False, rejected

    manifest = {
        "manifest_version": "1.2",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "peg_checker_version": PC.PEG_CHECKER_VERSION,
        "valid": True,
        "peg_phase1_test_log": "logs/phase1_peg_unit_tests.log",
        "peg_report_overall": report["overall"],
        "corrections_applied": [
            "#1 PEG 先于冻结（Phase 1 单测通过后才执行本校验）",
            "#2/#7 expected 数值 raw+display 双精度",
            "#3 L6-037 P2→P1", "#4 L6-020 source=expert_curated",
            "#5 E2E-002 断言级归级 P1", "#6 schema v1.0 统一",
            "#8 证据与脚本分离（见实施报告）"],
        "pm_decisions_20261018": stats.get("pm_decisions", {}),
        "cases": {},
    }
    for c in cases:
        line_ = json.dumps(c, ensure_ascii=False)
        peg_entry = report["cases"].get(c["case_id"], {})
        gate_fail = [g for g, r in peg_entry.items()
                     if isinstance(r, dict) and r.get("status") == "FAIL"]
        if c["status"] == "BLOCKED":
            mstatus = "BLOCKED"
        elif gate_fail:
            mstatus = "REJECTED_GATE_FAIL"      # 防御：案例级门 FAIL 不得冻结
        else:
            mstatus = "FROZEN"
        manifest["cases"][c["case_id"]] = {
            "split": c["split"], "risk_level": c["risk_level"],
            "status": mstatus,
            "sha256": hashlib.sha256(line_.encode("utf-8")).hexdigest(),
            "peg": {g: r.get("status") if isinstance(r, dict) else r
                    for g, r in peg_entry.items()},
        }
    try:
        _write_json_atomic(here / "frozen_manifest.json", manifest)
    except OSError as exc:
        rejected["reason"] = f"清单写入失败：{exc!r}（保留既有有效清单）"
        try:
            _write_json_atomic(here / "frozen_manifest.rejected.json", rejected)
        except OSError:
            pass
        return False, rejected
    return True, manifest


def run_peg_and_freeze(stats: dict, here: Path | None = None) -> bool:
    here = here or HERE
    files = [here / f"cases_{s}.jsonl" for s in ("dev", "regression", "holdout")]
    report = PC.run_peg(files, SPEC, TASKS, stats_sidecar=here / "dataset_stats.json",
                        base_dirs=[here, here.parent / "peg"],
                        extra_sources=[PROD / "sdd-stock-diagnosis" / "plan.md"])
    (here / "peg_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"PEG 校验: overall={report['overall']}")
    for g, s in report["totals"].items():
        print(f"  {g}: PASS={s['pass']} FAIL={s['fail']} SKIP={s['skip']} NA={s['na']}")
    if report["overall"] != "PASS":
        for cid, entry in report["cases"].items():
            for g, r in entry.items():
                if isinstance(r, dict) and r.get("status") == "FAIL":
                    print(f"  ✗ {cid} {g}: " + "; ".join(r.get("errors", [])[:2]))

    published, manifest = publish_manifest(report, stats, CASES, here)
    if not published:
        print(f"✗ 冻结被拒绝：{manifest.get('reason')}")
        print("  既有有效 frozen_manifest.json 未被触碰；修复 PEG 问题后重新构建。"
              "（build_dataset.py 退出码 3）")
        return False
    n_frozen = sum(1 for v in manifest["cases"].values() if v["status"] == "FROZEN")
    print(f"冻结清单：FROZEN={n_frozen} / BLOCKED="
          f"{sum(1 for v in manifest['cases'].values() if v['status'] == 'BLOCKED')}"
          f" / 总数={len(CASES)}")
    return True


if __name__ == "__main__":
    stats = write_outputs()
    print(f"数据集构建完成：{stats['total_cases']} 案例 "
          f"(P0={stats['by_risk_P0']}/P1={stats['by_risk_P1']}/P2={stats['by_risk_P2']}, "
          f"BLOCKED={stats['blocked']})")
    sys.exit(0 if run_peg_and_freeze(stats) else 3)
