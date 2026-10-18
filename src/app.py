# -*- coding: utf-8 -*-
"""T-07 Web 应用：FastAPI 网页自助载体（CR-S-001 / ADR-003⑤）。

路由（服务端渲染，无前端框架、无 JS 依赖、无登录、零采集 AC-009）：
- GET /                 输入页（唯一字段 = 股票代码）
- GET /diagnose?code=   诊断主路径：池校验（T-03）→ 数据（T-02）→ 因子（T-04）
                        → 报告组装（T-05，内含 T-06 解读与 T-01 硬门禁）→ 在线 HTML
- GET /download?code=   docx 下载（与在线版同源同构，AC-011）

池模式（部署配置，非功能分支）：
- SDD_POOL_MODE=demo（默认）：分池演示样本（CR-V-004：沪深300 样本 21 只 + 中证500 样本 20 只，
  开始页提供快速开始卡片与全池可选芯片；按真实归属池取样本计算 S 分）
- SDD_POOL_MODE=full：中证四池真实名单（M2 目标形态；首次冷启动需预热缓存）

报告缓存：out/reports/{code}/ 当日产物直接复用（数据日线级、日更，零信息损失）。
"""

from __future__ import annotations

import json
import os
import re
import threading
from datetime import datetime as _dt
from typing import Dict, List, Optional, Tuple

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, HTMLResponse

from src.compliance import check_report
from src.data_fetcher import fetch_many
from src.pool_manager import SUPPORTED_RANGE_TEXT, load_snapshot, resolve_pool
from src.rate_limiter import IPRateLimiter, limit_text
from src.report_builder import _html_to_text, build_report, check_sections
from src.trade_cal import get_trade_dates

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REPORT_DIR = os.path.join(APP_DIR, "out", "reports")
os.makedirs(REPORT_DIR, exist_ok=True)

POOL_MODE = os.environ.get("SDD_POOL_MODE", "demo")
DEFAULT_START = "20240901"

# ---- 演示模式分池样本（CR-V-004：开始页多选 + 按真实归属池分池演示） ---------------
# PM 2026-10-17 下令：开始页须提供多只股票可点选。演示模式由"单一演示池"升级为
# "分池样本"——每只股票按其真实归属池（BR-008）在该池的样本集内计算 S 分。

# 开始页"快速开始"精选卡片（code/name/pool；九号公司=CDR 数据源受限，禁用态展示，OPEN 待 PM 决策）
FEATURED: List[Dict[str, str]] = [
    {"code": "603799", "name": "华友钴业", "pool": "沪深300"},
    {"code": "300059", "name": "东方财富", "pool": "沪深300"},
    {"code": "300408", "name": "三环集团", "pool": "沪深300"},
    {"code": "300058", "name": "蓝色光标", "pool": "中证500"},
    {"code": "689009", "name": "九号公司", "pool": "中证500"},  # OPEN：新浪不提供 CDR 行情
]

# 沪深300 演示样本：原 20 只 + 三环集团（PM 参考报告之一，2026-10-17 快照实查归属沪深300）
DEMO_POOL_300: List[Tuple[str, str]] = [
    ("603799", "华友钴业"), ("600519", "贵州茅台"), ("601318", "中国平安"), ("600036", "招商银行"),
    ("000858", "五粮液"),   ("300750", "宁德时代"), ("002594", "比亚迪"),   ("600900", "长江电力"),
    ("601899", "紫金矿业"), ("000333", "美的集团"), ("600276", "恒瑞医药"), ("002415", "海康威视"),
    ("600887", "伊利股份"), ("601166", "兴业银行"), ("600028", "中国石化"), ("601398", "工商银行"),
    ("300059", "东方财富"), ("601668", "中国建筑"), ("601012", "隆基绿能"), ("600030", "中信证券"),
    ("300408", "三环集团"),
]


def _demo_pool_500(n: int = 20) -> List[str]:
    """中证500 演示样本：蓝色光标 + 快照确定性前 n-1 只（不含九号公司：数据源受限，OPEN）。"""
    try:
        codes = sorted(load_snapshot().get("中证500", []))
    except Exception:
        return ["300058"]
    rest = [c for c in codes if c not in ("300058", "689009")][: n - 1]
    return ["300058"] + rest


DEMO_POOLS: Dict[str, List[str]] = {"沪深300": [c for c, _ in DEMO_POOL_300]}
DEMO_POOLS["中证500"] = _demo_pool_500()

# 限流器（E-10，T-08）：单 IP 每日 50 次（/diagnose + /download 共享；环境变量可调）
LIMITER = IPRateLimiter(daily_limit=int(os.environ.get("SDD_DAILY_LIMIT", "50")))

# 名称查询表（零采集：不建用户数据，仅股票静态属性；全表落盘一次、重启零外网）
_NAME_PATH = os.path.join(APP_DIR, "data_cache", "name_map.json")


def _load_name_map() -> Dict[str, str]:
    base = {c: n for c, n in DEMO_POOL_300}
    base.update({"300058": "蓝色光标", "689009": "九号公司"})
    try:
        if os.path.exists(_NAME_PATH):
            with open(_NAME_PATH, encoding="utf-8") as f:
                base.update(json.load(f))
        else:
            import akshare as ak
            df = ak.stock_info_a_code_name()
            fetched = {str(c).zfill(6): str(n) for c, n in zip(df["code"], df["name"])}
            with open(_NAME_PATH, "w", encoding="utf-8") as f:
                json.dump(fetched, f, ensure_ascii=False)
            base.update(fetched)
    except Exception:
        pass
    return base


_NAME_MAP = _load_name_map()

app = FastAPI(title="AI 量化个股诊断 MVP", docs_url=None, redoc_url=None, openapi_url=None)

_page_locks: Dict[str, threading.Lock] = {}
_locks_guard = threading.Lock()


def _page_lock(code: str) -> threading.Lock:
    with _locks_guard:
        if code not in _page_locks:
            _page_locks[code] = threading.Lock()
        return _page_locks[code]


def _stock_name(code: str) -> str:
    if code in _NAME_MAP:
        return _NAME_MAP[code]
    try:                                   # full 模式下尝试从 akshare 取名（可失败，失败用代码）
        import akshare as ak
        info = ak.stock_individual_info_em(symbol=code)
        _NAME_MAP[code] = str(info[info["item"] == "股票简称"]["value"].iloc[0])
    except Exception:
        _NAME_MAP[code] = code
    return _NAME_MAP[code]


def _pool_for(code: str) -> Tuple[Optional[str], Optional[str], Optional[List[str]]]:
    """返回 (错误HTML, 池名, 成分代码列表)。错误路径覆盖 SCN-002/003。"""
    r = resolve_pool(code)                        # 格式校验 + 池外判定 + 真实归属池（T-03）
    if not r["supported"]:
        if r["reason"] == "invalid_format":
            return _err_page("代码格式无效", r["message"]), None, None
        return _err_page("暂不支持", r["message"]), None, None
    if POOL_MODE == "demo":
        pool = r["pool"]                           # BR-008：按真实归属池取分池样本
        demo = DEMO_POOLS.get(pool)
        if not demo:
            sizes = "、".join(f"{p} 样本 {len(v)} 只" for p, v in DEMO_POOLS.items())
            return _err_page(
                "演示模式支持范围",
                f"当前为演示模式，暂未提供 {pool} 的演示样本（当前提供：{sizes}）。"
                f"完整版支持 {SUPPORTED_RANGE_TEXT}。"), None, None
        if code not in demo:
            listing = "、".join(f"{c} {_NAME_MAP.get(c, '')}" for c in demo)
            return _err_page(
                "演示模式支持范围",
                f"当前为演示模式（{pool} 样本 {len(demo)} 只）。演示支持列表：{listing}。"
                f"完整版支持 {SUPPORTED_RANGE_TEXT}。"), None, None
        return None, f"演示同池（{pool} 样本 {len(demo)} 只）", list(demo)
    # full 模式：真实四池（M2）
    pools = load_snapshot()
    pool_name = r["pool"]
    return None, f"{pool_name}（{pool_name} 全池）", pools[pool_name]


def _err_page(title: str, message: str) -> str:
    return f"""<!DOCTYPE html><html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{title}</title><style>
*{{margin:0;padding:0;box-sizing:border-box}}
body{{background:#f5f5f7;color:#1d1d1f;text-align:center;
font-family:-apple-system,BlinkMacSystemFont,"SF Pro Display","SF Pro Text",
"PingFang SC","Hiragino Sans GB","Microsoft YaHei","Helvetica Neue",sans-serif;
-webkit-font-smoothing:antialiased}}
.wrap{{max-width:680px;margin:14vh auto 0;padding:0 22px}}
.card{{background:#fff;border-radius:18px;padding:48px 40px;
box-shadow:0 4px 24px rgba(0,0,0,.06)}}
h2{{font-size:28px;font-weight:700;letter-spacing:-.01em}}
.msg{{margin-top:14px;font-size:16px;color:#424245;line-height:1.6}}
.disc{{margin-top:24px;padding-top:20px;border-top:1px solid rgba(0,0,0,.08);
font-size:12px;color:#86868b;line-height:1.7}}
a{{display:inline-block;margin-top:26px;color:#0071e3;text-decoration:none;
font-size:15px}}
a:hover{{text-decoration:underline}}
</style></head>
<body><div class="wrap"><div class="card"><h2>{title}</h2>
<p class="msg">{message}</p>
<p class="disc">本工具仅输出量化因子状态的客观描述，不代表上涨概率、可能性或收益预期，不构成任何证券交易类操作建议。</p>
<a href="/">← 返回重新输入</a></div></div></body></html>"""


def _today_key() -> str:
    return _dt.now().strftime("%Y-%m-%d")


def _report_paths(code: str, name: str) -> Dict[str, str]:
    d = os.path.join(REPORT_DIR, code)
    return {"dir": d,
            "html": os.path.join(d, f"{name}_{code}_因子诊断报告_合并版.html"),
            "docx": os.path.join(d, f"{name}_{code}_因子诊断报告_合并版.docx"),
            "stamp": os.path.join(d, "generated_on.txt")}


def _build_if_needed(code: str) -> Tuple[str, str]:
    """生成或复用当日报告（进程内加锁防并发重算）。返回 (html_path, docx_path)。"""
    with _page_lock(code):
        name = _stock_name(code)
        paths = _report_paths(code, name)
        if (os.path.exists(paths["stamp"])
                and open(paths["stamp"], encoding="utf-8").read().strip() == _today_key()
                and os.path.exists(paths["html"]) and os.path.exists(paths["docx"])):
            return paths["html"], paths["docx"]           # 当日产物复用（日线级数据日更）
        err_html, pool_name, members = _pool_for(code)
        if err_html is not None or pool_name is None or members is None:
            raise _BlockedError(err_html or "不支持该代码")
        panels, failed = fetch_many(members, DEFAULT_START,
                                     _dt.now().strftime("%Y%m%d"), use_cache=True)
        if code not in panels:
            raise _BlockedError(_err_page("数据暂时不可用",
                                          f"目标股票当日数据拉取失败，请稍后重试。"))
        result = build_report(panels, code, name, pool_name, paths["dir"])
        with open(paths["stamp"], "w", encoding="utf-8") as f:
            f.write(_today_key())
        return result["html"], result["docx"]


class _BlockedError(Exception):
    """携带用户可见错误页 HTML 的业务异常（SCN-002/003）。"""
    def __init__(self, html: str):
        self.html = html
        super().__init__("blocked")


# ---- 页面 -----------------------------------------------------------------------

_INDEX_CSS = """*{margin:0;padding:0;box-sizing:border-box}
html{-webkit-text-size-adjust:100%}
body{background:#f5f5f7;color:#1d1d1f;text-align:center;
font-family:-apple-system,BlinkMacSystemFont,"SF Pro Display","SF Pro Text",
"PingFang SC","Hiragino Sans GB","Microsoft YaHei","Helvetica Neue",sans-serif;
-webkit-font-smoothing:antialiased}
.nav{position:sticky;top:0;z-index:10;border-bottom:1px solid rgba(0,0,0,.08);
background:rgba(245,245,247,.72);
-webkit-backdrop-filter:saturate(180%) blur(20px);
backdrop-filter:saturate(180%) blur(20px)}
.nav-inner{max-width:980px;margin:0 auto;padding:0 22px;height:48px;
display:flex;align-items:center;justify-content:center}
.nav-title{font-size:14px;font-weight:600;letter-spacing:.2px}
.hero{max-width:820px;margin:0 auto;padding:clamp(48px,9vh,84px) 22px 0}
h1{font-size:clamp(34px,5.6vw,52px);font-weight:700;letter-spacing:-.02em;line-height:1.1}
.sub{margin-top:16px;font-size:clamp(17px,2.4vw,21px);font-weight:400;
color:#6e6e73;line-height:1.55}
form{margin-top:40px;display:flex;justify-content:center;gap:14px;flex-wrap:wrap}
input[name=code]{width:min(320px,80vw);height:56px;border:1px solid #d2d2d7;
border-radius:12px;background:#fff;font-size:19px;font-family:inherit;
color:#1d1d1f;text-align:center;outline:none;
transition:box-shadow .18s ease,border-color .18s ease}
input[name=code]::placeholder{color:#a1a1a6;font-size:17px}
input[name=code]:focus{border-color:#0071e3;box-shadow:0 0 0 4px rgba(0,113,227,.16)}
button{height:56px;padding:0 34px;border:none;border-radius:980px;background:#0071e3;
color:#fff;font-size:17px;font-weight:600;font-family:inherit;letter-spacing:.1px;
cursor:pointer;transition:background .18s ease,transform .1s ease}
button:hover{background:#0077ed}
button:active{transform:scale(.98)}
.pick{max-width:920px;margin:52px auto 0;padding:0 22px}
.pick h2{font-size:clamp(20px,3vw,24px);font-weight:700;letter-spacing:-.01em}
.pick-sub{margin-top:8px;font-size:14px;color:#86868b}
.feat-row{margin-top:20px;display:flex;gap:14px;flex-wrap:wrap;justify-content:center}
.feat{display:flex;flex-direction:column;gap:7px;min-width:150px;padding:18px 22px;
background:#fff;border-radius:16px;text-decoration:none;
box-shadow:0 2px 12px rgba(0,0,0,.05);
transition:transform .16s ease,box-shadow .16s ease}
.feat:hover{transform:translateY(-2px);box-shadow:0 8px 24px rgba(0,0,0,.08)}
.feat.off{opacity:.42;cursor:not-allowed}
.feat.off:hover{transform:none;box-shadow:0 2px 12px rgba(0,0,0,.05)}
.fname{font-size:17px;font-weight:600;color:#1d1d1f}
.fcode{font-size:13px;color:#6e6e73}
.badge{font-size:11px;color:#0071e3;background:rgba(0,113,227,.09);border-radius:980px;
padding:3px 10px;align-self:flex-start}
.badge.off{color:#86868b;background:rgba(0,0,0,.05)}
.pool{max-width:920px;margin:48px auto 0;padding:0 22px;text-align:left}
.pool h2{font-size:clamp(18px,2.6vw,20px);font-weight:700;text-align:center}
.gtitle{font-size:13px;color:#86868b;margin:18px 0 10px;text-align:left}
.chips{display:flex;flex-wrap:wrap}
.chip{display:inline-block;margin:4px;padding:7px 14px;background:#fff;border-radius:980px;
text-decoration:none;font-size:13px;color:#1d1d1f;
box-shadow:0 1px 4px rgba(0,0,0,.05);transition:background .16s ease,color .16s ease}
.chip:hover{background:#0071e3;color:#fff}
.hairline{max-width:620px;margin:64px auto 0;height:1px;background:rgba(0,0,0,.1)}
.note{max-width:620px;margin:26px auto 96px;padding:0 22px;font-size:12px;
color:#86868b;line-height:1.75}
@media (max-width:560px){form{flex-direction:column;align-items:center}
button{width:min(320px,80vw)}}"""


def _build_index_html() -> str:
    cards, chips = [], []
    for it in FEATURED:
        if it["code"] in DEMO_POOLS.get(it["pool"], []):
            cards.append(
                f'<a class="feat" href="/diagnose?code={it["code"]}">'
                f'<span class="fname">{it["name"]}</span>'
                f'<span class="fcode">{it["code"]}</span>'
                f'<span class="badge">{it["pool"]}</span></a>')
        else:  # 数据源受限（CDR）：禁用态展示，透明可预期
            cards.append(
                f'<span class="feat off" title="该股（科创板 CDR）行情数据源暂未覆盖，即将支持">'
                f'<span class="fname">{it["name"]}</span>'
                f'<span class="fcode">{it["code"]}</span>'
                f'<span class="badge off">{it["pool"]} · 暂不可用</span></span>')
    for pool, demo in DEMO_POOLS.items():
        chips.append(f'<div class="gtitle">{pool} 样本 {len(demo)} 只</div><div class="chips">')
        for c in demo:
            nm = _NAME_MAP.get(c, c)
            chips.append(f'<a class="chip" href="/diagnose?code={c}">{nm}&nbsp;{c}</a>')
        chips.append("</div>")
    featured_html = "\n".join(cards)
    chips_html = "\n".join(chips)
    return f"""<!DOCTYPE html><html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>AI 量化个股诊断</title><style>{_INDEX_CSS}</style></head><body>
<nav class="nav"><div class="nav-inner"><span class="nav-title">AI 量化个股诊断</span></div></nav>
<main class="hero">
<h1>AI 量化个股诊断</h1>
<p class="sub">输入一个 A 股股票代码，生成该股的周频量化因子诊断报告（合并版：简要 + 详细）。</p>
<form method="get" action="/diagnose">
<input name="code" placeholder="如 603799" maxlength="6" inputmode="numeric" required
       pattern="[0-9]{{6}}" title="请输入 6 位数字股票代码" aria-label="股票代码">
<button type="submit">开始诊断</button>
</form>
</main>
<section class="pick">
<h2>快速开始</h2>
<p class="pick-sub">从下面几只股票直接选一只，立即体验</p>
<div class="feat-row">
{featured_html}
</div>
</section>
<section class="pool">
<h2>演示池全部可选</h2>
{chips_html}
</section>
<div class="hairline"></div>
<div class="note">
本工具仅使用收盘后的日线级公开行情与估值数据；不收集任何个人信息（仅代码一个输入）。
分数仅代表因子状态在同池中的相对强弱，不代表上涨概率、可能性或收益预期，
不构成任何证券交易类操作建议。
</div>
</body></html>"""


_INDEX_HTML = _build_index_html()


@app.get("/", response_class=HTMLResponse)
def index():
    return _INDEX_HTML                     # 无 Cookie、无 JS、无任何采集（AC-009）


@app.get("/diagnose", response_class=HTMLResponse)
def diagnose(code: str = "", request: Request = None):
    ip = request.client.host if request is not None and request.client else "unknown"
    allowed, used, limit = LIMITER.check_and_count(ip)
    if not allowed:                                     # E-10 防滥用（T-08）
        return HTMLResponse(_err_page("请求次数已达今日上限", limit_text(used, limit)),
                            status_code=429)
    code = (code or "").strip()
    if not re.fullmatch(r"\d{6}", code):
        return HTMLResponse(_err_page("代码格式无效",
                                      "股票代码格式无效：请输入 6 位数字的 A 股代码。"),
                            status_code=400)
    try:
        html_path, _docx = _build_if_needed(code)
    except _BlockedError as e:
        return HTMLResponse(e.html, status_code=400)   # SCN-003 池外/演示范围外
    html = open(html_path, encoding="utf-8").read()
    if check_sections(html):                             # 防御性复检（AC-001）
        return HTMLResponse(_err_page("报告异常", "报告章节完整性校验未通过，已阻断输出。"),
                            status_code=500)
    return HTMLResponse(html)                            # 200 在线完整报告


@app.get("/download")
def download(code: str = "", request: Request = None):
    ip = request.client.host if request is not None and request.client else "unknown"
    allowed, used, limit = LIMITER.check_and_count(ip)
    if not allowed:
        return HTMLResponse(_err_page("请求次数已达今日上限", limit_text(used, limit)),
                            status_code=429)
    code = (code or "").strip()
    if not re.fullmatch(r"\d{6}", code):
        return HTMLResponse(_err_page("代码格式无效",
                                      "股票代码格式无效：请输入 6 位数字的 A 股代码。"),
                            status_code=400)
    try:
        _html_path, docx_path = _build_if_needed(code)
    except _BlockedError as e:
        return HTMLResponse(e.html, status_code=400)
    name = _stock_name(code)
    return FileResponse(docx_path, media_type="application/vnd.openxmlformats-"
                        "officedocument.wordprocessingml.document",
                        filename=f"{name}_{code}_因子诊断报告_合并版.docx")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=int(os.environ.get("SDD_PORT", "8000")))
