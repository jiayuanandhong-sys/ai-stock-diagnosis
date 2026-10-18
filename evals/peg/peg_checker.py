# -*- coding: utf-8 -*-
"""PEG 检查器 v1.0.0 —— 评测集质量门（Evals 自身验收机制）。

PEG-1 结构校验（按 eval_case.schema.json 契约手工实现的等价校验器，仅标准库）
PEG-2 字段完整性与风险一致性（案例级 risk_level == 断言级最大值）
PEG-3 数学与逻辑一致性（从 input/snapshot 自动重算 expected；raw/display 双精度）
PEG-4 标准答案独立性（truth_origin 白名单 + 禁止以被测系统输出为真值）
PEG-5 需求追溯与覆盖（requirement_id 回查 spec/tasks；AC 覆盖；T-10 四池≥2；统计边车核对）
PEG-6 冻结数据与引用存在性（文件 + sha256）

执行顺序纪律（修正 #1）：本检查器必须先通过单元测试（含已知错误样本回归），
才允许对 Golden Dataset 执行首次强制校验并冻结。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import statistics
import sys
from datetime import datetime, timezone
from fractions import Fraction
from pathlib import Path

PEG_CHECKER_VERSION = "1.1.0"
# v1.1.0（2026-10-18 PM D4/D5 裁决落实）：新增 vol_ratio 检查类（OPEN-E09：近5日均量/
# 近20日均量，两窗口以最新交易日为端点）；_resolve_param 支持多股票快照点路径（STOCK.field）。

LAYERS = {"L1", "L2", "L3", "L4", "L5", "L6"}
SPLITS = {"dev", "regression", "holdout"}
RISKS = {"P0", "P1", "P2"}
RISK_ORDER = {"P0": 0, "P1": 1, "P2": 2}
SOURCES = {"synthetic", "expert_curated", "real_cached", "real_authorized"}
SNAP_TYPES = {"synthetic", "real_cached", "real_authorized", "runtime"}
TRUTH_METHODS = {"hand_computed", "independent_implementation", "spec_definition",
                 "implementation_convention_frozen", "evidence_frozen", "pending_pm_decision"}
EVALUATORS = {"Deterministic", "E2E", "LLM_Judge", "Human"}
SOURCE_TIERS = {"spec", "implementation_convention", "eval_suggestion", "pm_decision"}
CASE_ID_RE = re.compile(r"^(E2E|L[1-6]|HO)-[0-9]+[a-z]?$")
REQ_ID_RE = re.compile(r"^(REQ|BR|AC|ADR|SCN|NG|E)-[0-9]+(-[0-9]+)?$|^附表A$")
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

REQUIRED_13 = ["case_id", "requirement_id", "layer", "risk_level", "split", "input",
               "as_of_date", "data_snapshot", "expected_output", "assertions",
               "evaluator_type", "pass_criteria", "failure_reason"]

TRUTH_FORBIDDEN_MARKERS = ["被测系统输出为真值", "产品输出为真值", "以系统输出为真值",
                           "复制系统输出", "以被测实现结果为准"]
TRUTH_MATH_METHODS = {"hand_computed", "independent_implementation", "spec_definition"}


# ---------------------------------------------------------------- PEG-1 结构
def peg1_structure(case: dict) -> list[str]:
    """返回 FAIL 描述列表（空 = PASS）。手工实现 eval_case.schema.json 的等价校验。"""
    errs: list[str] = []
    for f in REQUIRED_13 + ["status"]:
        if f not in case:
            errs.append(f"PEG-1: 缺必填字段 {f}")
    if errs:
        return errs  # 后续字段校验无意义
    if not isinstance(case["case_id"], str) or not CASE_ID_RE.match(case["case_id"]):
        errs.append(f"PEG-1: case_id 格式非法: {case['case_id']!r}")
    if not isinstance(case["requirement_id"], list) or not case["requirement_id"]:
        errs.append("PEG-1: requirement_id 须为非空数组")
    else:
        for rid in case["requirement_id"]:
            if not isinstance(rid, str) or not REQ_ID_RE.match(rid):
                errs.append(f"PEG-1: requirement_id 条目非法: {rid!r}")
    if case["layer"] not in LAYERS:
        errs.append(f"PEG-1: layer 非法: {case['layer']!r}")
    sl = case.get("secondary_layers", [])
    if not isinstance(sl, list) or any(x not in LAYERS for x in sl):
        errs.append("PEG-1: secondary_layers 非法")
    if case["risk_level"] not in RISKS:
        errs.append(f"PEG-1: risk_level 非法: {case['risk_level']!r}")
    if case["split"] not in SPLITS:
        errs.append(f"PEG-1: split 非法: {case['split']!r}（枚举 dev/regression/holdout）")
    if not isinstance(case["input"], dict):
        errs.append("PEG-1: input 须为对象")
    as_of = case["as_of_date"]
    if as_of is not None and (not isinstance(as_of, str) or not DATE_RE.match(as_of)):
        errs.append(f"PEG-1: as_of_date 非法: {as_of!r}")
    snap = case.get("data_snapshot")
    if not isinstance(snap, dict):
        errs.append("PEG-1: data_snapshot 须为对象")
    else:
        if not snap.get("snapshot_id"):
            errs.append("PEG-1: data_snapshot.snapshot_id 缺失")
        if snap.get("type") not in SNAP_TYPES:
            errs.append(f"PEG-1: data_snapshot.type 非法: {snap.get('type')!r}")
        if not isinstance(snap.get("frozen"), bool):
            errs.append("PEG-1: data_snapshot.frozen 须为布尔")
        refs = snap.get("refs", [])
        if not isinstance(refs, list):
            errs.append("PEG-1: data_snapshot.refs 须为数组")
        else:
            for r in refs:
                if not isinstance(r, dict) or "path" not in r:
                    errs.append("PEG-1: refs 条目缺 path")
    if not isinstance(case.get("expected_output"), dict):
        errs.append("PEG-1: expected_output 须为对象")
    asserts = case.get("assertions")
    if not isinstance(asserts, list) or not asserts:
        errs.append("PEG-1: assertions 须为非空数组")
    else:
        for i, a in enumerate(asserts):
            if not isinstance(a, dict):
                errs.append(f"PEG-1: assertions[{i}] 须为对象"); continue
            if not a.get("type"):
                errs.append(f"PEG-1: assertions[{i}] 缺 type")
            if a.get("risk_level") not in RISKS:
                errs.append(f"PEG-1: assertions[{i}].risk_level 缺失或非法（断言级风险标记为必填）")
            if a.get("source_tier") is not None and a["source_tier"] not in SOURCE_TIERS:
                errs.append(f"PEG-1: assertions[{i}].source_tier 非法: {a.get('source_tier')!r}")
    ev = case.get("evaluator_type")
    if not isinstance(ev, list) or not ev or any(x not in EVALUATORS for x in ev):
        errs.append(f"PEG-1: evaluator_type 须为非空数组且取值于 {sorted(EVALUATORS)}: {ev!r}")
    if not isinstance(case.get("pass_criteria"), str) or not case["pass_criteria"].strip():
        errs.append("PEG-1: pass_criteria 须为非空字符串")
    if not isinstance(case.get("failure_reason"), str):
        errs.append("PEG-1: failure_reason 须为字符串（可为空）")
    if case["source"] not in SOURCES:
        errs.append(f"PEG-1: source 非法: {case['source']!r}（四级标签见 schema）")
    to = case.get("truth_origin")
    if not isinstance(to, dict) or to.get("method") not in TRUTH_METHODS \
            or not isinstance(to.get("detail"), str) or not to["detail"].strip():
        errs.append(f"PEG-1: truth_origin 结构非法（须含 method 枚举 + 非空 detail）: {to!r}")
    st = case.get("status")
    if st not in {"DRAFT", "FROZEN", "BLOCKED"}:
        errs.append(f"PEG-1: status 非法: {st!r}")
    elif st == "BLOCKED" and not str(case.get("blocked_reason", "")).strip():
        errs.append("PEG-1: status=BLOCKED 时 blocked_reason 必填")
    return errs


# ---------------------------------------------------------------- PEG-2 完整性
def peg2_fields(case: dict) -> list[str]:
    errs: list[str] = []
    warns: list[str] = []
    asserts = case.get("assertions") or []
    arisks = [a.get("risk_level") for a in asserts if isinstance(a, dict)
              and a.get("risk_level") in RISKS]
    if arisks and case.get("risk_level") in RISKS:
        expected_case_level = min(arisks, key=lambda r: RISK_ORDER[r])  # P0 最大
        if case["risk_level"] != expected_case_level:
            errs.append(f"PEG-2: 案例级 risk_level={case['risk_level']} 与断言级最大值 "
                        f"{expected_case_level} 不一致（混合严重度案例必须断言级标记、案例级取最大）")
    missing_tier = sum(1 for a in asserts if isinstance(a, dict) and not a.get("source_tier"))
    if missing_tier:
        warns.append(f"PEG-2[WARN]: {missing_tier} 条断言缺 source_tier 来源层级标注")
    if warns:
        case.setdefault("_warnings", []).extend(warns)
    return errs


# ---------------------------------------------------------------- PEG-3 数学
def _num_pair(expected: dict) -> tuple[float, float, int]:
    return (float(expected["raw"]), float(expected["display"]), int(expected.get("dp", 2)))


def _check_value(check_name: str, computed: float, expected: dict,
                 raw_tol: float = 1e-6) -> list[str]:
    raw, display, dp = _num_pair(expected)
    errs = []
    if abs(computed - raw) > raw_tol:
        errs.append(f"PEG-3[{check_name}]: 复算值 {computed!r} 与 expected.raw {raw!r} 偏差超容差 {raw_tol}")
    if abs(round(computed, dp) - display) > 1e-9:
        errs.append(f"PEG-3[{check_name}]: display {display!r} != round(raw, {dp})={round(computed, dp)}"
                    "（标准答案必须同时保留原始精度与展示精度且自洽）")
    return errs


def _pct_from_ranks(ranks: list[float], low_good: bool) -> list[float]:
    """定案公式（OPEN-E01，PM 2026-10-18 裁决）：pct = 平均并列名次/N*100；low_good → 100-pct。

    Fraction 精确实现：名次以 0.5 步长表示（平均并列），分子分母同乘 2 取整。
    """
    n = len(ranks)
    pcts = [float(Fraction(int(round(r * 2)), n * 2) * 100) for r in ranks]
    return pcts


def _pearson(xs: list[float], ys: list[float]) -> float:
    n = len(xs)
    mx, my = sum(xs) / n, sum(ys) / n
    cov = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    vx = sum((x - mx) ** 2 for x in xs)
    vy = sum((y - my) ** 2 for y in ys)
    return cov / (vx ** 0.5 * vy ** 0.5)


def _factor_raw(kind: str, p: dict) -> float:
    """18 因子原始值的独立复算（纯 Python 实现，与产品 pandas 代码路径独立）。"""
    s = p.get("series")
    if kind == "last_value":
        return float(s[-1])
    if kind == "close_ratio":                      # ret20/ret60: c[-1]/c[-back]-1
        return s[-1] / s[-(p["back"] + 1)] - 1
    if kind == "accel":                            # (c[-1]/c[-6]-1) - ret20
        return (s[-1] / s[-(p["short"] + 1)] - 1) - (s[-1] / s[-(p["back"] + 1)] - 1)
    if kind == "last_over_mean":                   # F6=收盘价/MA250（附表A 比值口径）
        w = s[-p["window"]:]
        return s[-1] / (sum(w) / len(w))
    if kind == "ma_ratio":                         # mean(c[-20:])/mean(c[-60:])
        a = sum(s[-p["w1"]:]) / p["w1"]
        b = sum(s[-p["w2"]:]) / p["w2"]
        return a / b
    if kind == "pos52w":                           # (c[-1]-min)/(max-min)
        w = s[-p["window"]:]
        return (s[-1] - min(w)) / (max(w) - min(w) + p.get("eps", 1e-12))
    if kind == "updays":                           # 近 window 日上涨天数占比
        w = s[-(p["window"] + 1):]
        ups = sum(1 for i in range(1, len(w)) if w[i] > w[i - 1])
        return ups / p["window"]
    if kind == "mean_tail":                        # amt20/turn20/amp20
        w = s[-p["window"]:]
        return sum(w) / len(w)
    if kind == "volx":                             # mean(v[-5:])/mean(v[-20:])
        a = sum(s[-p["w1"]:]) / p["w1"]
        b = sum(s[-p["w2"]:]) / p["w2"]
        return a / (b + p.get("eps", 1e-12))
    if kind == "vp_corr":                          # 量价相关（口径见 build_dataset 注释）
        c, v = p["close"], p["volume"]
        c_ret = [c[i] / c[i - 1] - 1 for i in range(len(c) - 19, len(c))]      # d2..d20
        v_ret = [v[i] / v[i - 1] - 1 for i in range(len(c) - 19, len(c))]
        return _pearson(c_ret, v_ret)
    if kind == "ret_std":                          # vol20/vol60: pct_change 尾部样本标准差 ddof=1
        rets_all = [s[i] / s[i - 1] - 1 for i in range(1, len(s))]
        w = rets_all[-p["window"]:]
        return statistics.stdev(w)
    if kind == "mdd60":                            # 1 - min(c[-w:]/cummax)
        w = s[-p["window"]:]
        peak, mdd = w[0], 0.0
        for x in w:
            peak = max(peak, x)
            mdd = min(mdd, x / peak - 1)
        return -mdd
    raise ValueError(f"未知 factor_raw kind: {kind}")


_PANEL_CACHE: dict[str, dict] = {}


def _resolve_param(v, base_dirs: list[Path]):
    """把 {"$panel": rel_path, "field": name[, "truncate_as_of": date]} 解析为字段列表。

    truncate_as_of：仅保留 dates ≤ 该日的行（未来数据泄漏类检查用）。
    """
    if isinstance(v, dict) and "$panel" in v:
        rel = v["$panel"]
        if rel not in _PANEL_CACHE:
            for b in base_dirs:
                p = b / rel
                if p.exists():
                    _PANEL_CACHE[rel] = json.loads(p.read_text(encoding="utf-8"))
                    break
            else:
                raise FileNotFoundError(f"panel_ref 快照不存在: {rel}（base_dirs={base_dirs}）")
        panel = _PANEL_CACHE[rel]
        key = v["field"]
        if isinstance(key, str) and "." in key:    # 多股票快照：点路径 STOCK.field
            cur = panel
            for part in key.split("."):
                cur = cur[part]
            vals = cur
        else:
            vals = panel[key]
        if v.get("truncate_as_of"):
            as_of = v["truncate_as_of"]
            idx = [i for i, d in enumerate(panel["dates"]) if d <= as_of]
            vals = [vals[i] for i in idx]
        return vals
    return v


def peg3_math(case: dict, base_dirs: list[Path] | None = None) -> tuple[list[str], str]:
    """返回 (errs, mode)。BLOCKED 案例 SKIP（无权威真值，不得伪造）。

    params 中支持 {"$panel": "snapshots/xxx.json", "field": "close"} 引用：
    从 base_dirs 解析快照文件并取字段（与 PEG-6 引用同源，避免 JSONL 内嵌长序列）。
    """
    if case.get("status") == "BLOCKED":
        return [], "SKIP"
    checks = case.get("peg3_checks") or []
    if not checks:
        return [], "NA"  # 结构型/E2E 型案例无数学重算项
    errs: list[str] = []
    for ch in checks:
        t = ch.get("check")
        exp = ch.get("expected")
        if isinstance(ch.get("params"), dict):
            ch = dict(ch)
            ch["params"] = {k: _resolve_param(v, base_dirs or [])
                            for k, v in ch["params"].items()}
        try:
            if t == "pct_from_ranks":
                ranks = ch["ranks"]
                low_good = ch["low_good"]
                n = len(ranks)
                if n < 2:
                    errs.append(f"PEG-3[pct_from_ranks]: 池规模 {n}<2，百分位无定义")
                    continue
                pcts = _pct_from_ranks(ranks, low_good)
                scores = [(100 - x) if low_good else x for x in pcts]
                exp_list = exp if isinstance(exp, list) else [exp]
                for got, e in zip(scores, exp_list):
                    errs += _check_value("pct_from_ranks", got, e)
            elif t == "mean_of_scores":
                vals = ch["values"]
                if ch.get("skipna", True):
                    vals = [v for v in vals if v is not None]
                if not vals:
                    errs.append("PEG-3[mean_of_scores]: 有效值为空")
                    continue
                errs += _check_value("mean_of_scores", sum(vals) / len(vals), exp)
            elif t == "side_mean":
                fs = ch["factor_scores"]
                vals = [fs[f] for f in ch["fids"] if fs.get(f) is not None]
                if not vals:
                    errs.append(f"PEG-3[side_mean]: 侧面 {ch.get('side')} 无有效因子分")
                    continue
                errs += _check_value("side_mean", sum(vals) / len(vals), exp)
            elif t == "band_of":
                score, want = ch["score"], ch["expected_color"]
                if score is None:
                    got = "gray"
                elif score > 60:
                    got = "red"
                elif score < 40:
                    got = "green"
                else:
                    got = "gray"
                if got != want:
                    errs.append(f"PEG-3[band_of]: 分数 {score} 着色应为 {got}，预期标注 {want}"
                                "（BR-007：>60 红 / <40 绿 / [40,60] 闭区间灰）")
            elif t == "s_from_sraw":
                sraw = ch["sraw"]                                   # {code: value}
                codes = sorted(sraw, key=lambda c: sraw[c])
                n = len(codes)
                ranks = {}
                i = 0
                while i < n:                                        # 平均并列名次
                    j = i
                    while j + 1 < n and sraw[codes[j + 1]] == sraw[codes[i]]:
                        j += 1
                    avg = (i + j) / 2 + 1
                    for k in range(i, j + 1):
                        ranks[codes[k]] = avg
                    i = j + 1
                for code, e in exp.items():
                    pct = float(Fraction(int(round(ranks[code] * 2)), n * 2) * 100)  # 定案公式 rank/N
                    errs += _check_value(f"s_from_sraw[{code}]", pct, e)
            elif t == "tie_symmetry":
                vals = ch["values"]
                tol = ch.get("tolerance", 1e-9)
                if len(vals) < 2 or max(vals) - min(vals) > tol:
                    errs.append(f"PEG-3[tie_symmetry]: 并列值不一致 {vals}")
            elif t == "display_rounding":
                raw, display, dp = _num_pair(ch)
                if abs(round(raw, dp) - display) > 1e-9:
                    errs.append(f"PEG-3[display_rounding]: {display} != round({raw}, {dp})")
            elif t == "h_percentile":
                hist, s_now = ch["hist"], ch["s_now"]
                if not hist:
                    errs.append("PEG-3[h_percentile]: 历史序列为空")
                    continue
                h = sum(1 for x in hist if x <= s_now) / len(hist) * 100
                errs += _check_value("h_percentile", h, exp)
            elif t == "vol_ratio":
                # OPEN-E09（PM 2026-10-18）：vol5/vol20，两窗口均以最新交易日为端点
                #（20 日窗含最近 5 日）；数据不足/零均值视为案例设计错误（raise → 门 FAIL）
                s = ch["params"]["series"]
                if len(s) < 20:
                    raise ValueError("vol_ratio 序列不足 20 根，无法复算")
                vol20 = sum(s[-20:]) / 20
                if vol20 <= 0:
                    raise ValueError("vol_ratio 20日均量 ≤0，无法复算")
                errs += _check_value("vol_ratio", (sum(s[-5:]) / 5) / vol20, exp)
            elif t == "factor_raw":
                errs += _check_value(f"factor_raw:{ch['kind']}", _factor_raw(ch["kind"], ch["params"]), exp)
            else:
                errs.append(f"PEG-3: 未知 check 类型 {t!r}")
        except Exception as exc:  # noqa: BLE001
            errs.append(f"PEG-3[{t}]: 重算异常 {exc!r}")
    return errs, "RUN"


# ---------------------------------------------------------------- PEG-4 真值独立性
def peg4_truth(case: dict) -> list[str]:
    errs: list[str] = []
    to = case.get("truth_origin") or {}
    detail = str(to.get("detail", ""))
    for marker in TRUTH_FORBIDDEN_MARKERS:
        if marker in detail:
            errs.append(f"PEG-4: truth_origin.detail 含违规表述“{marker}”（禁止以被测系统输出为真值）")
    if case.get("status") == "BLOCKED":
        if to.get("method") != "pending_pm_decision":
            errs.append("PEG-4: BLOCKED 案例的 truth_origin.method 必须为 pending_pm_decision（不得伪造预期答案）")
    elif case.get("status") == "FROZEN":
        has_math_truth = bool(case.get("peg3_checks"))
        if has_math_truth and to.get("method") not in TRUTH_MATH_METHODS:
            errs.append(f"PEG-4: 含数学真值的冻结案例 method={to.get('method')!r} 不在 "
                        f"{sorted(TRUTH_MATH_METHODS)}（真值必须独立于被测系统）")
    return errs


# ---------------------------------------------------------------- PEG-5 追溯与覆盖
def _load_ids(*paths: Path) -> set[str]:
    ids: set[str] = set()
    for p in paths:
        if p and p.exists():
            text = p.read_text(encoding="utf-8")
            ids |= set(re.findall(r"(?:REQ|BR|AC|ADR|SCN|NG|E)-\d+(?:-\d+)?", text))
            if "附表" in text:
                ids.add("附表A")   # spec 写作"附表 A"（带空格），契约统一为附表A
                ids.add("附表 A")
    return ids


def peg5_traceability(cases: list[dict], spec_ids: set[str],
                      coverage_ac: bool = True) -> list[str]:
    errs: list[str] = []
    for c in cases:
        for rid in c.get("requirement_id", []):
            if rid not in spec_ids:
                errs.append(f"PEG-5: {c['case_id']} 的 requirement_id {rid!r} 在 spec.md/tasks.md 中不存在")
    if coverage_ac:
        covered = set()
        for c in cases:
            covered |= {r for r in c.get("requirement_id", []) if r.startswith("AC-")}
        for i in range(1, 12):
            ac = f"AC-{i:03d}"
            if ac not in covered:
                errs.append(f"PEG-5: {ac} 无任何案例覆盖（覆盖矩阵缺口）")
    # T-10 四池各 ≥2 只（从 E2E 池归属案例派生；批次内无任何池归属案例时跳过，
    # 全缺场景由上方 AC-003 覆盖检查兜底）
    pool_codes: dict[str, set[str]] = {}
    has_pool_case = False
    for c in cases:
        inp = c.get("input", {})
        if isinstance(inp, dict) and inp.get("expected_pool") and inp.get("code"):
            has_pool_case = True
            pool_codes.setdefault(inp["expected_pool"], set()).add(inp["code"])
    if has_pool_case:
        for pool, need in [("沪深300", 2), ("中证500", 2), ("中证1000", 2), ("中证2000", 2)]:
            got = len(pool_codes.get(pool, set()))
            if got < need:
                errs.append(f"PEG-5: {pool} 测试股仅 {got} 只 < {need}（违反 tasks.md T-10 DoD-1 四池各≥2）")
    return errs


def peg5_stats_sidecar(cases: list[dict], sidecar: dict) -> list[str]:
    errs: list[str] = []
    for key, actual in [
        ("total_cases", len(cases)),
        ("by_split_dev", sum(1 for c in cases if c["split"] == "dev")),
        ("by_split_regression", sum(1 for c in cases if c["split"] == "regression")),
        ("by_split_holdout", sum(1 for c in cases if c["split"] == "holdout")),
        ("by_risk_P0", sum(1 for c in cases if c["risk_level"] == "P0")),
        ("by_risk_P1", sum(1 for c in cases if c["risk_level"] == "P1")),
        ("by_risk_P2", sum(1 for c in cases if c["risk_level"] == "P2")),
        ("blocked", sum(1 for c in cases if c["status"] == "BLOCKED")),
    ]:
        if key in sidecar and sidecar[key] != actual:
            errs.append(f"PEG-5: 统计边车 {key}={sidecar[key]} 与实际 {actual} 不一致")
    return errs


# ---------------------------------------------------------------- PEG-6 引用存在性
def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    h.update(path.read_bytes())
    return h.hexdigest()


def peg6_refs(case: dict, base_dirs: list[Path]) -> list[str]:
    errs: list[str] = []
    snap = case.get("data_snapshot") or {}
    for r in snap.get("refs", []) or []:
        p = Path(r["path"])
        if not p.is_absolute():
            p = next((b / r["path"] for b in base_dirs if (b / r["path"]).exists()), base_dirs[0] / r["path"])
        if not p.exists():
            errs.append(f"PEG-6: 引用文件不存在: {r['path']}")
            continue
        if r.get("sha256"):
            actual = _sha256(p)
            if actual != r["sha256"]:
                errs.append(f"PEG-6: {r['path']} 哈希不匹配（预期 {r['sha256'][:12]}…，实际 {actual[:12]}…）")
    return errs


# ---------------------------------------------------------------- 主流程
def load_jsonl(path: Path) -> list[dict]:
    cases = []
    for ln, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        line = line.strip()
        if not line:
            continue
        try:
            cases.append(json.loads(line))
        except json.JSONDecodeError as exc:
            raise SystemExit(f"{path.name} 第 {ln} 行 JSON 解析失败: {exc}")
    return cases


def run_peg(case_files: list[Path], spec_path: Path, tasks_path: Path,
            stats_sidecar: Path | None = None, base_dirs: list[Path] | None = None,
            coverage_ac: bool = True, extra_sources: list[Path] | None = None) -> dict:
    base_dirs = base_dirs or [Path.cwd()]
    spec_ids = _load_ids(spec_path, tasks_path, *(extra_sources or []))
    sidecar = json.loads(stats_sidecar.read_text(encoding="utf-8")) if stats_sidecar else {}
    all_cases: list[dict] = []
    for f in case_files:
        all_cases += load_jsonl(f)

    report: dict = {
        "checker_version": PEG_CHECKER_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "case_files": [str(f) for f in case_files],
        "spec_id_count": len(spec_ids),
        "gates": {}, "cases": {}, "totals": {}, "overall": "PASS",
    }
    gate_stat = {g: {"pass": 0, "fail": 0, "skip": 0, "na": 0}
                 for g in ("PEG-1", "PEG-2", "PEG-3", "PEG-4", "PEG-5", "PEG-6")}
    for c in all_cases:
        cid = c.get("case_id", "<未知>")
        entry: dict = {}
        for gate, errs in [("PEG-1", peg1_structure(c)), ("PEG-2", peg2_fields(c)),
                           ("PEG-4", peg4_truth(c)), ("PEG-6", peg6_refs(c, base_dirs))]:
            if errs:
                entry[gate] = {"status": "FAIL", "errors": errs}
                gate_stat[gate]["fail"] += 1
            else:
                entry[gate] = {"status": "PASS"}
                gate_stat[gate]["pass"] += 1
        e3, mode3 = peg3_math(c, base_dirs)
        if mode3 == "SKIP":
            entry["PEG-3"] = {"status": "SKIP", "reason": "BLOCKED 案例无权威真值"}
            gate_stat["PEG-3"]["skip"] += 1
        elif mode3 == "NA":
            entry["PEG-3"] = {"status": "NA", "reason": "无数学重算项（结构/E2E 型）"}
            gate_stat["PEG-3"]["na"] += 1
        elif e3:
            entry["PEG-3"] = {"status": "FAIL", "errors": e3}
            gate_stat["PEG-3"]["fail"] += 1
        else:
            entry["PEG-3"] = {"status": "PASS", "checks": len(c.get("peg3_checks") or [])}
            gate_stat["PEG-3"]["pass"] += 1
        if entry.get("_warnings") or c.get("_warnings"):
            entry["warnings"] = c.get("_warnings", [])
        report["cases"][cid] = entry

    e5 = peg5_traceability(all_cases, spec_ids, coverage_ac=coverage_ac)
    if sidecar:
        e5 += peg5_stats_sidecar(all_cases, sidecar)
    if e5:
        report["gates"]["PEG-5"] = {"status": "FAIL", "errors": e5}
        gate_stat["PEG-5"]["fail"] = 1
    else:
        report["gates"]["PEG-5"] = {"status": "PASS"}
        gate_stat["PEG-5"]["pass"] = 1

    report["totals"] = gate_stat
    report["overall"] = "PASS" if all(v["fail"] == 0 for v in gate_stat.values()) else "FAIL"
    return report


def main() -> int:
    ap = argparse.ArgumentParser(description="PEG 评测集质量检查器")
    ap.add_argument("cases", nargs="+", type=Path)
    ap.add_argument("--spec", type=Path, required=True)
    ap.add_argument("--tasks", type=Path, required=True)
    ap.add_argument("--stats", type=Path, default=None)
    ap.add_argument("--base", type=Path, action="append", default=None)
    ap.add_argument("--extra", type=Path, action="append", default=None,
                    help="额外需求编号来源（如 plan.md 的 ADR-xxx）")
    ap.add_argument("--out", type=Path, default=None)
    args = ap.parse_args()

    report = run_peg(args.cases, args.spec, args.tasks, args.stats,
                     args.base or [Path.cwd()], extra_sources=args.extra)
    out = args.out or Path("peg_report.json")
    out.write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")

    print(f"PEG 检查器 v{PEG_CHECKER_VERSION} | 案例数: {len(report['cases'])} | 总体: {report['overall']}")
    for g, s in report["totals"].items():
        print(f"  {g}: PASS={s['pass']} FAIL={s['fail']} SKIP={s['skip']} NA={s['na']}")
    for cid, entry in report["cases"].items():
        for g, r in entry.items():
            if isinstance(r, dict) and r.get("status") == "FAIL":
                print(f"  ✗ {cid} {g}: " + "; ".join(r["errors"][:3]))
    return 0 if report["overall"] == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())
