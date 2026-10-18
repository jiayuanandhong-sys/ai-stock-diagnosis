# -*- coding: utf-8 -*-
"""T-02 数据层单元测试（无网络依赖）：
DoD-2 封禁主源自动切备援不崩溃；DoD-3 缓存命中（0 外网请求，计数验证）。
（DoD-1 20/20 拉取与 DoD-4 复算一致性为联网验收，见 evidence 验收脚本。）
"""

import os
import shutil
import sys
import unittest

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

import src.data_fetcher as dfm
from src.data_fetcher import DataFetchError, fetch_panel, register_fetcher

TEST_CACHE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                              "_test_cache_t02")


def _fake_daily_ok(code, start, end):
    """构造 10 行标准化日线（8 列 float）；模拟 1 次外网请求计数。"""
    dfm.STATS.network_calls += 1
    idx = pd.date_range("2025-01-01", periods=10, freq="D")
    data = {c: np.random.default_rng(7).uniform(0.9, 1.1, 10) for c in dfm.DAILY_COLS}
    return pd.DataFrame(data, index=idx)


def _fake_daily_dead(code, start, end):
    dfm.STATS.network_calls += 1
    raise RuntimeError("simulated network block")


def _fake_val_ok(code):
    dfm.STATS.network_calls += 3
    return {"total_mv": 100.0, "pe_ttm": 10.0, "pb": 1.0}


class _Fixture(unittest.TestCase):
    """公共夹具：隔离缓存目录 + 注入 fake 源 + 退出还原。"""

    def setUp(self):
        os.makedirs(TEST_CACHE_DIR, exist_ok=True)
        self._orig_cache = dfm.CACHE_DIR
        dfm.CACHE_DIR = TEST_CACHE_DIR
        dfm.reset_stats()
        register_fetcher("sina", dfm._fetch_daily_sina)          # 还原真实注册
        register_fetcher("eastmoney", dfm._fetch_daily_east)
        dfm.register_valuation_fetcher(dfm.fetch_valuation_snapshot)
        dfm.SOURCE_ORDER[:] = ["sina", "eastmoney"]

    def tearDown(self):
        dfm.CACHE_DIR = self._orig_cache
        shutil.rmtree(TEST_CACHE_DIR, ignore_errors=True)

    def inject(self, primary, secondary, val=None):
        register_fetcher("sina", primary)
        register_fetcher("eastmoney", secondary)
        dfm.register_valuation_fetcher(val or _fake_val_ok)


class TestFallback(_Fixture):
    """DoD-2：人为封禁主源 → 自动切换备援，全程不崩溃。"""

    def test_primary_blocked_falls_back(self):
        self.inject(_fake_daily_dead, _fake_daily_ok)
        df, meta = fetch_panel("000001", "20250101", "20250110", use_cache=False)
        self.assertEqual(meta["source"], "eastmoney")
        self.assertEqual(int(len(df)), 10)
        self.assertIn("000001:sina→eastmoney", dfm.STATS.fallback_events)

    def test_all_sources_blocked_raises_no_crash(self):
        self.inject(_fake_daily_dead, _fake_daily_dead)
        with self.assertRaises(DataFetchError):
            fetch_panel("000001", "20250101", "20250110", use_cache=False)
        self.assertEqual(len(dfm.STATS.fallback_events), 0)   # 无成功切换

    def test_normal_order_prefers_primary(self):
        self.inject(_fake_daily_ok, _fake_daily_ok)
        _, meta = fetch_panel("000001", "20250101", "20250110", use_cache=False)
        self.assertEqual(meta["source"], "sina")


class TestCache(_Fixture):
    """DoD-3：二次运行命中缓存，0 外网请求（计数验证）。"""

    def test_second_call_zero_network(self):
        self.inject(_fake_daily_ok, _fake_daily_ok)
        _, m1 = fetch_panel("600519", "20250101", "20250110", use_cache=True)
        net_after_first = dfm.STATS.network_calls
        self.assertFalse(m1["from_cache"])
        self.assertGreater(net_after_first, 0)

        dfm.reset_stats()
        _, m2 = fetch_panel("600519", "20250101", "20250110", use_cache=True)
        self.assertTrue(m2["from_cache"])
        self.assertEqual(dfm.STATS.network_calls, 0)     # 零外网请求
        self.assertEqual(dfm.STATS.cache_hits, 1)

    def test_cache_bypass_with_use_cache_false(self):
        self.inject(_fake_daily_ok, _fake_daily_ok)
        fetch_panel("600519", "20250101", "20250110", use_cache=True)
        dfm.reset_stats()
        _, m = fetch_panel("600519", "20250101", "20250110", use_cache=False)
        self.assertFalse(m["from_cache"])
        self.assertGreater(dfm.STATS.network_calls, 0)

    def test_stale_cache_refetches(self):
        """隔日缓存失效 → 重新联网（fetch_date 戳机制）。"""
        self.inject(_fake_daily_ok, _fake_daily_ok)
        fetch_panel("600519", "20250101", "20250110", use_cache=True)
        dfm.reset_stats()
        # 篡改缓存中的 fetch_date 为昨天 → 应判过期重拉
        import src.data_fetcher as m
        p = m._panel_cache_path("600519", "20250101", "20250110")
        cached = pd.read_parquet(p)
        cached["fetch_date"] = "2000-01-01"
        cached.to_parquet(p)
        _, meta = fetch_panel("600519", "20250101", "20250110", use_cache=True)
        self.assertFalse(meta["from_cache"])
        self.assertGreater(dfm.STATS.network_calls, 0)


class TestPanelStructure(_Fixture):
    """面板结构 = Spike-2 同构（DoD-4 前置）。"""

    def test_panel_columns_match_spike2(self):
        self.inject(_fake_daily_ok, _fake_daily_ok)
        df, _ = fetch_panel("603799", "20250101", "20250110", use_cache=True)
        for col in dfm.DAILY_COLS + ["pe_ttm", "pb", "total_mv", "code", "source", "fetch_date"]:
            self.assertIn(col, df.columns, f"缺列 {col}")
        self.assertEqual(df["code"].iloc[0], "603799")

    def test_missing_valuation_nan_not_zero(self):
        """估值失败 → NaN（缺失不填分，不填 0）。"""
        self.inject(_fake_daily_ok, _fake_daily_ok,
                    val=lambda code: {"total_mv": float("nan"), "pe_ttm": float("nan"),
                                      "pb": float("nan")})
        df, meta = fetch_panel("603799", "20250101", "20250110", use_cache=False)
        self.assertEqual(meta["val_nan"], 30)   # 10 行 × 3 估值列，整列 NaN（缺失不填 0）
        self.assertTrue(df["pe_ttm"].isna().all())


if __name__ == "__main__":
    unittest.main(verbosity=2)
