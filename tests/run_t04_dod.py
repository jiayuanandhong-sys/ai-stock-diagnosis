# -*- coding: utf-8 -*-
"""T-04 DoD-3 联网验收：H 正式口径实跑（52 完成周逐周回算）+ 与 Spike-2 简化口径差异书面说明。

正式口径（BR-006）：H = 当前 S 相对此前 52 个完成周 S 值的百分位位置，
其中每周 S 均由 18 项因子（附表 A）在同池内完整两级排名得到。
Spike-2 简化口径（2026-10-17，已废弃）：仅用 3 因子近似周频序列。
证据落 evidence/T-04_H正式口径_实跑记录_20261017.txt / .json。
"""

import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, ".."))

import src.data_fetcher as dfm
from src.factor_engine import compute_H, compute_pool_scores

START, END = "20240901", "20261017"
TARGET = "603799"

POOL_DEMO = [
    ("603799", "华友钴业"), ("600519", "贵州茅台"), ("601318", "中国平安"), ("600036", "招商银行"),
    ("000858", "五粮液"),   ("300750", "宁德时代"), ("002594", "比亚迪"),   ("600900", "长江电力"),
    ("601899", "紫金矿业"), ("000333", "美的集团"), ("600276", "恒瑞医药"), ("002415", "海康威视"),
    ("600887", "伊利股份"), ("601166", "兴业银行"), ("600028", "中国石化"), ("601398", "工商银行"),
    ("300059", "东方财富"), ("601668", "中国建筑"), ("601012", "隆基绿能"), ("600030", "中信证券"),
]

out = {"steps": {}}


def step(name, passed, detail):
    out["steps"][name] = {"pass": bool(passed), "detail": detail}
    print(f"[{'PASS' if passed else 'FAIL'}] {name} —— {detail}", flush=True)


# 1. 数据（T-02 层，优先命中当日缓存）
panels, failed = dfm.fetch_many([c for c, _ in POOL_DEMO], START, END, use_cache=True)
step("数据就绪（T-02 缓存/拉取）", len(panels) == 20 and not failed,
     f"{len(panels)}/20；失败: {failed if failed else '无'}；外网 {dfm.STATS.network_calls} 次")

# 2. 当前截面 S（正式口径全 18 因子两级排名）
scores, S_raw, S = compute_pool_scores(panels)
s_now = float(S[TARGET])
step("当前截面 S（18 因子两级排名）", s_now > 0,
     f"603799: S={s_now:.1f}（Spike-2 简化口径当时为 10.0），同池排名 "
     f"{int(S.rank(ascending=False)[TARGET])}/20；因子有效 "
     f"{int(scores.loc[TARGET].notna().sum())}/18")

# 3. H 正式口径实跑：52 完成周逐周回算
print("逐周回算中（52 个周截面 × 20 只 × 18 因子）...", flush=True)
H, series, _s_now = compute_H(panels, TARGET, weeks=52)
step("H 正式口径实跑（52 完成周）", H == H and 0 <= H <= 100,
     f"周截面数 {len(series)}；603799 当前 S={s_now:.1f}；正式口径 H={H:.1f}；"
     f"历史 S 分位（min/median/max）= {series.min():.1f}/{series.median():.1f}/{series.max():.1f}")

# 4. 与 Spike-2 简化口径的差异书面说明（DoD-3 要求）
diff_note = (
    "【正式口径 vs Spike-2 简化口径差异说明】\n"
    "1) 简化口径（Spike-2，2026-10-17，已废弃）：每周 S 仅由 3 项价格因子近似回算，"
    "未使用 18 因子全集、未做第二级排名回算，H=20 为演示性质。\n"
    "2) 正式口径（本次实跑）：每个完成周截面用截至该周的完整面板重算 18 项因子"
    "（附表 A）→ 同池平均并列名次百分位 → 等权合成 → 放回同池两级排名得 S_t；"
    f"H = 当前 S 在此前 52 个完成周 S 序列中的相对位置 = {H:.1f}。\n"
    "3) 为何正式口径更准：①因子覆盖 18/18（估值/价格/交易/风险四侧面），简化口径仅价格单侧面，"
    "无法反映估值与风险维度的历史位置；②S_t 每周都经同池两级排名，与当前 S 同一构造，"
    "历史与当下可比；简化口径的历史 S 与当前 S 构造不同，位置比较存在口径错位。\n"
    "4) 演示池声明：本次实跑基于 20 只演示同池（含 603799），机制与口径已验证；"
    "M2 全池（沪深300 真实名单）环境下 H 数值会随池构成变化，届时以全池口径为准。"
)
step("差异书面说明", True, diff_note.replace("\n", " | "))

all_pass = all(v["pass"] for v in out["steps"].values())
out["overall"] = "ALL PASS" if all_pass else "HAS FAIL"
out["H_formal"] = H
out["S_now"] = s_now
out["diff_note"] = diff_note
out["S_series_tail"] = {str(k.date()): round(v, 2) for k, v in series.tail(8).items()}

with open(os.path.join(HERE, "..", "evidence", "T-04_H正式口径_实跑记录_20261017.json"),
          "w", encoding="utf-8") as f:
    json.dump(out, f, ensure_ascii=False, indent=2)
print("\n=== T-04 DoD-3 验收:", out["overall"], "===")
