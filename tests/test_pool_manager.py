# -*- coding: utf-8 -*-
"""T-03 池管理用例集：
DoD-1 四池名单落快照可离线恢复；DoD-2 归属判定正确（603799→沪深300，每池抽1只）；
DoD-3 池外拒绝（北交所/非成分微盘股 → "暂不支持+支持范围"，BR-009/AC-008）。
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from src.pool_manager import (SUPPORTED_RANGE_TEXT, fetch_pool_lists, load_snapshot,
                               pool_members, resolve_pool, snapshot_info)


class TestPoolManager(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.pools = fetch_pool_lists()          # 当日快照已存在则 0 外网

    # ---- DoD-1：名单落快照、可离线恢复 ------------------------------------
    def test_snapshot_persisted_and_offline_load(self):
        info = snapshot_info()
        self.assertIsNotNone(info)
        self.assertEqual(len(info["sizes"]), 4)                     # 四池齐全
        self.assertGreater(info["sizes"]["沪深300"], 250)
        self.assertGreater(info["sizes"]["中证500"], 450)
        self.assertGreater(info["sizes"]["中证1000"], 950)
        self.assertGreater(info["sizes"]["中证2000"], 1800)
        self.assertEqual(info["snapshot_date"][:4], "2026")
        pools2 = load_snapshot()                                    # 离线恢复（读文件）
        self.assertEqual(pools2, self.pools)

    def test_pool_members_api(self):
        codes = pool_members("沪深300")
        self.assertIn("603799", codes)

    # ---- DoD-2：归属判定（AC-003）------------------------------------------
    def test_603799_resolves_hs300(self):
        r = resolve_pool("603799", self.pools)
        self.assertTrue(r["supported"])
        self.assertEqual(r["pool"], "沪深300")
        self.assertEqual(r["reason"], "ok")

    def test_one_member_each_pool(self):
        for name, codes in self.pools.items():
            r = resolve_pool(codes[0], self.pools)
            self.assertTrue(r["supported"], f"{name} 抽样 {codes[0]} 判定失败: {r}")
            self.assertEqual(r["pool"], name)

    def test_priority_higher_pool_wins(self):
        """同代码若出现在多池名单（理论不应发生），高级别池优先。"""
        merged = {k: list(v) for k, v in self.pools.items()}
        merged["中证500"].append("603799")          # 人为注入冲突
        r = resolve_pool("603799", merged)
        self.assertEqual(r["pool"], "沪深300")

    # ---- DoD-3：池外拒绝（AC-008/BR-009）------------------------------------
    def test_bse_code_rejected(self):
        all_codes = set().union(*self.pools.values())
        bse_candidates = [f"{n:06d}" for n in (833171, 830001, 871981, 430047, 832000, 889999)]
        code = next(c for c in bse_candidates if c not in all_codes)
        r = resolve_pool(code, self.pools)
        self.assertFalse(r["supported"])
        self.assertEqual(r["reason"], "out_of_pool")
        self.assertIn("暂不支持", r["message"])
        self.assertIn(SUPPORTED_RANGE_TEXT.split("、")[0], r["message"])   # 提示支持范围
        self.assertIn("沪深300", r["message"])

    def test_nonmember_microcap_rejected(self):
        all_codes = set().union(*self.pools.values())
        code = next(f"{600000 + i:06d}" for i in range(6000)
                    if f"{600000 + i:06d}" not in all_codes)   # 60 段非成分股
        r = resolve_pool(code, self.pools)
        self.assertFalse(r["supported"])
        self.assertEqual(r["reason"], "out_of_pool")
        self.assertIn("当前支持", r["message"])

    def test_invalid_format(self):
        for bad in ["", "12345", "abcdef", "1234567", "603799x", "  "]:
            r = resolve_pool(bad, self.pools)
            self.assertFalse(r["supported"])
            self.assertEqual(r["reason"], "invalid_format")
            self.assertIn("6 位数字", r["message"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
