# -*- coding: utf-8 -*-
"""T-02 联网验收（DoD-1 / DoD-3 / DoD-4）——证据落 evidence/。

DoD-1 样本 20 只拉取 20/20 成功（Spike-2 同款 20 只 + 中证 2000 小盘股样本）；
DoD-3 二次运行命中缓存 0 外网（真实请求计数验证）；
DoD-4 相同输入下因子原始值与 Spike-2 结果一致（603799 锚点：S_raw≈33.6, S≈10）。
"""

import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, ".."))              # 工作区根（import src.*）

import pandas as pd
import src.data_fetcher as dfm

START, END = "20240901", "20261017"
TARGET = "603799"

POOL_DEMO = [                 # Spike-2 同款演示池（spike2_render_pipeline.py L49-55）
    ("603799", "华友钴业"), ("600519", "贵州茅台"), ("601318", "中国平安"), ("600036", "招商银行"),
    ("000858", "五粮液"),   ("300750", "宁德时代"), ("002594", "比亚迪"),   ("600900", "长江电力"),
    ("601899", "紫金矿业"), ("000333", "美的集团"), ("600276", "恒瑞医药"), ("002415", "海康威视"),
    ("600887", "伊利股份"), ("601166", "兴业银行"), ("600028", "中国石化"), ("601398", "工商银行"),
    ("300059", "东方财富"), ("601668", "中国建筑"), ("601012", "隆基绿能"), ("600030", "中信证券"),
]

results = {"steps": {}}


def step(name, passed, detail):
    results["steps"][name] = {"pass": bool(passed), "detail": detail}
    print(f"[{'PASS' if passed else 'FAIL'}] {name} —— {detail}", flush=True)


# ---- 0. 中证 2000 小盘股样本（DoD-1 要求覆盖小盘股） --------------------------
def pick_csi2000_samples(n=3):
    codes = []
    try:
        df = __import__("akshare").index_stock_cons_csindex("932000")
        codes = [str(c).zfill(6) for c in df["成分券代码"].tolist()][:n]
    except Exception as e:
        print(f"  (中证官网名单失败: {e!r}，改用备源)")
    if not codes:
        try:
            df = __import__("akshare").index_stock_cons(symbol="932000")
            codes = [str(c).zfill(6) for c in df["品种代码"].tolist()][:n]
        except Exception as e:
            print(f"  (备源名单也失败: {e!r})")
    return codes


csi2000_codes = pick_csi2000_samples(3)
step("中证2000名单取样", len(csi2000_codes) == 3, f"样本: {csi2000_codes}")

all_codes = [c for c, _ in POOL_DEMO] + csi2000_codes

# ---- 1. DoD-1：批量拉取 20/20（Spike-2 20 只 + 中证2000 小盘 3 只） ----------
dfm.reset_stats()
panels, failed = dfm.fetch_many(all_codes, START, END, use_cache=True)
net1 = dfm.STATS.network_calls
step("DoD-1 批量拉取", len(panels) >= 20 and not failed,
     f"成功 {len(panels)}/{len(all_codes)}；外网请求 {net1} 次；失败: {failed if failed else '无'}；"
     f"来源 {dfm.STATS.source_used}；备援事件 {len(dfm.STATS.fallback_events)} 起")
small_ok = all(c in panels for c in csi2000_codes) if csi2000_codes else False
step("DoD-1 中证2000小盘股", small_ok,
     f"小盘样本 {[(c, len(panels[c])) for c in csi2000_codes]}")

# ---- 2. DoD-3：二次运行命中缓存（0 外网请求） --------------------------------
dfm.reset_stats()
panels2, failed2 = dfm.fetch_many(all_codes, START, END, use_cache=True)
step("DoD-3 缓存命中零外网", dfm.STATS.network_calls == 0 and len(panels2) == len(panels),
     f"二次外网请求 {dfm.STATS.network_calls} 次；缓存命中 {dfm.STATS.cache_hits}/{len(all_codes)}")

# ---- 3. DoD-4：复算与 Spike-2 一致（603799 锚点 S_raw≈33.6 / S≈10） ----------
def raw_factors(df):                       # spike2_render_pipeline.py L150-173 逐行同款
    c = df["close"]; v = df["volume"]
    r = {}
    r["pe_pct"]  = df["pe_ttm"].iloc[-1]
    r["pb_pct"]  = df["pb"].iloc[-1]
    r["mv_pct"]  = df["total_mv"].iloc[-1]
    r["ret20"]   = c.iloc[-1] / c.iloc[-21] - 1
    r["ret60"]   = c.iloc[-1] / c.iloc[-61] - 1
    r["ma250_dist"] = c.iloc[-1] / c.rolling(250).mean().iloc[-1]
    r["ma_ratio"]   = c.rolling(20).mean().iloc[-1] / c.rolling(60).mean().iloc[-1]
    w = c.iloc[-252:]
    r["pos52w"]  = (w.iloc[-1] - w.min()) / (w.max() - w.min() + 1e-12)
    r["accel"]   = (c.iloc[-1] / c.iloc[-6] - 1) - r["ret20"]
    r["updays"]  = float((c.diff().iloc[-20:] > 0).mean())
    r["amt20"]   = df["amount"].iloc[-20:].mean()
    r["volx"]    = v.iloc[-5:].mean() / (v.iloc[-20:].mean() + 1e-12)
    r["vp_corr"] = c.iloc[-20:].pct_change().corr(v.pct_change().iloc[-20:])
    r["turn20"]  = df["turnover"].iloc[-20:].mean()
    r["vol20"]   = c.pct_change().iloc[-20:].std()
    r["vol60"]   = c.pct_change().iloc[-60:].std()
    w60 = c.iloc[-60:]; cummax = w60.cummax()
    r["mdd60"]   = float(1 - (w60 / cummax).min())
    r["amp20"]   = df["amp"].iloc[-20:].mean()
    return r

FACTOR_TABLE = [               # spike2 同款：(F#, 侧面, key, 是否低值有利)
    ("F1", "估值特征", "pe_pct", True), ("F2", "估值特征", "pb_pct", True), ("F3", "估值特征", "mv_pct", True),
    ("F4", "价格行为", "ret20", False), ("F5", "价格行为", "ret60", False), ("F6", "价格行为", "ma250_dist", False),
    ("F7", "价格行为", "ma_ratio", False), ("F8", "价格行为", "pos52w", False), ("F9", "价格行为", "accel", False),
    ("F10", "价格行为", "updays", False), ("F11", "交易行为", "amt20", False), ("F12", "交易行为", "volx", False),
    ("F13", "交易行为", "vp_corr", False), ("F14", "交易行为", "turn20", False), ("F15", "风险特征", "vol20", True),
    ("F16", "风险特征", "vol60", True), ("F17", "风险特征", "mdd60", True), ("F18", "风险特征", "amp20", True),
]

demo_only = [c for c, _ in POOL_DEMO]            # 复算仅用 Spike-2 同款 20 只（同池口径）
raws = pd.DataFrame({code: raw_factors(panels[code]) for code in demo_only}).T
scores = pd.DataFrame(index=raws.index, columns=[f[0] for f in FACTOR_TABLE], dtype=float)
for fid, _side, key, low_good in FACTOR_TABLE:
    s = raws[key].rank(method="average", pct=True) * 100
    scores[fid] = (100 - s) if low_good else s
scores = scores.astype(float)
S_raw = scores.mean(axis=1, skipna=True)
S_score = S_raw.rank(method="average", pct=True) * 100

s_raw_t, s_score_t = float(S_raw[TARGET]), float(S_score[TARGET])
step("DoD-4 复算与Spike-2一致", abs(s_raw_t - 33.6) < 0.5 and abs(s_score_t - 10.0) < 0.5,
     f"603799: S_raw={s_raw_t:.2f}（Spike-2=33.60）, S={s_score_t:.2f}（Spike-2=10.0），"
     f"同池排名 {int(S_raw.rank(ascending=False)[TARGET])}/20；矩阵 {raws.shape}，有效样本 "
     f"{int(raws.notna().sum().min())}–{int(raws.notna().sum().max())}")

all_pass = all(v["pass"] for v in results["steps"].values())
results["overall"] = "ALL PASS" if all_pass else "HAS FAIL"
out_path = os.path.join(HERE, "..", "evidence", "T-02_数据层_验收记录_20261017.json")
with open(out_path, "w", encoding="utf-8") as f:
    json.dump(results, f, ensure_ascii=False, indent=2)
print("\n=== T-02 验收:", results["overall"], "===")
