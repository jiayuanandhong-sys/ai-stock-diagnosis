# -*- coding: utf-8 -*-
"""REQ-003 方案A（PM 2026-10-18 D2/D4/D5 定向）自动判断单测：

条件2 业绩预告/定期财报披露（announcement_source：cninfo 拉取+缓存+可注入检测）；
条件3 成交结构异常（OPEN-E09：近5日均量 > 2×近20日均量，严格大于）；
条件1 周涨跌幅 ≥5% 回归（行为不变）。

纪律：全部用例离线确定性（公告走注入 / 合成代码不触发网络），真实网络拉取
单独经 announcement_source.__main__ 冒烟验证（600768 cninfo 实测）。
"""

import os
import sys
import unittest
from datetime import date, timedelta

import pandas as pd

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from src import announcement_source as AS
from src.trade_cal import (VOLUME_ANOMALY_TIMES, early_review_flags,
                           volume_anomaly_ratio)

GENERIC = "如遇业绩预告/定期财报披露或成交结构异常，建议提前复核。"


def mk_panel(week_ret_tail, vols, n_days=30, start="2026-09-01"):
    """n_days 日面板；末 6 日收盘价由 week_ret_tail 精确给定（控制周涨跌幅）。"""
    closes = [100.0 + 0.1 * i for i in range(n_days - 6)] + week_ret_tail
    dates = pd.bdate_range(start, periods=n_days)
    return pd.DataFrame({"close": closes, "volume": vols}, index=dates)


CALM_TAIL = [100.0, 100.3, 100.6, 101.0, 101.5, 102.0]    # +2.0% <5%
TRIG_TAIL = [100.0, 100.5, 101.0, 101.5, 102.0, 107.0]    # +7.0% ≥5%
FLAT_VOL = [1_000_000.0] * 30


class TestVolumeAnomalyRatio(unittest.TestCase):
    """OPEN-E09（PM 2026-10-18）：近5日均量 > 2×近20日均量 = 异常（严格大于）。"""

    def test_ratio_surge(self):
        vols = [1e6] * 25 + [5e6] * 5          # vol5=5M, vol20=(15M+25M)/20=2M → 2.5
        self.assertAlmostEqual(volume_anomaly_ratio(mk_panel(CALM_TAIL, vols)), 2.5)

    def test_ratio_exactly_two_not_anomaly(self):
        vols = [1e6] * 25 + [3e6] * 5          # vol5=3M, vol20=1.5M → 恰 2.0，不触发
        ratio = volume_anomaly_ratio(mk_panel(CALM_TAIL, vols))
        self.assertAlmostEqual(ratio, 2.0)
        flags = early_review_flags(mk_panel(CALM_TAIL, vols), "SYN-VEDGE")
        self.assertFalse(any("近5日平均成交量" in f for f in flags))   # 特定触发语不得出现

    def test_insufficient_data_none(self):
        p = mk_panel(CALM_TAIL, [1e6] * 19, n_days=19)
        self.assertIsNone(volume_anomaly_ratio(p))

    def test_zero_baseline_none(self):
        vols = [0.0] * 25 + [5e6] * 5          # vol20=1.25M>0 —— 改为全零验证 ≤0 分支
        self.assertAlmostEqual(volume_anomaly_ratio(mk_panel(CALM_TAIL, vols)),
                               5e6 / 1.25e6)
        self.assertIsNone(volume_anomaly_ratio(mk_panel(CALM_TAIL, [0.0] * 30)))


class TestEarlyReviewCondition3(unittest.TestCase):
    def test_volume_trigger_on(self):
        vols = [1e6] * 25 + [5e6] * 5
        flags = early_review_flags(mk_panel(CALM_TAIL, vols), "SYN-VTRIG")
        hit = [f for f in flags if "近5日平均成交量" in f]     # 特定触发语（区别于通用提示语）
        self.assertEqual(len(hit), 1)
        self.assertIn("2.5 倍", hit[0])
        self.assertIn("成交结构异常", hit[0])
        self.assertIn("提前复核", hit[0])
        self.assertIn(GENERIC, flags)           # 合成代码无公告数据 → 通用提示语保留

    def test_flat_volume_no_trigger(self):
        flags = early_review_flags(mk_panel(CALM_TAIL, FLAT_VOL), "SYN-CALM")
        self.assertEqual(flags, [GENERIC])      # 仅通用提示语（条件1/3 均未触发）


class TestAnnouncementQualifying(unittest.TestCase):
    def test_keywords(self):
        for t in ["2026年第三季度业绩预告", "2025年年度报告", "2026年半年度报告摘要",
                  "2026年第一季度报告", "2025年度业绩快报", "关于2026年半年度业绩预告的更正公告"]:
            self.assertTrue(AS.is_qualifying(t), t)
        for t in ["关于召开2026年半年度业绩说明会的公告", "关于收到《民事上诉状》暨诉讼进展公告",
                  "关于召开2026年半年度业绩说明会召开情况的公告", ""]:
            self.assertFalse(AS.is_qualifying(t), t)

    def test_detect_window(self):
        as_of = date(2026, 10, 16)
        in_win = {"date": "2026-10-14", "title": "2026年第三季度业绩预告"}
        out_win = {"date": "2026-09-20", "title": "2026年第三季度业绩预告"}
        nonqual = {"date": "2026-10-14", "title": "关于召开业绩说明会的公告"}
        self.assertEqual(AS.detect_qualifying([in_win, nonqual], as_of=as_of), [in_win])
        self.assertEqual(AS.detect_qualifying([out_win], as_of=as_of), [])
        self.assertEqual(AS.detect_qualifying([], as_of=as_of), [])
        # 边界：恰为 as_of - 10 天（开区间下界）不入；恰为 as_of（闭区间上界）入
        self.assertEqual(AS.detect_qualifying(
            [{"date": "2026-10-06", "title": "业绩预告"}], as_of=as_of), [])
        self.assertEqual(len(AS.detect_qualifying(
            [{"date": "2026-10-16", "title": "业绩预告"}], as_of=as_of)), 1)


class TestEarlyReviewCondition2(unittest.TestCase):
    """条件2 注入式：命中/非符合/窗口外 三态。"""

    def setUp(self):
        self.panel = mk_panel(CALM_TAIL, FLAT_VOL)
        self.last = self.panel.index[-1].date()
        self.in_win = {"date": (self.last - timedelta(days=2)).isoformat(),
                       "title": "SYN-CALM 公司2026年第三季度业绩预告"}
        self.nonqual = {"date": (self.last - timedelta(days=2)).isoformat(),
                        "title": "关于召开2026年半年度业绩说明会的公告"}
        self.out_win = {"date": (self.last - timedelta(days=30)).isoformat(),
                        "title": "SYN-CALM 公司2026年半年度业绩预告"}

    def test_hit_triggers_and_generic_suppressed(self):
        flags = early_review_flags(self.panel, "SYN-CALM", announcements=[self.in_win])
        hit = [f for f in flags if "业绩预告" in f]
        self.assertEqual(len(hit), 1)
        self.assertIn("提前复核", hit[0])
        self.assertIn(self.in_win["date"], hit[0])
        self.assertNotIn(GENERIC, flags)        # 三条件均可判定 → 通用提示语收敛

    def test_nonqualifying_no_flags(self):
        flags = early_review_flags(self.panel, "SYN-CALM", announcements=[self.nonqual])
        self.assertEqual(flags, [])             # 无任何触发事项

    def test_out_of_window_no_flags(self):
        flags = early_review_flags(self.panel, "SYN-CALM", announcements=[self.out_win])
        self.assertEqual(flags, [])

    def test_auto_with_synthetic_code_degrades_to_generic(self):
        # 合成代码（非6位数字）不发起网络 → 数据不可得 → 通用提示语
        flags = early_review_flags(self.panel, "SYN-CALM")    # announcements="auto"
        self.assertEqual(flags, [GENERIC])

    def test_announcements_none_keeps_generic(self):
        flags = early_review_flags(self.panel, "SYN-CALM", announcements=None)
        self.assertEqual(flags, [GENERIC])


class TestCondition1Regression(unittest.TestCase):
    def test_week_change_still_triggers(self):
        flags = early_review_flags(mk_panel(TRIG_TAIL, FLAT_VOL), "SYN-TRIG")
        hit = [f for f in flags if "建议在复核日之前提前复核" in f]
        self.assertEqual(len(hit), 1)
        self.assertIn("≥5%", hit[0])
        self.assertIn("+7.0%", hit[0])
        self.assertIn(GENERIC, flags)

    def test_calm_no_week_trigger(self):
        flags = early_review_flags(mk_panel(CALM_TAIL, FLAT_VOL), "SYN-CALM")
        self.assertFalse(any("≥5%" in f for f in flags))


class TestFetchInvalidCode(unittest.TestCase):
    def test_invalid_code_returns_none_without_network(self):
        self.assertIsNone(AS.fetch_announcements("SYN-TRIG"))
        self.assertIsNone(AS.fetch_announcements(""))
        self.assertIsNone(AS.fetch_announcements(None))


if __name__ == "__main__":
    unittest.main(verbosity=2)
