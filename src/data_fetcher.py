# -*- coding: utf-8 -*-
"""T-02 数据层：多源日线 + 估值拉取与 parquet 缓存（tasks.md v1.0）。

设计依据：
- ADR-002（修订版）：免费开源渠道，多源备援——新浪 `stock_zh_a_daily` 主源（自带换手率/
  成交额，Spike-2 实测 20/20）+ 东财 `stock_zh_a_hist` 备援；估值走百度股市通
  `stock_zh_valuation_baidu`（总市值 / 市盈率(TTM) / 市净率，截面最新值）。
- BR-004/NG-006：仅日线级公开行情与估值数据，无任何用户数据。
- SKILL.md 环境备忘：本机代理对数据源域名不可达 → 导入即设 NO_PROXY 并清空代理变量；
  akshare 锁定 1.18.64；接口漂移常态 → 每源独立 try、失败重试、全链路不静默崩溃。

接口契约：
- fetch_daily(code, start, end, use_cache)   -> (df, meta)  标准化日线（8 列 float）
- fetch_valuation_snapshot(code, retries)    -> dict{pe_ttm, pb, total_mv}
- fetch_panel(code, start, end, use_cache)   -> DataFrame   日线+估值合并面板（Spike-2 同构，
                                                  含 pe_ttm/pb/total_mv/amp/turnover/code/source/fetch_date）
- 缓存：data_cache/panel_{code}_{start}_{end}.parquet，当日有效；二次请求 0 外网（计数可验证）
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass, field
from datetime import datetime as _dt
from typing import Callable, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

# ---- 本机环境备忘（SKILL.md §4）：绕过系统代理，直连数据源 ------------------
os.environ.setdefault("NO_PROXY", "*")
for _k in ("http_proxy", "https_proxy", "HTTP_PROXY", "HTTPS_PROXY"):
    os.environ.pop(_k, None)

import akshare as ak  # noqa: E402  (须在代理清理后导入)

DEFAULT_START = "20240901"          # ≥52 周 + MA250 余量（对齐 Spike-2 口径）
DEFAULT_END = _dt.now().strftime("%Y%m%d")

CACHE_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                         "data_cache")
os.makedirs(CACHE_DIR, exist_ok=True)

SOURCE_ORDER: List[str] = ["sina", "eastmoney"]   # 新浪主源 → 东财备援（tasks.md T-02 产出定义）
DAILY_COLS = ["open", "high", "low", "close", "volume", "amount", "amp", "turnover"]


# ---- 请求统计（DoD-3：缓存命中验证用） ---------------------------------------

@dataclass
class FetchStats:
    network_calls: int = 0          # 外网数据请求次数（日线按源计 1 次/股，估值 3 次/股）
    cache_hits: int = 0
    source_used: Dict[str, int] = field(default_factory=dict)
    fallback_events: List[str] = field(default_factory=list)   # 备援切换事件日志


STATS = FetchStats()


def reset_stats() -> None:
    """清零统计（测试用）。"""
    global STATS
    STATS = FetchStats()


# ---- 低层拉取函数（可注入替换，供测试模拟断连） -------------------------------

def _sym_prefix(code: str) -> str:
    """新浪代码前缀：6/9→sh，4/8→bj（北交所），其余→sz。"""
    if code.startswith(("6", "9")):
        return "sh"
    if code.startswith(("4", "8")):
        return "bj"
    return "sz"


def _fetch_daily_sina(code: str, start: str, end: str) -> pd.DataFrame:
    STATS.network_calls += 1
    df = ak.stock_zh_a_daily(symbol=_sym_prefix(code) + code,
                             start_date=start, end_date=end, adjust="qfq")
    if df is None or len(df) == 0:
        raise RuntimeError(f"sina empty for {code}")
    df = df.copy()
    df["date"] = pd.to_datetime(df["date"])
    df = df.set_index("date").sort_index()
    # amp 由 OHLC 补算（新浪无振幅列；Spike-2 同口径）
    df["amp"] = (df["high"] - df["low"]) / df["close"].shift(1) * 100
    out = df[DAILY_COLS].astype(float)
    return out


def _fetch_daily_east(code: str, start: str, end: str) -> pd.DataFrame:
    STATS.network_calls += 1
    df = ak.stock_zh_a_hist(symbol=code, period="daily",
                            start_date=start, end_date=end, adjust="qfq")
    if df is None or len(df) == 0:
        raise RuntimeError(f"eastmoney empty for {code}")
    df = df.rename(columns={"日期": "date", "开盘": "open", "收盘": "close",
                            "最高": "high", "最低": "low", "成交量": "volume",
                            "成交额": "amount", "振幅": "amp", "换手率": "turnover"}).copy()
    df["date"] = pd.to_datetime(df["date"])
    df = df.set_index("date").sort_index()
    out = df[DAILY_COLS].astype(float)
    return out


# 拉取函数注册表：可注入 fake（DoD-2 封禁主源测试）
_FETCHERS: Dict[str, Callable[[str, str, str], pd.DataFrame]] = {
    "sina": _fetch_daily_sina,
    "eastmoney": _fetch_daily_east,
}


def register_fetcher(name: str, fn: Callable[[str, str, str], pd.DataFrame],
                     order: Optional[List[str]] = None) -> None:
    """注册/替换数据源（测试注入点）。order 为 None 时保持现有顺序。"""
    _FETCHERS[name] = fn
    if order is not None:
        SOURCE_ORDER[:] = order


# ---- 估值（百度股市通，截面最新值；Spike-2 同口径） ---------------------------

_BAIDU_INDICATORS = [("总市值", "total_mv"), ("市盈率(TTM)", "pe_ttm"), ("市净率", "pb")]


def fetch_valuation_snapshot(code: str, retries: int = 2) -> Dict[str, float]:
    """估值截面最新值 {total_mv, pe_ttm, pb}；单项失败重试 retries 次后置 NaN（缺失不填分原则）。"""
    out: Dict[str, float] = {}
    for ind, col in _BAIDU_INDICATORS:
        v = float("nan")
        for attempt in range(retries):
            try:
                STATS.network_calls += 1
                bv = ak.stock_zh_valuation_baidu(symbol=code, indicator=ind, period="近一年")
                v = float(bv["value"].dropna().iloc[-1])
                break
            except Exception:
                if attempt < retries - 1:
                    time.sleep(1.5)
        out[col] = v
    return out


# 估值注入点（测试用；生产恒为 fetch_valuation_snapshot）
_VALUATION_FETCHER: Callable[[str], Dict[str, float]] = fetch_valuation_snapshot


def register_valuation_fetcher(fn: Callable[[str], Dict[str, float]]) -> None:
    """替换估值函数（测试注入点；不触碰真实网络）。"""
    global _VALUATION_FETCHER
    _VALUATION_FETCHER = fn


# ---- 缓存 --------------------------------------------------------------------

def _panel_cache_path(code: str, start: str, end: str) -> str:
    return os.path.join(CACHE_DIR, f"panel_{code}_{start}_{end}.parquet")


def _today() -> str:
    return _dt.now().strftime("%Y-%m-%d")


class DataFetchError(Exception):
    """全部数据源失败时抛出。"""


# ---- 对外主接口 ----------------------------------------------------------------

def fetch_daily(code: str, start: str = DEFAULT_START, end: str = DEFAULT_END,
                use_cache: bool = True) -> Tuple[pd.DataFrame, Dict]:
    """标准化日线（8 列 float）。返回 (df, meta)，meta 含 source/from_cache。"""
    panel, meta = fetch_panel(code, start, end, use_cache)
    return panel[DAILY_COLS], meta


def fetch_panel(code: str, start: str = DEFAULT_START, end: str = DEFAULT_END,
                use_cache: bool = True) -> Tuple[pd.DataFrame, Dict]:
    """日线+估值合并面板（Spike-2 同构），返回 (df, meta)。

    meta = {"code", "source", "from_cache", "rows", "val_nan"}；df 列 =
    DAILY_COLS + [pe_ttm, pb, total_mv, code, source, fetch_date]，index 为交易日。
    """
    cache = _panel_cache_path(code, start, end)
    if use_cache and os.path.exists(cache):
        try:
            cached = pd.read_parquet(cache)
            if str(cached["fetch_date"].iloc[0]) == _today():
                STATS.cache_hits += 1
                meta = {"code": code, "source": str(cached["source"].iloc[0]),
                        "from_cache": True, "rows": int(len(cached)),
                        "val_nan": int(cached[["pe_ttm", "pb", "total_mv"]].isna().sum().sum())}
                return cached, meta
        except Exception:
            pass  # 缓存损坏 → 视为未命中，走网络

    last_err: Optional[Exception] = None
    for source in SOURCE_ORDER:
        fetcher = _FETCHERS.get(source)
        if fetcher is None:
            continue
        try:
            df = fetcher(code, start, end)
            if len(df) == 0:
                raise RuntimeError("empty frame")
            STATS.source_used[source] = STATS.source_used.get(source, 0) + 1
            if source != SOURCE_ORDER[0]:
                STATS.fallback_events.append(f"{code}:{SOURCE_ORDER[0]}→{source}")
            val = _VALUATION_FETCHER(code)
            for col, v in val.items():
                df[col] = v
            df["code"] = code
            df["source"] = source
            df["fetch_date"] = _today()
            try:
                df.to_parquet(cache)
            except Exception:
                pass  # 缓存写失败不影响返回
            meta = {"code": code, "source": source, "from_cache": False,
                    "rows": int(len(df)),
                    "val_nan": int(df[["pe_ttm", "pb", "total_mv"]].isna().sum().sum())}
            return df, meta
        except Exception as e:      # 该源失败 → 下一源（多源备援，不崩）
            last_err = e
            continue
    raise DataFetchError(f"{code} 全部数据源失败: {last_err!r}")


def fetch_many(codes: List[str], start: str = DEFAULT_START, end: str = DEFAULT_END,
               use_cache: bool = True) -> Tuple[Dict[str, pd.DataFrame], List[Tuple[str, str]]]:
    """批量拉取。返回 (panels, failed)；单股失败不中断批量（缺失不填分原则）。"""
    panels: Dict[str, pd.DataFrame] = {}
    failed: List[Tuple[str, str]] = []
    for code in codes:
        try:
            df, _meta = fetch_panel(code, start, end, use_cache)
            panels[code] = df
        except Exception as e:
            failed.append((code, repr(e)[:90]))
    return panels, failed


if __name__ == "__main__":
    # 独立自检：拉单只 603799 并打印摘要
    df, meta = fetch_panel("603799")
    print("meta =", meta)
    print(df.tail(2).to_string())
