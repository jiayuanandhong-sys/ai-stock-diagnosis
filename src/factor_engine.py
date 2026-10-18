# -*- coding: utf-8 -*-
"""T-04 因子引擎：18 项日线因子（附表 A）→ 同池百分位 → S（两级排名）→ H（正式口径）。

设计依据：
- BR-006：因子分 = 统一方向后的平均并列名次在同池的百分位分数（0–100）；
          综合分 S = 有效因子等权平均后再放回同池重新排名；
          H = 历史位置参考分（此前 52 个完成周相对位置）；
          缺失项不填分、不计权；
- 附表 A（spec v1.3，18 项 PM 审定冻结）：估值 3 / 价格 7 / 交易 4 / 风险 4；
- BR-007：分档着色 >60 红 / <40 绿 / 40–60 深灰（涨红跌绿）。

接口契约：
- FACTOR_TABLE                   18 项因子定义（内部实现依据；报告对外匿名）
- compute_raw_factors(panel)     单只 18 因子原始值（Spike-2 同口径）
- compute_pool_scores(panels)    全池 → (scores 矩阵, S_raw, S)
- band(score)                    BR-007 三色分档
- weekly_S_series(panels, code)  逐周回算的 S 序列（H 正式口径）
- compute_H(panels, code)        -> (H, S_series, S_now)
"""

from __future__ import annotations

from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

# ---- 附表 A：18 项因子（fid, 侧面, 内部键, 报告匿名标签, 是否低值有利） ---------
FACTOR_TABLE: List[Tuple[str, str, str, str, bool]] = [
    ("F1",  "估值特征", "pe_pct",     "估值·定价水平",        True),
    ("F2",  "估值特征", "pb_pct",     "估值·资产口径",        True),
    ("F3",  "估值特征", "mv_pct",     "市值·规模位置",        True),
    ("F4",  "价格行为", "ret20",      "短周期价格动量",       False),
    ("F5",  "价格行为", "ret60",      "中周期价格动量",       False),
    ("F6",  "价格行为", "ma250_dist", "长周期趋势位置",       False),
    ("F7",  "价格行为", "ma_ratio",   "趋势结构",             False),
    ("F8",  "价格行为", "pos52w",     "年度区间位置",         False),
    ("F9",  "价格行为", "accel",      "短期动能变化",         False),
    ("F10", "价格行为", "updays",     "上行一致性",           False),
    ("F11", "交易行为", "amt20",      "交易活跃度",           False),
    ("F12", "交易行为", "volx",       "量能扩张度",           False),
    ("F13", "交易行为", "vp_corr",    "量价配合度",           False),
    ("F14", "交易行为", "turn20",     "筹码交换强度",         False),
    ("F15", "风险特征", "vol20",      "短期波动",             True),
    ("F16", "风险特征", "vol60",      "中期波动",             True),
    ("F17", "风险特征", "mdd60",      "区间回撤深度",         True),
    ("F18", "风险特征", "amp20",      "日内振幅",             True),
]

FACTOR_KEYS = [f[2] for f in FACTOR_TABLE]
SIDE_ORDER = ["估值特征", "价格行为", "交易行为", "风险特征"]

# BR-007：<40 绿（偏不利）/ 40–60 深灰（中性）/ >60 红（偏有利）；涨红跌绿
RED, GREEN, GRAY = "#C00000", "#1E7B3C", "#555555"


def band(score: Optional[float]) -> Tuple[str, str, str]:
    """分数 → (色名, 十六进制色, 分档标签)。缺失（None/NaN）→ 中性灰。"""
    if score is None or (isinstance(score, float) and np.isnan(score)):
        return ("gray", GRAY, "无数据")
    if score > 60:
        return ("red", RED, "偏有利")
    if score < 40:
        return ("green", GREEN, "偏不利")
    return ("gray", GRAY, "中性")


# ---- 因子原始值（单只，Spike-2 同口径） -----------------------------------------

def compute_raw_factors(panel: pd.DataFrame) -> Dict[str, float]:
    """18 项因子原始值。输入面板列要求与 data_fetcher.fetch_panel 输出一致。"""
    c = panel["close"]; v = panel["volume"]
    r: Dict[str, float] = {}
    r["pe_pct"] = float(panel["pe_ttm"].iloc[-1]) if "pe_ttm" in panel else np.nan
    r["pb_pct"] = float(panel["pb"].iloc[-1]) if "pb" in panel else np.nan
    r["mv_pct"] = float(panel["total_mv"].iloc[-1]) if "total_mv" in panel else np.nan
    r["ret20"] = c.iloc[-1] / c.iloc[-21] - 1 if len(c) >= 21 else np.nan
    r["ret60"] = c.iloc[-1] / c.iloc[-61] - 1 if len(c) >= 61 else np.nan
    r["ma250_dist"] = (c.iloc[-1] / c.rolling(250).mean().iloc[-1]
                       if len(c) >= 250 else np.nan)
    ma20 = c.rolling(20).mean().iloc[-1]; ma60 = c.rolling(60).mean().iloc[-1]
    r["ma_ratio"] = ma20 / ma60 if len(c) >= 60 else np.nan
    w = c.iloc[-252:] if len(c) >= 252 else c
    r["pos52w"] = (w.iloc[-1] - w.min()) / (w.max() - w.min() + 1e-12) if len(w) > 1 else np.nan
    r["accel"] = (c.iloc[-1] / c.iloc[-6] - 1) - r["ret20"] if len(c) >= 21 else np.nan
    r["updays"] = float((c.diff().iloc[-20:] > 0).mean()) if len(c) >= 21 else np.nan
    r["amt20"] = float(panel["amount"].iloc[-20:].mean()) if len(panel) >= 1 else np.nan
    r["volx"] = (v.iloc[-5:].mean() / (v.iloc[-20:].mean() + 1e-12)
                 if len(v) >= 20 and v.iloc[-20:].mean() > 0 else np.nan)
    r["vp_corr"] = (c.iloc[-20:].pct_change().corr(v.pct_change().iloc[-20:])
                    if len(c) >= 21 else np.nan)
    r["turn20"] = (float(panel["turnover"].iloc[-20:].mean())
                   if "turnover" in panel and len(panel) >= 1 else np.nan)
    r["vol20"] = (c.pct_change().iloc[-20:].std() if len(c) >= 21 else np.nan)
    r["vol60"] = (c.pct_change().iloc[-60:].std() if len(c) >= 61 else np.nan)
    w60 = c.iloc[-60:]; cummax = w60.cummax()
    r["mdd60"] = float(1 - (w60 / cummax).min()) if len(c) >= 2 else np.nan
    r["amp20"] = (float(panel["amp"].iloc[-20:].mean())
                  if "amp" in panel and len(panel) >= 1 else np.nan)
    return r


# ---- 同池因子分与 S 合成（BR-006 两级排名） -------------------------------------

def compute_pool_scores(panels: Dict[str, pd.DataFrame]
                        ) -> Tuple[pd.DataFrame, pd.Series, pd.Series]:
    """全池计算。返回：
    scores —— (股票×18) 因子分矩阵（统一方向后的同池百分位，缺失不填分 NaN）
    S_raw  —— 有效因子等权平均（skipna：缺失项不计权）
    S      —— S_raw 放回同池重新排名的百分位（两级排名，BR-006）
    """
    raws = pd.DataFrame({code: compute_raw_factors(df) for code, df in panels.items()}).T
    scores = pd.DataFrame(index=raws.index,
                          columns=[f[0] for f in FACTOR_TABLE], dtype=float)
    for fid, _side, key, _label, low_good in FACTOR_TABLE:
        col = raws[key]
        s = col.rank(method="average", pct=True) * 100     # 平均并列名次百分位
        scores[fid] = (100 - s) if low_good else s         # 统一方向（低值有利→反转）
    scores = scores.astype(float)
    S_raw = scores.mean(axis=1, skipna=True)                # 缺失不填分、不计权
    S = S_raw.rank(method="average", pct=True) * 100        # 放回同池重新排名
    return scores, S_raw, S


def side_means(scores: pd.Series) -> Dict[str, Optional[float]]:
    """四侧面均值（仅拆解展示，不参与加权，BR-006）。"""
    out: Dict[str, Optional[float]] = {}
    for side in SIDE_ORDER:
        fids = [f[0] for f in FACTOR_TABLE if f[1] == side]
        vals = [scores.get(fid) for fid in fids]
        arr = [x for x in vals if x is not None and not (isinstance(x, float) and np.isnan(x))]
        out[side] = float(np.mean(arr)) if arr else None
    return out


# ---- H 正式口径：18 因子周频 S 的 52 完成周历史位置 -------------------------------

def _week_ends(panels: Dict[str, pd.DataFrame], weeks: int = 52) -> List[pd.Timestamp]:
    """全池共同交易日历上，每个 ISO 交易周的最后一个交易日（供周频截面回算）。"""
    cal = max(panels.values(), key=len).index          # 以最长面板的交易日为基准
    s = pd.Series(cal, index=cal)
    iso = s.dt.isocalendar()
    key = iso["year"].astype(str) + "-" + iso["week"].astype(str)
    ends = [g.iloc[-1] for _k, g in s.groupby(key.values)]
    return ends[-(weeks + 1):]                        # 52 个历史完成周 + 当前最新截面


def weekly_S_series(panels: Dict[str, pd.DataFrame], code: str,
                    weeks: int = 52, progress: bool = False) -> pd.Series:
    """逐周回算 S 序列（H 正式口径）。

    对最近 weeks 个完成周的每个周末截面：截断全池面板至该日 → 重算因子与排名 → S_t。
    返回 index=周末日期、value=S 的 Series（最后一位为当前最新截面）。
    """
    ends = _week_ends(panels, weeks)
    if len(ends) < 2:
        raise ValueError("历史数据不足以回算周频 S（需 ≥2 个周末截面）")
    out = {}
    for i, t in enumerate(ends):
        cut = {c: df.loc[df.index <= t] for c, df in panels.items()}
        cut = {c: d for c, d in cut.items() if len(d) >= 21}       # 数据太短的周剔除
        if len(cut) < 2:                                            # 池内至少 2 只才能排名
            continue
        _scores, _sraw, s = compute_pool_scores(cut)
        if code in s.index:
            out[t] = float(s[code])
        if progress:
            print(f"  周截面 {i+1}/{len(ends)}: {t.date()} S={out.get(t, float('nan')):.1f}",
                  flush=True)
    return pd.Series(out).sort_index()


def compute_H(panels: Dict[str, pd.DataFrame], code: str, weeks: int = 52
              ) -> Tuple[float, pd.Series, float]:
    """H = 当前 S 相对此前 weeks 个完成周 S 值的百分位位置（0–100）。

    返回 (H, S_序列, S_now)。历史不足时 H = NaN（缺失不填分）。
    """
    series = weekly_S_series(panels, code, weeks)
    if len(series) < 2:
        return float("nan"), series, float(series.iloc[-1]) if len(series) else float("nan")
    S_now = float(series.iloc[-1])
    hist = series.iloc[:-1]                                        # 此前完成周
    H = float((hist <= S_now).mean() * 100)
    return H, series, S_now


if __name__ == "__main__":
    print("因子定义:", len(FACTOR_TABLE), "项；侧面分布:",
          {s: sum(1 for f in FACTOR_TABLE if f[1] == s) for s in SIDE_ORDER})
