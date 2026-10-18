# -*- coding: utf-8 -*-
"""Spike-1: 数据可行性实测（免费渠道：akshare 开源接口 + pytdx 通达信协议）

测试目标（BR-008 风险验证）:
  1. 四池成分股名单可得性（沪深300/中证500/中证1000/中证2000）
  2. 日线行情：小盘股覆盖与历史深度（要求 >= 52 周）
  3. 日线估值（PE/PB）：覆盖与深度
  4. 1 分钟线：历史深度（样例口径需要 52 周 ~= 60000 根 1 分钟K）
  5. 逐笔/分笔成交：历史可得天数
输出: 每项 PASS/FAIL + 实测数值, 供回填 plan.md Spike-1 结论
"""
import time
import json

import akshare as ak

RESULTS = []


def timed(name, fn, *args, **kwargs):
    t0 = time.time()
    try:
        out = fn(*args, **kwargs)
        dt = time.time() - t0
        RESULTS.append({"test": name, "ok": True, "secs": round(dt, 1), "note": out})
        print(f"[PASS] {name} ({dt:.1f}s) {out}")
        return True
    except Exception as e:
        dt = time.time() - t0
        RESULTS.append({"test": name, "ok": False, "secs": round(dt, 1), "note": str(e)[:200]})
        print(f"[FAIL] {name} ({dt:.1f}s) {str(e)[:200]}")
        return False


def fmt_df_range(df, date_col):
    c = df.columns[0] if date_col is None else date_col
    first, last = str(df.iloc[0][c])[:19], str(df.iloc[-1][c])[:19]
    return f"rows={len(df)}, first={first}, last={last}"


def main():
    print("=" * 72)
    print("SPIKE-1 数据可行性实测 — 免费渠道（akshare + pytdx）")
    print("=" * 72)

    # ---------- 1) 四池成分股名单 ----------
    pools = {}
    for name, code in [("沪深300", "000300"), ("中证500", "000905"),
                       ("中证1000", "000852"), ("中证2000", "932000")]:
        df = None
        try:
            df = ak.index_stock_cons_csindex(code)
        except Exception:
            try:
                df = ak.index_stock_cons(symbol=code)
            except Exception as e:
                print(f"[FAIL] 成分股名单 {name}({code}): {str(e)[:120]}")
                continue
        codes = [str(c).zfill(6) for c in df["成分券代码"]] if "成分券代码" in df.columns else [str(c).zfill(6) for c in df.iloc[:, 1]]
        pools[name] = codes
        print(f"[PASS] 成分股名单 {name}({code}): count={len(codes)}, sample={codes[:3]}")

    if not pools:
        print("[ABORT] 四池名单全部获取失败，后续测试无样本")
        return

    # ---------- 样本股 ----------
    sample = {
        "沪深300": "603799",  # 华友钴业（样例报告原股）
    }
    for pool in ["中证500", "中证1000", "中证2000"]:
        if pool in pools and pools[pool]:
            sample[pool] = pools[pool][0]
    print(f"\n样本股: {sample}\n")

    # ---------- 2) 日线行情 ----------
    print("---- 2) 日线行情（东财, 前复权, 2025-09 至今）----")
    for pool, code in sample.items():
        try:
            t0 = time.time()
            df = ak.stock_zh_a_hist(symbol=code, period="daily",
                                    start_date="20250901", adjust="qfq")
            print(f"[PASS] 日线 {pool} {code}: {fmt_df_range(df, '日期')}"
                  f" ({time.time()-t0:.1f}s)")
        except Exception as e:
            print(f"[FAIL] 日线 {pool} {code}: {str(e)[:120]}")

    # ---------- 3) 日线估值（PE/PB, 乐咕）----------
    print("\n---- 3) 日线估值（乐咕 stock_a_indicator_lg）----")
    for pool, code in list(sample.items())[:2] + [("中证2000", sample.get("中证2000"))]:
        if not code:
            continue
        try:
            t0 = time.time()
            df = ak.stock_a_indicator_lg(symbol=code)
            print(f"[PASS] 估值 {pool} {code}: {fmt_df_range(df, 'trade_date')}"
                  f" ({time.time()-t0:.1f}s)")
        except Exception as e:
            print(f"[FAIL] 估值 {pool} {code}: {str(e)[:120]}")

    # ---------- 4) 1 分钟线 ----------
    print("\n---- 4) 1 分钟线历史深度 ----")
    # 4a) 东财 1 分钟（预期仅近几个交易日）
    for pool, code in list(sample.items()):
        try:
            t0 = time.time()
            df = ak.stock_zh_a_hist_min_em(symbol=code, period="1", adjust="")
            print(f"[PASS] 东财1min {pool} {code}: {fmt_df_range(df, '时间')}"
                  f" ({time.time()-t0:.1f}s)")
        except Exception as e:
            print(f"[FAIL] 东财1min {pool} {code}: {str(e)[:120]}")
    # 4b) 新浪 1 分钟（预期 ~ 近期数日, 最多 ~1920 根）
    for pool, code in list(sample.items())[:2]:
        sh_code = ("sh" if code.startswith(("6", "9", "5")) else "sz") + code
        try:
            t0 = time.time()
            df = ak.stock_zh_a_minute(symbol=sh_code, period="1", adjust="qfq")
            print(f"[PASS] 新浪1min {pool} {sh_code}: {fmt_df_range(df, 'day')}"
                  f" ({time.time()-t0:.1f}s)")
        except Exception as e:
            print(f"[FAIL] 新浪1min {pool} {sh_code}: {str(e)[:120]}")
    # 4c) pytdx 通达信服务器 1 分钟线翻页深度（目标 52 周 ~ 60000 根）
    print("\n---- 4c) pytdx 1 分钟线翻页深度（通达信免费服务器）----")
    try:
        from pytdx.hq import TdxHq_API
        api = TdxHq_API()
        hosts = [("119.147.212.81", 7709), ("114.80.63.12", 7709), ("115.238.90.165", 7709)]
        connected = False
        for h in hosts:
            try:
                if api.connect(*h, time_out=8):
                    connected = True
                    print(f"[PASS] pytdx 连接服务器 {h}")
                    break
            except Exception:
                continue
        if connected:
            code = sample.get("中证2000") or sample.get("沪深300")
            market = 1 if code.startswith(("6", "9", "5")) else 0
            # 二分探测翻页深度: start 偏移越深越接近历史
            for depth in [240, 2400, 12000, 30000, 60000]:
                t0 = time.time()
                bars = api.get_security_bars(7, market, code, depth, 10)  # 7 = 1分钟K
                got = len(bars) if bars else 0
                d = str(bars[-1]["datetime"])[:16] if bars else "N/A"
                print(f"[{'PASS' if got else 'FAIL'}] 1min翻页 offset={depth}: "
                      f"got={got}, earliest={d} ({time.time()-t0:.1f}s)")
                if not got:
                    break
            # 4d) 分笔成交历史深度
            print("\n---- 4d) pytdx 分笔成交历史深度 ----")
            for back in [0, 3600, 20000, 60000]:
                t0 = time.time()
                ticks = api.get_transaction_data(market, code, back, 10)
                got = len(ticks) if ticks else 0
                d = str(ticks[-1]["time"])[:8] if ticks else "N/A"
                print(f"[{'PASS' if got else 'FAIL'}] 分笔翻页 offset={back}: "
                      f"got={got}, earliest_time={d} ({time.time()-t0:.1f}s)")
                if not got:
                    break
            api.disconnect()
        else:
            print("[FAIL] pytdx 所有服务器连接失败")
    except Exception as e:
        print(f"[FAIL] pytdx: {str(e)[:150]}")

    print("\n" + "=" * 72)
    print("SPIKE-1 实测结束。结论请对照: 样例口径 = 52周 1分钟线 + 逐笔历史")
    print(json.dumps(RESULTS, ensure_ascii=False, indent=1)[:1500])


if __name__ == "__main__":
    main()
