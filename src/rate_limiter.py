# -*- coding: utf-8 -*-
"""T-08 限流器：单 IP 每日次数上限（内存计数，无外部依赖）。

E-10：无登录防滥用。BR-004 零采集——计数仅含 IP 与次数（运行时内存态，
不落盘、不与任何个人数据关联）；重启清零（MVP 量级可接受，ADR-003⑤）。
"""

from __future__ import annotations

import threading
from datetime import datetime as _dt
from typing import Dict, Tuple


class IPRateLimiter:
    """单 IP 每日请求计数限流（/diagnose 与 /download 共享配额）。"""

    def __init__(self, daily_limit: int = 50):
        self.daily_limit = int(daily_limit)
        self._counts: Dict[str, int] = {}
        self._day = _dt.now().strftime("%Y-%m-%d")
        self._lock = threading.Lock()

    def _rollover(self) -> None:
        today = _dt.now().strftime("%Y-%m-%d")
        if today != self._day:            # 跨日自动重置（内存态）
            self._counts.clear()
            self._day = today

    def check_and_count(self, ip: str) -> Tuple[bool, int, int]:
        """返回 (是否放行, 已用次数, 上限)。放行即计数 +1（原子操作）。"""
        with self._lock:
            self._rollover()
            n = self._counts.get(ip, 0)
            if n >= self.daily_limit:
                return False, n, self.daily_limit
            self._counts[ip] = n + 1
            return True, n + 1, self.daily_limit

    def current(self, ip: str) -> int:
        with self._lock:
            self._rollover()
            return self._counts.get(ip, 0)


def limit_text(used: int, limit: int) -> str:
    return (f"今日请求次数已达上限（{used}/{limit}）。本服务为小规模试用版，"
            "每个访问地址每日可发起的次数有限，明天再来即可。")
