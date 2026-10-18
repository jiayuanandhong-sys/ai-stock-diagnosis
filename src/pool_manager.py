# -*- coding: utf-8 -*-
"""T-03 池管理：四池名单、归属判定、池外拒绝（tasks.md v1.0）。

设计依据：
- BR-008：S 按归属成分股指数在四池（沪深300 / 中证500 / 中证1000 / 中证2000）内计算与排名；
- BR-009：四池之外的 A 股（非成分微盘股、北交所个股、新股等）拒绝服务并提示当前支持范围；
- AC-003：报告元信息"同池"标注与所属池一致；AC-008：池外返回"暂不支持 + 支持范围"；
- ADR-002：名单走中证官网（index_stock_cons_csindex 主源 / index_stock_cons 备源），
  落带日期戳 JSON 快照，可离线恢复。

接口契约：
- fetch_pool_lists(force)  -> Dict[pool, List[code]]   拉取四池名单并落快照
- load_snapshot()          -> dict                     从最新快照离线恢复（0 外网）
- resolve_pool(code)       -> dict                     归属判定（结构化结果，含拒绝文案）
"""

from __future__ import annotations

import glob
import json
import os
import re
from datetime import datetime as _dt
from typing import Dict, List, Optional

os.environ.setdefault("NO_PROXY", "*")
for _k in ("http_proxy", "https_proxy", "HTTP_PROXY", "HTTPS_PROXY"):
    os.environ.pop(_k, None)

import akshare as ak  # noqa: E402

SNAPSHOT_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                            "data_cache", "pool_snapshots")
os.makedirs(SNAPSHOT_DIR, exist_ok=True)

# 池定义（BR-008）：名称 → 中证指数代码；顺序即归属查找优先级（高级别池优先）
POOL_INDEX: Dict[str, str] = {
    "沪深300": "000300",
    "中证500": "000905",
    "中证1000": "000852",
    "中证2000": "932000",
}
SUPPORTED_RANGE_TEXT = "沪深300、中证500、中证1000、中证2000 成分股"

_CODE_RE = re.compile(r"^\d{6}$")


# ---- 名单拉取与快照 ------------------------------------------------------------

def _fetch_one_list(index_code: str) -> List[str]:
    """单池名单：中证官网主源 → akshare 备源。"""
    try:
        df = ak.index_stock_cons_csindex(index_code)
        codes = [str(c).zfill(6) for c in df["成分券代码"].tolist()]
        if codes:
            return codes
    except Exception:
        pass
    df = ak.index_stock_cons(symbol=index_code)
    codes = [str(c).zfill(6) for c in df["品种代码"].tolist()]
    if not codes:
        raise RuntimeError(f"指数 {index_code} 名单为空（主备源均失败）")
    return codes


def fetch_pool_lists(force: bool = False) -> Dict[str, List[str]]:
    """拉取四池名单 → 落带日期戳快照 → 返回 {池名: [代码]}。

    当日快照已存在且 force=False 时直接复用（0 外网）。
    """
    today = _dt.now().strftime("%Y-%m-%d")
    path = os.path.join(SNAPSHOT_DIR, f"pool_list_{today}.json")
    if not force and os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            snap = json.load(f)
        return snap["pools"]

    pools: Dict[str, List[str]] = {}
    for name, idx in POOL_INDEX.items():
        pools[name] = _fetch_one_list(idx)
    snapshot = {"snapshot_date": today, "pools": pools}
    with open(path, "w", encoding="utf-8") as f:
        json.dump(snapshot, f, ensure_ascii=False, indent=1)
    return pools


def load_snapshot() -> Dict[str, List[str]]:
    """从最新快照离线恢复（不联网）。无快照时抛 FileNotFoundError。"""
    files = sorted(glob.glob(os.path.join(SNAPSHOT_DIR, "pool_list_*.json")))
    if not files:
        raise FileNotFoundError("无池名单快照：请先调用 fetch_pool_lists()")
    with open(files[-1], encoding="utf-8") as f:
        snap = json.load(f)
    return snap["pools"]


def snapshot_info() -> Optional[dict]:
    """当前快照元信息（调试/报告元数据用）。"""
    files = sorted(glob.glob(os.path.join(SNAPSHOT_DIR, "pool_list_*.json")))
    if not files:
        return None
    with open(files[-1], encoding="utf-8") as f:
        snap = json.load(f)
    return {"snapshot_date": snap["snapshot_date"],
            "path": files[-1],
            "sizes": {k: len(v) for k, v in snap["pools"].items()}}


# ---- 归属判定（BR-008/009，AC-003/008） ------------------------------------------

def resolve_pool(code: str, pools: Optional[Dict[str, List[str]]] = None) -> dict:
    """归属判定，返回结构化结果：

    supported=True  → {"supported": True, "pool": "沪深300", "code": "603799", "message": ""}
    格式错误        → {"supported": False, "reason": "invalid_format", ...}
    池外 A 股       → {"supported": False, "reason": "out_of_pool", "message": BR-009 文案}
    """
    code = (code or "").strip()
    result = {"code": code, "pool": None, "message": ""}
    if not _CODE_RE.match(code):
        result.update({"supported": False, "reason": "invalid_format",
                       "message": "股票代码格式无效：请输入 6 位数字的 A 股代码。"})
        return result
    if pools is None:
        try:
            pools = load_snapshot()
        except FileNotFoundError:
            pools = fetch_pool_lists()
    for name in POOL_INDEX:                      # 高级别池优先
        if code in pools.get(name, []):
            result.update({"supported": True, "reason": "ok", "pool": name})
            return result
    result.update({"supported": False, "reason": "out_of_pool",
                   "message": (f"暂不支持：该股票（{code}）不在当前支持的同池范围内。"
                               f"当前支持：{SUPPORTED_RANGE_TEXT}。"
                               "其余 A 股（非成分微盘股、北交所个股等）暂不在服务范围内。")})
    return result


def pool_members(pool_name: str) -> List[str]:
    """取指定池的成分代码列表（因子引擎同池计算用）。"""
    pools = load_snapshot()
    if pool_name not in pools:
        raise KeyError(f"未知池: {pool_name}，可选 {list(pools)}")
    return pools[pool_name]


if __name__ == "__main__":
    pools = fetch_pool_lists()
    info = snapshot_info()
    print("快照:", info["snapshot_date"], info["sizes"])
    print("603799 →", resolve_pool("603799", pools)["pool"])
