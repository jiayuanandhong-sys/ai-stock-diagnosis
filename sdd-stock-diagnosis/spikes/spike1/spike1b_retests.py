# -*- coding: utf-8 -*-
"""Spike-1b: 修正版专项重测
1. 估值接口（akshare 1.18.64 中 stock_a_indicator_lg 已更名 → 改用 stock_value_em）
2. pytdx 分钟线翻页深度（修正: 从 offset=0 起探, 分 1min/5min 两档）
3. pytdx 历史分笔（修正: 用 get_history_transaction_data 按日查）
4. 东财 1min 重试（判定是瞬时网络还是持续不可达）
"""
import time
import akshare as ak

print("=" * 72)
print("SPIKE-1b 修正专项重测")
print("=" * 72)

# ---------- 1) 估值（stock_value_em）----------
print("\n---- 1) 日线估值（乐咕 stock_value_em）----")
for code in ["603799", "000011"]:
    try:
        t0 = time.time()
        df = ak.stock_value_em(symbol=code)
        first = str(df.iloc[0, 0])[:10]
        last = str(df.iloc[-1, 0])[:10]
        cols = list(df.columns)
        print(f"[PASS] 估值 {code}: rows={len(df)}, first={first}, last={last}, cols={cols[:6]}"
              f" ({time.time()-t0:.1f}s)")
    except Exception as e:
        print(f"[FAIL] 估值 {code}: {str(e)[:140]}")

# ---------- 2) pytdx 分钟线深度（修正起点）----------
print("\n---- 2) pytdx 分钟线深度（1min=cat7, 5min=cat0）----")
from pytdx.hq import TdxHq_API
api = TdxHq_API()
hosts = [("115.238.90.165", 7709), ("119.147.212.81", 7709), ("114.80.63.12", 7709)]
conn = None
for h in hosts:
    try:
        if api.connect(*h, time_out=8):
            conn = h
            print(f"[PASS] 连接 {h}")
            break
    except Exception:
        continue
if conn:
    tests = [("603799", 1, "华友钴业/沪"), ("000011", 0, "中证2000样本/深")]
    for code, mkt, tag in tests:
        for cat, cat_name in [(7, "1min"), (0, "5min")]:
            depth_found = None
            for depth in [0, 240, 800, 2400, 8000, 20000, 40000, 80000]:
                t0 = time.time()
                bars = api.get_security_bars(cat, mkt, code, depth, 10)
                got = len(bars) if bars else 0
                d = bars[-1]["datetime"][:16] if bars else "N/A"
                print(f"[{'PASS' if got else 'FAIL'}] {tag} {code} {cat_name} offset={depth}: "
                      f"got={got}, earliest={d} ({time.time()-t0:.1f}s)")
                if not got:
                    break
                depth_found = d
    # ---------- 3) 历史分笔（按日）----------
    print("\n---- 3) pytdx 历史分笔 get_history_transaction_data ----")
    for date in [20261016, 20261015, 20261013, 20260918, 20260420]:
        t0 = time.time()
        try:
            ticks = api.get_history_transaction_data(1, "603799", date)
            got = len(ticks) if ticks else 0
            print(f"[{'PASS' if got else 'FAIL'}] 历史分笔 603799 @ {date}: got={got}"
                  f" ({time.time()-t0:.1f}s)")
        except Exception as e:
            print(f"[FAIL] 历史分笔 603799 @ {date}: {str(e)[:100]}")
    api.disconnect()
else:
    print("[FAIL] pytdx 服务器全部连接失败")

# ---------- 4) 东财 1min 重试 ----------
print("\n---- 4) 东财 1min 重试（判定瞬时/持续）----")
try:
    t0 = time.time()
    df = ak.stock_zh_a_hist_min_em(symbol="603799", period="1", adjust="")
    print(f"[PASS] 东财1min 603799: rows={len(df)}, "
          f"first={df.iloc[0,0]}, last={df.iloc[-1,0]} ({time.time()-t0:.1f}s)")
except Exception as e:
    print(f"[FAIL] 东财1min 603799 重试仍失败: {str(e)[:140]}")
