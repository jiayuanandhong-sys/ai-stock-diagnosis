# -*- coding: utf-8 -*-
"""Spike-1c: pytdx 多服务器交叉验证 + 本地通达信 vipdoc 数据文件扫描"""
import os
from pytdx.hq import TdxHq_API

HOSTS = [
    ("119.147.212.81", 7709), ("114.80.63.12", 7709), ("115.238.90.165", 7709),
    ("218.108.98.244", 7709), ("218.75.126.9", 7709), ("123.125.108.14", 7709),
    ("123.125.108.59", 7709), ("119.147.164.60", 7709), ("122.224.2.119", 7709),
]

print("---- A) pytdx 多服务器交叉验证（603799, 沪市 market=1）----")
working = None
for h in HOSTS:
    api = TdxHq_API()
    try:
        if not api.connect(*h, time_out=5):
            print(f"[----] {h} 连接失败")
            continue
        day = api.get_security_bars(4, 1, "603799", 0, 10)      # 日K
        m1 = api.get_security_bars(7, 1, "603799", 0, 10)       # 1分钟K
        m5 = api.get_security_bars(0, 1, "603799", 0, 10)       # 5分钟K
        tick = api.get_transaction_data(1, "603799", 0, 20)     # 当日分笔
        d_ok, m1_ok, m5_ok, t_ok = (len(x) if x else 0 for x in (day, m1, m5, tick))
        print(f"[{'PASS' if d_ok else 'FAIL'}] {h} 日K={d_ok} 1min={m1_ok} "
              f"5min={m5_ok} 当日分笔={t_ok}")
        if d_ok:
            working = (h, api)
            break
    except Exception as e:
        print(f"[----] {h} 异常: {str(e)[:60]}")
    finally:
        try:
            api.disconnect()
        except Exception:
            pass

if working:
    h, api = working
    print(f"\n使用 {h} 深挖:")
    for cat, name in [(7, "1min"), (0, "5min")]:
        for depth in [0, 400, 800, 2400, 4800, 9600, 19200, 38400, 60000]:
            bars = api.get_security_bars(cat, 1, "603799", depth, 10)
            got = len(bars) if bars else 0
            d = bars[-1]["datetime"][:16] if bars else "N/A"
            print(f"  {name} offset={depth}: got={got}, earliest={d}")
            if not got:
                break
    for date in [20261016, 20261015, 20260918, 20260420, 20251016]:
        try:
            ticks = api.get_history_transaction_data(1, "603799", date)
            print(f"  历史分笔@{date}: got={len(ticks) if ticks else 0}")
        except Exception as e:
            print(f"  历史分笔@{date}: 异常 {str(e)[:60]}")
    api.disconnect()
else:
    print("\n[FAIL] 无任何服务器返回日K数据（网络受限或协议不可用）")

print("\n---- B) 本地通达信 vipdoc 数据文件扫描 ----")
CANDIDATE_ROOTS = [
    "C:/", "D:/", "E:/", "F:/", "H:/",
]
vipdocs = []
for root in CANDIDATE_ROOTS:
    try:
        entries = os.listdir(root)
    except Exception:
        continue
    for e in entries:
        p = os.path.join(root, e)
        if os.path.isdir(p) and "vipdoc" in p.lower():
            vipdocs.append(p)
        # 常见通达信安装目录名
        elif os.path.isdir(p) and any(k in e.lower() for k in ["tdx", "tongda", "new_tdx", "zd_zsone", "zxhsztd"]):
            vip = os.path.join(p, "vipdoc")
            if os.path.isdir(vip):
                vipdocs.append(vip)

# 也直接探测常见安装路径
for guess in ["C:/new_tdx/vipdoc", "C:/zd_zsone/vipdoc", "D:/new_tdx/vipdoc",
              "C:/Program Files/new_tdx/vipdoc", "C:/T0002/vipdoc",
              "C:/通达信/vipdoc", "D:/通达信/vipdoc"]:
    if os.path.isdir(guess) and guess not in vipdocs:
        vipdocs.append(guess)

print(f"发现 vipdoc 目录: {vipdocs if vipdocs else '无'}")
for v in vipdocs[:2]:
    for sub in ["sh/lday", "sh/minline", "sh/fzline", "sz/lday", "sz/minline", "sz/fzline"]:
        d = os.path.join(v, sub)
        if os.path.isdir(d):
            files = os.listdir(d)
            exts = {}
            for f in files:
                e = os.path.splitext(f)[1].lower()
                exts[e] = exts.get(e, 0) + 1
            sample = [f for f in files if "603799" in f][:3]
            print(f"  {d}: files={len(files)}, exts={exts}, 华友钴业样本={sample}")
            for s in sample:
                fp = os.path.join(d, s)
                print(f"    {s}: {os.path.getsize(fp)/1024:.0f} KB")
