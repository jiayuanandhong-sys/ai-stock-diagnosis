# -*- coding: utf-8 -*-
"""交易日历小工具（E-7）：A 股交易日历缓存 + 复核日计算（REQ-003 / AC-006）。

口径（Spike-2 已预演并验证：出分日 2026-10-16 → 复核日 2026-10-23）：
- 复核日 = 出分日 + 5 个交易日（跳过周末与节假日）；
- 日历源：新浪交易日历（akshare tool_trade_date_hist_sina），一次拉取落 JSON 缓存。
"""

from __future__ import annotations

import json
import os
from datetime import date as _date, datetime as _dt
from typing import List, Optional, Tuple

try:                                    # 产品上下文（src. 命名空间包）
    from src.announcement_source import (detect_qualifying as _detect_ann,
                                         fetch_announcements as _fetch_ann)
except ImportError:                     # 独立运行 / 评测注入上下文
    from announcement_source import (detect_qualifying as _detect_ann,
                                     fetch_announcements as _fetch_ann)

os.environ.setdefault("NO_PROXY", "*")
for _k in ("http_proxy", "https_proxy", "HTTP_PROXY", "HTTPS_PROXY"):
    os.environ.pop(_k, None)

CAL_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        "data_cache", "trade_calendar.json")


def get_trade_dates(force: bool = False) -> List[_date]:
    """A 股交易日列表（升序）。当日历缓存缺失或 force 时联网拉取。"""
    if not force and os.path.exists(CAL_PATH):
        with open(CAL_PATH, encoding="utf-8") as f:
            return [ _dt.strptime(s, "%Y-%m-%d").date() for s in json.load(f)["dates"] ]
    import akshare as ak
    df = ak.tool_trade_date_hist_sina()
    dates = sorted(_dt.strptime(str(s), "%Y-%m-%d").date() for s in df["trade_date"])
    os.makedirs(os.path.dirname(CAL_PATH), exist_ok=True)
    with open(CAL_PATH, "w", encoding="utf-8") as f:
        json.dump({"fetched": _dt.now().strftime("%Y-%m-%d"),
                   "dates": [d.isoformat() for d in dates]}, f, ensure_ascii=False)
    return dates


def next_n_trade_days(after: _date, n: int = 5, dates: Optional[List[_date]] = None) -> List[_date]:
    """after 之后的 n 个交易日（升序）。日历不足时按自然日+周末跳过近似（容错）。"""
    if dates is None:
        dates = get_trade_dates()
    future = [d for d in dates if d > after]
    if len(future) >= n:
        return future[:n]
    # 日历未覆盖远期：按工作日近似补齐（周一至周五）
    out = list(future)
    cur = dates[-1] if dates else after
    while len(out) < n:
        cur = _date.fromordinal(cur.toordinal() + 1)
        if cur.weekday() < 5:
            out.append(cur)
    return out[:n]


def review_date(score_date: _date, n: int = 5) -> Tuple[_date, bool]:
    """复核日 = score_date + n 个交易日。返回 (复核日, 是否跨节假日)。

    跨节假日判定：复核日与出分日之间自然日数 > n+4（>2 个周末日）→ 跨了法定节假日。
    """
    days = next_n_trade_days(score_date, n)
    rd = days[-1]
    span_natural = (rd - score_date).days
    crossed_holiday = span_natural > n + 4
    return rd, crossed_holiday


def volume_anomaly_ratio(panel) -> Optional[float]:
    """成交结构异常比值：近5日平均成交量 / 近20日平均成交量（OPEN-E09，PM 2026-10-18）。

    口径：两个窗口均以最新交易日为端点（20 日窗包含最近 5 日）；
    数据不足 20 根或 20 日均量 ≤0 → None（无法判定，不视为异常）；
    比值 > VOLUME_ANOMALY_TIMES（2.0，严格大于）= 成交结构异常。
    """
    try:
        v = panel["volume"].dropna()
    except Exception:
        return None
    if len(v) < 20:
        return None
    vol5 = float(v.iloc[-5:].mean())
    vol20 = float(v.iloc[-20:].mean())
    if vol20 <= 0:
        return None
    return vol5 / vol20


#: OPEN-E09（PM 2026-10-18）：近5日均量超过近20日均量的 2 倍（严格大于）= 成交结构异常
VOLUME_ANOMALY_TIMES = 2.0


def _panel_last_date(panel) -> Optional[_date]:
    """面板最后一个交易日（优先 date 列，其次 DatetimeIndex）。失败返回 None。"""
    try:
        if "date" in getattr(panel, "columns", []):
            s = panel["date"].dropna()
            if len(s):
                return _dt.strptime(str(s.iloc[-1])[:10], "%Y-%m-%d").date()
        idx = panel.index
        if len(idx):
            return _dt.strptime(str(idx[-1])[:10], "%Y-%m-%d").date()
    except Exception:
        pass
    return None


def early_review_flags(panel, code: str, announcements="auto") -> List[str]:
    """REQ-003 提前复核提示（方案A：三条件运行时自动判断，PM 2026-10-18 D2/D4/D5）。

    条件1：本周涨跌幅绝对值 ≥5%（原有行为，不变）；
    条件2：业绩预告/定期财报披露——自动获取公告发布时间（cninfo，PM 2026-10-18 定向），
           窗口内命中 qualifying 公告 → 打印具体触发语；
    条件3：成交结构异常——近5日均量 > 2×近20日均量（OPEN-E09 定案，严格大于）。

    announcements 参数："auto"（默认，真实拉取+缓存）｜公告列表（注入，确定性测试）｜
    None（显式关闭条件2检测）。公告数据不可得（网络失败且无缓存、合成代码）时，
    条件2降级为通用提示语；条件3数据不足时同理。三条件均可判定且均未触发 → 返回空列表。
    """
    flags: List[str] = []
    # ---- 条件1：周涨跌幅 ≥5%（不变） ----
    try:
        c = panel["close"].dropna()
        if len(c) >= 6:
            week_ret = c.iloc[-1] / c.iloc[-6] - 1
            if abs(week_ret) >= 0.05:
                flags.append(f"近一周涨跌幅 {week_ret:+.1%}（绝对值≥5%），建议在复核日之前提前复核。")
    except Exception:
        pass
    # ---- 条件3：成交结构异常（OPEN-E09） ----
    vol_determined = False
    try:
        ratio = volume_anomaly_ratio(panel)
        vol_determined = ratio is not None
        if ratio is not None and ratio > VOLUME_ANOMALY_TIMES:
            flags.append(f"近5日平均成交量约为近20日均量的 {ratio:.1f} 倍（超过2倍，"
                         f"成交结构异常），建议在复核日之前提前复核。")
    except Exception:
        pass
    # ---- 条件2：业绩预告/定期财报披露（PM 2026-10-18 定向自动获取） ----
    ann_records = None
    if announcements is None:
        ann_records = None                      # 显式关闭
    elif isinstance(announcements, str) and announcements == "auto":
        try:
            ann_records = _fetch_ann(code)      # 真实拉取（缓存感知；失败/合成代码 → None）
        except Exception:
            ann_records = None
    elif isinstance(announcements, (list, tuple)):
        ann_records = list(announcements)       # 注入（确定性测试）
    if ann_records is not None:
        try:
            as_of = _panel_last_date(panel)
            hits = _detect_ann(ann_records, as_of=as_of)
            for h in hits:
                flags.append(f"检测到《{h['title']}》于 {h['date']} 披露"
                             f"（业绩预告/定期财报），建议在复核日之前提前复核。")
        except Exception:
            ann_records = None                  # 检测异常按数据不可得降级
    # ---- 降级通用提示语：任一条件无法自动判定时保留 ----
    if ann_records is None or not vol_determined:
        flags.append("如遇业绩预告/定期财报披露或成交结构异常，建议提前复核。")
    return flags


if __name__ == "__main__":
    d, crossed = review_date(_dt.strptime("2026-10-16", "%Y-%m-%d").date())
    print("2026-10-16 + 5 交易日 =", d, "| 跨节假日:", crossed)
