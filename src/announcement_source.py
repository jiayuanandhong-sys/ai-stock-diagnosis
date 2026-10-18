# -*- coding: utf-8 -*-
"""公告/财报披露数据源（REQ-003 方案A·条件2，PM 2026-10-18 定向）。

PM 定向（2026-10-18）：系统自动获取股票公告或财报发布时间，检测到符合条件的公告时
提醒提前复核。

口径（实现约定，PM 可调整）：
- 数据源：巨潮资讯 cninfo（akshare stock_zh_a_disclosure_report_cninfo，按股票代码拉取，
  近 180 自然日窗口）；
- 一次拉取落 JSON 缓存（data_cache/announcements/{code}.json），当日缓存直接复用；
  网络失败时回退旧缓存，仍失败则返回 None（调用方降级为通用提示语，不阻断报告）；
- 符合条件的公告：标题命中业绩预告/业绩快报/定期财报关键词
  （业绩预告、业绩快报、年度报告、半年度报告、第一季度报告、第三季度报告）；
  注意「业绩说明会」类公告不属于披露事项，不得命中；
- 检测窗口：公告时间在 (as_of - lookback_days, as_of] 自然日内（默认 10 天，
  覆盖一个完整复核周期的自然日跨度）。

确定性测试纪律：本模块的检测逻辑（is_qualifying / detect_qualifying）为纯函数，
支持注入公告列表，不依赖网络；网络仅发生在 fetch_announcements。
"""

from __future__ import annotations

import json
import os
import re
from datetime import date as _date, datetime as _dt, timedelta as _td
from typing import List, Optional

os.environ.setdefault("NO_PROXY", "*")
for _k in ("http_proxy", "https_proxy", "HTTP_PROXY", "HTTPS_PROXY"):
    os.environ.pop(_k, None)

_CACHE_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                          "data_cache", "announcements")

#: 符合提前复核条件的公告标题关键词（业绩预告/业绩快报/定期财报）
QUALIFYING_KEYWORDS = ("业绩预告", "业绩快报", "年度报告",
                       "半年度报告", "第一季度报告", "第三季度报告")

#: 默认检测窗口（自然日）：公告时间在 (as_of - lookback, as_of] 内视为"新披露"
DEFAULT_LOOKBACK_DAYS = 10

_CODE_RE = re.compile(r"^\d{6}$")


def is_qualifying(title: str) -> bool:
    """标题是否属于业绩预告/业绩快报/定期财报披露事项（REQ-003 条件2）。"""
    if not title:
        return False
    return any(k in title for k in QUALIFYING_KEYWORDS)


def _parse_date(s) -> Optional[_date]:
    try:
        return _dt.strptime(str(s)[:10], "%Y-%m-%d").date()
    except Exception:
        return None


def detect_qualifying(announcements: Optional[List[dict]],
                      as_of=None,
                      lookback_days: int = DEFAULT_LOOKBACK_DAYS) -> List[dict]:
    """纯函数：返回窗口内符合条件（业绩预告/定期财报）的公告记录。

    announcements: [{"date": "YYYY-MM-DD", "title": "..."}]，可注入（确定性测试）；
    as_of: 锚定日（默认今天）；命中条件 is_qualifying(title) 且
           (as_of - lookback_days) < date <= as_of。
    """
    if not announcements:
        return []
    anchor = as_of if isinstance(as_of, _date) else (
        _parse_date(as_of) if as_of else _date.today())
    if anchor is None:
        anchor = _date.today()
    lo = anchor - _td(days=lookback_days)
    hits = []
    for rec in announcements:
        d = _parse_date(rec.get("date", ""))
        if d is not None and lo < d <= anchor and is_qualifying(rec.get("title", "")):
            hits.append(rec)
    return hits


def fetch_announcements(code: str, force: bool = False,
                        cache_dir: Optional[str] = None) -> Optional[List[dict]]:
    """按股票代码拉取公告列表（cninfo）。返回 [{"date","title"}]（按日期降序）或 None。

    - 非 6 位数字代码（如评测合成代码）不发起网络请求，直接返回 None；
    - 当日缓存命中直接复用；网络失败回退旧缓存；两者皆无 → None（调用方降级）。
    """
    if not code or not _CODE_RE.match(str(code)):
        return None
    cdir = cache_dir or _CACHE_DIR
    cpath = os.path.join(cdir, f"{code}.json")
    today = _date.today().isoformat()
    if not force and os.path.exists(cpath):
        try:
            with open(cpath, encoding="utf-8") as f:
                cached = json.load(f)
            if cached.get("fetched") == today:
                return cached["records"]
        except Exception:
            pass  # 缓存损坏 → 重新拉取
    try:
        import akshare as ak
        end = _date.today()
        start = end - _td(days=180)
        df = ak.stock_zh_a_disclosure_report_cninfo(
            symbol=code, market="沪深京",
            start_date=start.strftime("%Y%m%d"), end_date=end.strftime("%Y%m%d"))
        records = [{"date": str(r["公告时间"])[:10], "title": str(r["公告标题"])}
                   for _, r in df.iterrows()]
        records.sort(key=lambda r: r["date"], reverse=True)
        os.makedirs(cdir, exist_ok=True)
        tmp = cpath + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump({"fetched": today, "source": "cninfo", "records": records},
                      f, ensure_ascii=False)
        os.replace(tmp, cpath)
        return records
    except Exception:
        if os.path.exists(cpath):                       # 回退旧缓存（陈旧好于无数据）
            try:
                with open(cpath, encoding="utf-8") as f:
                    return json.load(f)["records"]
            except Exception:
                return None
        return None


if __name__ == "__main__":
    # 冒烟：真实拉取一只池外测试股（600768，富邦新材）
    recs = fetch_announcements("600768")
    print("600768 公告数:", len(recs) if recs is not None else None)
    for r in (recs or [])[:5]:
        print(" ", r["date"], r["title"], "| qualifying =", is_qualifying(r["title"]))
