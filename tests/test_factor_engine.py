# -*- coding: utf-8 -*-
"""T-04 因子引擎用例集（合成数据，无网络）：
DoD-1 并列名次/方向统一/缺失不填分（BR-006 三大口径规则）；
DoD-2 S 两级排名手算比对；
DoD-4 分档着色边界（BR-007）。
（DoD-3 H 正式口径实跑见 evidence 联网验收脚本。）
"""

import os
import sys
import unittest

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

import src.factor_engine as fe
from src.factor_engine import (FACTOR_TABLE, band, compute_H, compute_pool_scores,
                               compute_raw_factors, side_means, weekly_S_series)

FIDS = [f[0] for f in FACTOR_TABLE]
KEYS = [f[2] for f in FACTOR_TABLE]      # 引擎实际查询的内部键（pe_pct/ret20/...）


def _panel(n=300, base=10.0, drift=0.01, vol=1.0):
    """合成日线面板：close 线性上升/下降，volume 常数，估值常数列。"""
    idx = pd.bdate_range("2025-01-01", periods=n)
    close = pd.Series([base * (1 + drift) ** i for i in range(n)], index=idx)
    df = pd.DataFrame({
        "open": close.shift(1).fillna(close.iloc[0]),
        "high": close * 1.01, "low": close * 0.99, "close": close,
        "volume": pd.Series(vol, index=idx), "amount": close * vol * 100,
        "amp": 2.0, "turnover": pd.Series(1.5, index=idx),
        "pe_ttm": 20.0, "pb": 2.0, "total_mv": 5e10,
    })
    df["code"] = "T"
    return df


class TestPoolScores(unittest.TestCase):
    """BR-006 口径：并列名次/方向统一/缺失不填分/两级排名（DoD-1 + DoD-2）。"""

    def setUp(self):
        # 注入手工原始值矩阵（3 只股票），精确手算比对
        self._orig = fe.compute_raw_factors
        raws = {
            "A": {k: v for k, v in zip(KEYS, [10, 10, 10, 1, 1, 1, 1, 1, 1, 1,
                                              100, 1, 1, 1, 0.1, 0.1, 0.01, 1])},
            "B": {k: v for k, v in zip(KEYS, [20, 20, 20, 2, 2, 2, 2, 2, 2, 2,
                                              200, 2, 2, 2, 0.2, 0.2, 0.02, 2])},
            "C": {k: v for k, v in zip(KEYS, [30, 30, 30, 3, 3, 3, 3, 3, 3, 3,
                                              300, 3, 3, 3, 0.3, 0.3, 0.03, 3])},
        }
        def fake_raw(panel):
            return raws[panel["code"].iloc[0]]
        fe.compute_raw_factors = fake_raw
        self.panels = {c: _panel().assign(code=c) for c in "ABC"}

    def tearDown(self):
        fe.compute_raw_factors = self._orig

    def test_low_good_direction_reversed(self):
        """DoD-1 方向统一：低值有利因子（F1 PE）低 PE → 高分。
        3 只池：A rank1 → pct 33.3 → 反转 66.7；C rank3 → pct 100 → 反转 0。"""
        scores, _sraw, _S = compute_pool_scores(self.panels)
        self.assertAlmostEqual(scores.loc["A", "F1"], 66.67, places=1)
        self.assertAlmostEqual(scores.loc["C", "F1"], 0.0, places=1)

    def test_high_good_direction_kept(self):
        """高值有利因子（F4 动量）高值 → 高分（C=100, A=33.3）。"""
        scores, _sraw, _S = compute_pool_scores(self.panels)
        self.assertAlmostEqual(scores.loc["C", "F4"], 100.0, places=1)
        self.assertAlmostEqual(scores.loc["A", "F4"], 33.33, places=1)

    def test_tied_ranks_averaged(self):
        """DoD-1 平均并列名次：B、C 并列 → 名次 (2+3)/2=2.5 → pct 83.3 → 反转 16.7。"""
        raws = {
            "A": dict(zip(KEYS, [10] * 18)),
            "B": dict(zip(KEYS, [20] * 18)),
            "C": dict(zip(KEYS, [20] * 18)),
        }
        def fake(panel):
            return raws[panel["code"].iloc[0]]
        fe.compute_raw_factors = fake
        try:
            scores, _sraw, _S = compute_pool_scores(self.panels)
            self.assertAlmostEqual(scores.loc["B", "F1"], 16.67, places=1)
            self.assertAlmostEqual(scores.loc["C", "F1"], 16.67, places=1)
            self.assertAlmostEqual(scores.loc["A", "F1"], 66.67, places=1)
        finally:
            fe.compute_raw_factors = self._orig

    def test_missing_not_weighted(self):
        """DoD-1 缺失不填分不计权：A 的 F18 缺失 → S_raw = 其余 17 项均值。"""
        raws = {
            "A": dict(zip(KEYS, [10] * 17 + [np.nan])),
            "B": dict(zip(KEYS, [20] * 18)),
            "C": dict(zip(KEYS, [30] * 18)),
        }
        def fake(panel):
            return raws[panel["code"].iloc[0]]
        fe.compute_raw_factors = fake
        try:
            scores, S_raw, _S = compute_pool_scores(self.panels)
            self.assertTrue(np.isnan(scores.loc["A", "F18"]))          # 不填分
            expected = scores.loc["A", FIDS[:17]].mean()               # 17 项均值（不计权）
            self.assertAlmostEqual(S_raw["A"], expected, places=6)
        finally:
            fe.compute_raw_factors = self._orig

    def test_two_level_ranking_handcheck(self):
        """DoD-2 两级排名手算比对（A 全因子最优 / B 居中 / C 全最差）。

        手算（3 只池）：low_good 7 项（估值3+风险4）：最优 A → 100-33.3=66.7；
        high_good 11 项：最优 A → 100。故
        S_raw[A] = (7×66.67 + 11×100)/18 = 87.0；B = (7×33.3+11×66.7)/18 = 53.7；
        C = (7×0 + 11×33.3)/18 = 20.4。
        S 两级排名：A=100、B=66.7、C=33.3，且与 S_raw 序一致。
        """
        low_keys = [f[2] for f in FACTOR_TABLE if f[4]]
        high_keys = [f[2] for f in FACTOR_TABLE if not f[4]]
        def make(code, best):
            d = {}
            for k in low_keys:
                d[k] = {"A": 1, "B": 2, "C": 3}[code]
            for k in high_keys:
                d[k] = {"A": 3, "B": 2, "C": 1}[code]
            return d
        raws = {c: make(c, None) for c in "ABC"}
        def fake(panel):
            return raws[panel["code"].iloc[0]]
        fe.compute_raw_factors = fake
        try:
            scores, S_raw, S = compute_pool_scores(self.panels)
            self.assertAlmostEqual(S_raw["A"], (7 * 66.6667 + 11 * 100) / 18, places=1)
            self.assertAlmostEqual(S_raw["B"], (7 * 33.3333 + 11 * 66.6667) / 18, places=1)
            self.assertAlmostEqual(S_raw["C"], (7 * 0 + 11 * 33.3333) / 18, places=1)
            self.assertAlmostEqual(S["A"], 100.0, places=1)             # 放回同池重排名
            self.assertAlmostEqual(S["B"], 66.67, places=1)
            self.assertAlmostEqual(S["C"], 33.33, places=1)
            self.assertEqual(list(S.sort_values(ascending=False).index),
                             list(S_raw.sort_values(ascending=False).index))
        finally:
            fe.compute_raw_factors = self._orig


class TestRawFactors(unittest.TestCase):
    """因子原始值口径（合成面板已知值验证 + 短数据缺失保护）。"""

    def test_known_values_up_trend(self):
        df = _panel(n=300, base=10.0, drift=0.01)     # 恒定日涨 1%
        r = compute_raw_factors(df)
        self.assertAlmostEqual(r["ret20"], 1.01 ** 20 - 1, places=4)
        self.assertAlmostEqual(r["ret60"], 1.01 ** 60 - 1, places=4)
        self.assertEqual(r["updays"], 1.0)            # 每天都涨
        self.assertAlmostEqual(r["volx"], 1.0, places=6)   # 量恒定 → 均量比 1
        self.assertAlmostEqual(r["pos52w"], 1.0, places=6) # 创 52 周新高 → 位于区间顶
        self.assertLess(r["mdd60"], 1e-9)             # 单调上行 → 回撤≈0
        self.assertEqual(r["pe_pct"], 20.0)

    def test_short_data_nan_protection(self):
        df = _panel(n=50)                             # 不足 60/250 天
        r = compute_raw_factors(df)
        self.assertTrue(np.isnan(r["ret60"]))         # 数据不足 → 不填分
        self.assertTrue(np.isnan(r["ma250_dist"]))
        self.assertFalse(np.isnan(r["ret20"]))


class TestBand(unittest.TestCase):
    """DoD-4：BR-007 分档着色边界（>60 红 / <40 绿 / 40–60 深灰）。"""

    def test_boundaries(self):
        self.assertEqual(band(61)[0], "red")
        self.assertEqual(band(60.0001)[0], "red")
        self.assertEqual(band(60)[0], "gray")         # 60 归中性（>60 才红）
        self.assertEqual(band(50)[0], "gray")
        self.assertEqual(band(40)[0], "gray")         # 40 归中性（<40 才绿）
        self.assertEqual(band(39.999)[0], "green")
        self.assertEqual(band(0)[0], "green")
        self.assertEqual(band(None)[0], "gray")      # 缺失 → 无数据灰
        self.assertEqual(band(float("nan"))[0], "gray")

    def test_colors_cn_market_convention(self):
        """涨红跌绿：偏有利=红 #C00000，偏不利=绿 #1E7B3C，中性=深灰。"""
        self.assertEqual(band(100)[1], "#C00000")
        self.assertEqual(band(10)[1], "#1E7B3C")
        self.assertEqual(band(50)[1], "#555555")


class TestSideMeans(unittest.TestCase):
    """四侧面仅为拆解展示，不参与加权（AC-002）。"""

    def test_side_means_structure(self):
        scores = pd.Series({f: 50.0 for f in FIDS})
        sm = side_means(scores)
        self.assertEqual(set(sm), set(fe.SIDE_ORDER))
        self.assertEqual(sm["估值特征"], 50.0)
        # 侧面数量：3/7/4/4（附表 A）
        self.assertEqual([sum(1 for f in FACTOR_TABLE if f[1] == s) for s in fe.SIDE_ORDER],
                         [3, 7, 4, 4])


class TestWeeklyH(unittest.TestCase):
    """H 正式口径机制（合成数据：恒强股 vs 恒弱股）。"""

    def test_H_range_and_monotonic(self):
        panels = {
            "STRONG": _panel(n=300, base=10, drift=0.01),   # 恒涨
            "WEAK": _panel(n=300, base=10, drift=-0.01),    # 恒跌
        }
        panels["STRONG"]["code"] = "STRONG"
        panels["WEAK"]["code"] = "WEAK"
        series = weekly_S_series(panels, "STRONG", weeks=30)   # 小窗口快速验证
        self.assertGreaterEqual(len(series), 20)               # 周截面足够多
        H, s, s_now = compute_H(panels, "STRONG", weeks=30)
        self.assertTrue(np.isnan(H) or 0 <= H <= 100)
        # 恒强股在整个历史中都排第一 → 当前 S 不低于任何历史周 → H = 100
        self.assertAlmostEqual(H, 100.0, places=1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
