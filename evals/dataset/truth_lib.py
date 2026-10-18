# -*- coding: utf-8 -*-
"""truth_lib.py —— Golden Dataset 独立真值计算库（纯 Python，零第三方依赖）。

独立性声明（PEG-4）：
- 与被测产品 src/factor_engine.py（pandas/numpy 实现）完全独立的代码路径；
- 百分位采用**定案公式** pct = 平均并列名次/N_valid*100（OPEN-E01 经 PM 于 2026-10-18
  裁决为 rank/N，与产品 pandas rank(pct=True) 同口径），使用 Fraction 精确运算后再转 float；
- 因子原始值按 spec 附表 A + Spike-2 口径（样本标准差 ddof=1、量价相关 19 对口径等）
  从面板数据独立复算，本库即为标准答案来源，禁止参考产品输出。

面板合成数据为确定性构造（无随机数），任何人用本库重跑得到逐位一致的真值。
"""
from __future__ import annotations

import math
from datetime import date, timedelta
from fractions import Fraction
from typing import Dict, List, Optional

# 附表 A：fid -> (侧面, 是否低值有利)
FACTOR_TABLE = [
    ("F1", "估值特征", True), ("F2", "估值特征", True), ("F3", "估值特征", True),
    ("F4", "价格行为", False), ("F5", "价格行为", False), ("F6", "价格行为", False),
    ("F7", "价格行为", False), ("F8", "价格行为", False), ("F9", "价格行为", False),
    ("F10", "价格行为", False),
    ("F11", "交易行为", False), ("F12", "交易行为", False), ("F13", "交易行为", False),
    ("F14", "交易行为", False),
    ("F15", "风险特征", True), ("F16", "风险特征", True), ("F17", "风险特征", True),
    ("F18", "风险特征", True),
]
FIDS = [f[0] for f in FACTOR_TABLE]
LOW_GOOD = {f[0]: f[2] for f in FACTOR_TABLE}
# FID -> 原始因子键（raw_factors 输出键）
RAW_KEY = {"F1": "pe_pct", "F2": "pb_pct", "F3": "mv_pct", "F4": "ret20",
           "F5": "ret60", "F6": "ma250_dist", "F7": "ma_ratio", "F8": "pos52w",
           "F9": "accel", "F10": "updays", "F11": "amt20", "F12": "volx",
           "F13": "vp_corr", "F14": "turn20", "F15": "vol20", "F16": "vol60",
           "F17": "mdd60", "F18": "amp20"}
SIDES = ["估值特征", "价格行为", "交易行为", "风险特征"]
SIDE_FIDS = {s: [f[0] for f in FACTOR_TABLE if f[1] == s] for s in SIDES}


# ---------------------------------------------------------------- 合成面板
def business_days(start: str, n: int) -> List[str]:
    d0 = date.fromisoformat(start)
    out, d = [], d0
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d.isoformat())
        d += timedelta(days=1)
    return out


def synth_panel(seed: int, n_days: int, start: str = "2025-06-02",
                pe: Optional[float] = None, pb: Optional[float] = None,
                mv: Optional[float] = None, drift: float = 0.001,
                vol_base: float = 8_000_000.0, tail_vol_boost: float = 1.0,
                zero_volume_tail: int = 0, limit_up_streak: int = 0,
                price_base: float = 25.0) -> dict:
    """确定性合成日线面板（列口径与产品 data_fetcher.fetch_panel 输出一致）。

    drift 控制趋势（ret20/ret60 排序用）；tail_vol_boost 控制量能扩张（volx 用）；
    zero_volume_tail / limit_up_streak 供 L2-027 极端值场景。
    """
    dates = business_days(start, n_days)
    close, volume = [], []
    for t in range(n_days):
        if limit_up_streak and t >= n_days - limit_up_streak:
            c = (close[-1] * 1.10) if close else price_base
        else:
            c = price_base * (1.0 + drift * t
                              + 0.05 * math.sin(0.31 * (t + seed))
                              + 0.03 * math.cos(0.17 * (t + 2 * seed)))
        close.append(round(c, 2))
        if zero_volume_tail and t >= n_days - zero_volume_tail:
            v = 0.0
        else:
            v = vol_base * (1 + 0.4 * math.sin(0.23 * (t + 3 * seed))) * (
                tail_vol_boost if t >= n_days - 5 else 1.0)
        volume.append(int(max(v, 0)))
    amount = [round(c * v, 2) for c, v in zip(close, volume)]
    turnover = [round(1.5 + 0.8 * abs(math.sin(0.13 * (t + seed))), 3) for t in range(n_days)]
    amp = [round(1.2 + 1.1 * abs(math.cos(0.19 * (t + seed))), 3) for t in range(n_days)]
    return {
        "dates": dates,
        "open": [round(c * 0.995, 2) for c in close],
        "high": [round(c * 1.012, 2) for c in close],
        "low": [round(c * 0.988, 2) for c in close],
        "close": close,
        "volume": volume,
        "amount": amount,
        "turnover": turnover,
        "amp": amp,
        "pe_ttm": [pe if pe is not None else 15.0 + 5 * math.sin(seed + 0.02 * t) for t in range(n_days)],
        "pb": [pb if pb is not None else 2.0 + 0.5 * math.cos(seed + 0.02 * t) for t in range(n_days)],
        "total_mv": [mv if mv is not None else 5e10 + seed * 1e9 + t * 1e7 for t in range(n_days)],
    }


# ---------------------------------------------------------------- 18 因子原始值
def _mean(xs: List[float]) -> float:
    return sum(xs) / len(xs)


def _stdev(xs: List[float]) -> float:
    m = _mean(xs)
    return math.sqrt(sum((x - m) ** 2 for x in xs) / (len(xs) - 1))  # ddof=1（Spike-2 口径）


def _pearson(xs: List[float], ys: List[float]) -> float:
    mx, my = _mean(xs), _mean(ys)
    cov = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    vx = sum((x - mx) ** 2 for x in xs)
    vy = sum((y - my) ** 2 for y in ys)
    return cov / (math.sqrt(vx) * math.sqrt(vy))


def raw_factors(panel: dict) -> Dict[str, Optional[float]]:
    """单只股票 18 因子原始值（独立实现；NaN 语义 = 缺失不填分）。"""
    c, v = panel["close"], panel["volume"]
    n = len(c)
    out: Dict[str, Optional[float]] = {}
    out["pe_pct"] = panel["pe_ttm"][-1] if panel.get("pe_ttm") else None
    out["pb_pct"] = panel["pb"][-1] if panel.get("pb") else None
    out["mv_pct"] = panel["total_mv"][-1] if panel.get("total_mv") else None
    out["ret20"] = c[-1] / c[-21] - 1 if n >= 21 else None
    out["ret60"] = c[-1] / c[-61] - 1 if n >= 61 else None
    out["ma250_dist"] = c[-1] / _mean(c[-250:]) if n >= 250 else None  # F6=收盘价/MA250（附表A 比值口径）
    out["ma_ratio"] = (_mean(c[-20:]) / _mean(c[-60:])) if n >= 60 else None
    w = c[-252:] if n >= 252 else c
    out["pos52w"] = (w[-1] - min(w)) / (max(w) - min(w) + 1e-12) if len(w) > 1 else None
    out["accel"] = (c[-1] / c[-6] - 1) - out["ret20"] if n >= 21 else None
    out["updays"] = (sum(1 for i in range(n - 20, n) if c[i] > c[i - 1]) / 20) if n >= 21 else None
    out["amt20"] = _mean(panel["amount"][-20:]) if n >= 1 else None
    m20 = _mean(v[-20:]) if n >= 20 else 0.0
    out["volx"] = (_mean(v[-5:]) / (m20 + 1e-12)) if (n >= 20 and m20 > 0) else None
    # 量价相关：close 侧近 20 日的 pct_change（19 个有效值），volume 侧全面板 pct_change
    # 的近 20 日切片（20 个有效值），对齐后 19 对（Spike-2 口径）
    if n >= 21 and all(x > 0 for x in v[n - 21:]):
        idx = list(range(n - 19, n))
        c_ret = [c[i] / c[i - 1] - 1 for i in idx]
        v_ret = [v[i] / v[i - 1] - 1 for i in idx]
        out["vp_corr"] = _pearson(c_ret, v_ret)
    else:
        out["vp_corr"] = None  # 零成交 → 相关性无定义（缺失不填分）
    out["turn20"] = _mean(panel["turnover"][-20:]) if n >= 1 else None
    rets = [c[i] / c[i - 1] - 1 for i in range(1, n)]
    out["vol20"] = _stdev(rets[-20:]) if n >= 21 else None
    out["vol60"] = _stdev(rets[-60:]) if n >= 61 else None
    if n >= 2:
        w60 = c[-60:]
        peak, mdd = w60[0], 0.0
        for x in w60:
            peak = max(peak, x)
            mdd = min(mdd, x / peak - 1)
        out["mdd60"] = -mdd
    else:
        out["mdd60"] = None
    out["amp20"] = _mean(panel["amp"][-20:]) if n >= 1 else None
    return out


# ---------------------------------------------------------------- 打分管线（暂拟公式）
def avg_ranks(values: List[Optional[float]]) -> List[Optional[float]]:
    """平均并列名次（升序，缺失不参与）。"""
    idx_valid = [i for i, x in enumerate(values) if x is not None]
    sorted_valid = sorted(idx_valid, key=lambda i: values[i])
    ranks: Dict[int, float] = {}
    k = 0
    while k < len(sorted_valid):
        j = k
        while j + 1 < len(sorted_valid) and values[sorted_valid[j + 1]] == values[sorted_valid[k]]:
            j += 1
        avg = (k + j) / 2 + 1
        for m in range(k, j + 1):
            ranks[sorted_valid[m]] = avg
        k = j + 1
    return [ranks.get(i) for i in range(len(values))]


def pct_from_rank(rank: float, n_valid: int) -> float:
    """定案公式（OPEN-E01，PM 2026-10-18 裁决为 rank/N）：pct = 平均并列名次/N_valid*100。

    Fraction 精确实现：名次以 0.5 步长表示（平均并列），分子分母同乘 2 取整。
    与产品 pandas rank(pct=True) 同口径，但代码路径完全独立（纯 Python 手写排名）。
    """
    return float(Fraction(int(round(rank * 2)), n_valid * 2) * 100)


def factor_scores(raws: Dict[str, Dict[str, Optional[float]]]) -> Dict[str, Dict[str, Optional[float]]]:
    """全池因子分：逐因子同池百分位（定案公式 rank/N），缺失不填分（None 传播）。"""
    codes = list(raws)
    scores: Dict[str, Dict[str, Optional[float]]] = {c: {} for c in codes}
    for fid in FIDS:
        key = RAW_KEY[fid]
        vals = [raws[c].get(key) for c in codes]
        n_valid = sum(1 for x in vals if x is not None)
        ranks = avg_ranks(vals)
        for c, r, x in zip(codes, ranks, vals):
            if x is None or n_valid < 2:
                scores[c][fid] = None if x is None else 50.0  # 单股池：无排名意义（数据集不使用）
            else:
                pct = pct_from_rank(r, n_valid)
                scores[c][fid] = (100 - pct) if LOW_GOOD[fid] else pct
    return scores


def s_raw(scores: Dict[str, Dict[str, Optional[float]]]) -> Dict[str, float]:
    """S_raw = 有效因子等权平均（缺失不填分不计权）。"""
    out = {}
    for c, fs in scores.items():
        vals = [v for v in fs.values() if v is not None]
        out[c] = _mean(vals) if vals else float("nan")
    return out


def s_final(sraw: Dict[str, float]) -> Dict[str, float]:
    """S = S_raw 放回同池重新排名的百分位（两级排名，定案公式 rank/N）。"""
    codes = [c for c in sraw if sraw[c] == sraw[c]]  # 排除 NaN
    ranks = avg_ranks([sraw[c] for c in codes])
    n = len(codes)
    return {c: pct_from_rank(r, n) for c, r in zip(codes, ranks) if r is not None}


def side_means(scores: Dict[str, Dict[str, Optional[float]]]) -> Dict[str, Optional[float]]:
    out = {}
    for c, fs in scores.items():
        out[c] = {s: (_mean([fs[f] for f in SIDE_FIDS[s] if fs.get(f) is not None]) if any(
            fs.get(f) is not None for f in SIDE_FIDS[s]) else None) for s in SIDES}
    return out


def band(score: Optional[float]) -> str:
    """BR-007：>60 红 / <40 绿 / [40,60] 闭区间灰；缺失=无数据（灰）。"""
    if score is None or score != score:
        return "gray"
    if score > 60:
        return "red"
    if score < 40:
        return "green"
    return "gray"


# ---------------------------------------------------------------- H 与周频
def week_ends(dates: List[str], weeks: int) -> List[str]:
    """每个 ISO 交易周的最后一个交易日（独立实现），取最后 weeks+1 个。"""
    def iso_key(d: str) -> tuple:
        dd = date.fromisoformat(d)
        iy, iw, _ = dd.isocalendar()
        return (iy, iw)
    ends: List[str] = []
    for d in dates:
        if not ends or iso_key(ends[-1]) != iso_key(d):
            ends.append(d)
        else:
            ends[-1] = d
    return ends[-(weeks + 1):]


def weekly_s(panels: Dict[str, dict], code: str, weeks: int = 52) -> Dict[str, float]:
    """逐周回算 S 序列（定案公式 rank/N）。截断/剔除规则与产品同口径：周截面截断、
    <21 行的股票剔除、池内 <2 只跳过。"""
    all_dates = sorted({d for p in panels.values() for d in p["dates"]})
    ends = week_ends(all_dates, weeks)
    out: Dict[str, float] = {}
    for t in ends:
        cut = {}
        for c, p in panels.items():
            sub = _truncate(p, t)
            if len(sub["close"]) >= 21:
                cut[c] = sub
        if len(cut) < 2:
            continue
        raws = {c: raw_factors(p) for c, p in cut.items()}
        _sc = factor_scores(raws)
        sraw = s_raw(_sc)
        s = s_final(sraw)
        if code in s:
            out[t] = s[code]
    return out


def h_from_series(series: Dict[str, float]) -> Optional[float]:
    """H = 当前 S 相对此前完成周 S 值的百分位位置。"""
    if len(series) < 2:
        return None
    keys = sorted(series)
    s_now = series[keys[-1]]
    hist = [series[k] for k in keys[:-1]]
    return sum(1 for x in hist if x <= s_now) / len(hist) * 100


def _truncate(panel: dict, as_of: str) -> dict:
    idx = [i for i, d in enumerate(panel["dates"]) if d <= as_of]
    return {k: ([v[i] for i in idx] if isinstance(v, list) else v) for k, v in panel.items()}
