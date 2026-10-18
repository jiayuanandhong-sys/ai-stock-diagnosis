# -*- coding: utf-8 -*-
"""run_e2e.py —— Evals E2E Runner（Phase 4）：真实 Web 服务端到端执行。

执行纪律（延续 run_evals.py，v1.2 冻结数据集 manifest_version 1.2）：
- 启动门禁与 run_evals 完全相同（冻结清单 valid + PEG PASS + 逐案例 sha256）；
  本 Runner 执行离线 Runner 中标记「需服务 / 需联网」的全部 E2E 断言（15 案例：
  L6-020/021/023、E2E-001/002、E2E-003a/b、E2E-004a/b、E2E-005a/b、L2-008、
  L1-029、L5-031、L6-038），不重复执行已离线验证的断言，不修改任何冻结数据集文件；
- 被测对象 = 产品真实服务进程（uvicorn + src.app:app，demo 部署配置，
  与 deploy/start_server.bat 一致：SDD_POOL_MODE=demo / SDD_DAILY_LIMIT=50），
  真实 HTTP 请求（127.0.0.1:8137），真实数据层网络取数（新浪主源 / 东财备援 /
  百度估值），不 mock 任何被测链路；
- 每条断言独立记录 PASS/FAIL/NOT_RUN 与证据；五状态聚合与退出码约定同 run_evals
  （0=完整通过；1=有 FAIL；2=存在 PARTIAL/BLOCKED/NOT_RUN 或门禁失败）；
- E2E 断言与最近一次离线运行（runner/results/ 最新 run）按「断言名去重合并」
  计算案例级合并状态，供放行清单引用。

已知口径披露（执行时如实记录，不静默）：
- 冻结案例元数据 http_method=POST，产品真实契约为 GET /diagnose?code=
  （T-07 表单 method="get"；FastAPI @app.get）。本 Runner 按真实契约执行，
  差异作为发现披露，冻结数据集不动（下一 PEG 周期候选修订项）；
- 部署配置为 demo 模式：E2E-004a/b（中证1000）、E2E-005a/b（中证2000）
  在 demo 模式下的正确行为 = 400 + 正确池归属 + 透明范围提示
  （expected_output.report_generated=null，案例预期核心为池归属 AC-003）；
  full 模式报告生成链路（1000/2000 成分预热）属 M2 范围，本轮不覆盖并在证据中披露。

运行：python run_e2e.py   （结果写 results_e2e/run_<时间戳>/）
"""
from __future__ import annotations

import json
import os
import re
import shutil
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
EVALS = HERE.parent
sys.path.insert(0, str(HERE))          # 复用 run_evals 的门禁与聚合
sys.path.insert(0, str(EVALS / "dataset"))

from run_evals import (             # noqa: E402
    PROD, DATASET, Ctx, load_cases, validate_freeze_gate,
    aggregate_status, exit_code_from_summary,
)
import src.compliance as CM          # noqa: E402  产品被测模块（T-01 扫描器）
import src.report_builder as RB      # noqa: E402  章节清单 / HTML→text
import src.factor_engine as FE       # noqa: E402  band 着色基准
import src.data_fetcher as DF        # noqa: E402  取数护栏（L2-008 / L1-029）
import src.trade_cal as TC           # noqa: E402  交易日历（数据新鲜度）

RUN_TS = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
OUT = HERE / "results_e2e" / f"run_{RUN_TS}"
LOG_LINES: list[str] = []

PORT = 8137
BASE = f"http://127.0.0.1:{PORT}"
TODAY_DASH = datetime.now().strftime("%Y-%m-%d")
TODAY_COMPACT = datetime.now().strftime("%Y%m%d")

INDEX_INPUT_RE = re.compile(r'<input[^>]*name="([^"]+)"')


def log(msg: str) -> None:
    line = f"[{datetime.now().strftime('%H:%M:%S')}] {msg}"
    print(line, flush=True)
    LOG_LINES.append(line)


# --------------------------------------------------------------- HTTP 原语
def http_get(path: str, timeout: float = 120.0):
    """真实 HTTP GET。返回 (status, headers(dict 小写键), body_bytes)。"""
    req = urllib.request.Request(BASE + path, headers={"User-Agent": "sdd-evals-e2e/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, {k.lower(): v for k, v in r.headers.items()}, r.read()
    except urllib.error.HTTPError as e:
        return e.code, {k.lower(): v for k, v in e.headers.items()}, e.read()


def diagnose(code: str, timeout: float = 900.0):
    """GET /diagnose?code=…，返回 (status, html_text)。"""
    q = urllib.parse.urlencode({"code": code})
    st, _hd, body = http_get(f"/diagnose?{q}", timeout=timeout)
    return st, body.decode("utf-8", "replace")


# --------------------------------------------------------------- 报告解析（黑盒）
_S_RE = re.compile(r'style="color:(#[0-9A-Fa-f]{6})">([\d.]+)（([^（）]*)）')
_H_RE = re.compile(r'style="color:(#[0-9A-Fa-f]{6})">([\d.]+)</td>')
_RANK_RE = re.compile(r"<td>(\d+)/(\d+)</td>")
_REVIEW_RE = re.compile(r"复核日</th><td>(\d{4}-\d{2}-\d{2})")
_OBSERVE_RE = re.compile(r"观察日（出分日）</th><td>(\d{4}-\d{2}-\d{2})")
_SIDE_RE = re.compile(r"<tr><td>(估值特征|价格行为|交易行为|风险特征)</td>"
                      r"<td>([\d.]+|—)</td>"
                      r"<td style='color:(#[0-9A-Fa-f]{6})'>([^<]*)</td></tr>")
_FACTOR_RE = re.compile(r"<tr><td>(F\d{1,2})</td><td>[^<]*</td><td>[^<]*</td>"
                        r"<td>([\d.]+|—)</td>"
                        r"<td style='color:(#[0-9A-Fa-f]{6})'>([^<]*)</td></tr>")


def parse_report(html: str) -> dict:
    out: dict = {"title_pool": None, "S": None, "H": None, "rank": None,
                 "review_date": None, "observe_date": None,
                 "S_hex": None, "S_label": None, "H_hex": None,
                 "sides": [], "factors": []}
    m = _S_RE.search(html)
    if m:
        out["S_hex"], out["S"], out["S_label"] = m.group(1), float(m.group(2)), m.group(3)
    if m:
        m2 = _H_RE.search(html, m.end())
        if m2:
            out["H_hex"], out["H"] = m2.group(1), float(m2.group(2))
    mr = _RANK_RE.search(html)
    if mr:
        out["rank"] = (int(mr.group(1)), int(mr.group(2)))
    mrv = _REVIEW_RE.search(html)
    if mrv:
        out["review_date"] = mrv.group(1)
    mo = _OBSERVE_RE.search(html)
    if mo:
        out["observe_date"] = mo.group(1)
    out["sides"] = [(m.group(1), m.group(2), m.group(3), m.group(4))
                    for m in _SIDE_RE.finditer(html)]
    out["factors"] = [(m.group(1), m.group(2), m.group(3), m.group(4))
                      for m in _FACTOR_RE.finditer(html)]
    return out


def coloring_pairs_ok(parsed: dict) -> tuple[bool, dict]:
    """着色一致性：S/H/四侧面/18 因子的 (色值, 分档标签) 与 band(分数) 一致（Gate V 48 项口径）。"""
    checks: list[tuple[str, bool]] = []

    def hexok(actual, expect):
        return actual.lower() == expect.lower()

    if parsed["S"] is not None:
        _n, hx, lb = FE.band(parsed["S"])
        checks.append(("S.hex", hexok(parsed["S_hex"], hx)))
        checks.append(("S.label", parsed["S_label"] == lb))
    if parsed["H"] is not None:
        _n, hx, _lb = FE.band(parsed["H"])
        checks.append(("H.hex", hexok(parsed["H_hex"], hx)))
    for name, val, hx, lb in parsed["sides"]:
        v = None if val == "—" else float(val)
        _n, ehx, elb = FE.band(v)
        checks.append((f"side.{name}.hex", hexok(hx, ehx)))
        checks.append((f"side.{name}.label", lb == elb))
    for fid, val, hx, lb in parsed["factors"]:
        v = None if val == "—" else float(val)
        _n, ehx, elb = FE.band(v)
        checks.append((f"{fid}.hex", hexok(hx, ehx)))
        checks.append((f"{fid}.label", lb == elb))
    wrong = [k for k, ok in checks if not ok]
    return (len(checks) > 0 and not wrong), {"total_checks": len(checks), "wrong": wrong}


def docx_text(docx_path: Path) -> str:
    from docx import Document
    doc = Document(str(docx_path))
    parts = [p.text for p in doc.paragraphs]
    for tb in doc.tables:
        for row in tb.rows:
            parts.extend(c.text for c in row.cells)
    return "\n".join(parts)


# --------------------------------------------------------------- 服务进程管理
def _port_free(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            s.bind(("127.0.0.1", port))
            return True
        except OSError:
            return False


def start_server() -> subprocess.Popen:
    if not _port_free(PORT):
        raise RuntimeError(f"端口 {PORT} 被占用")
    env = {**os.environ,
           "SDD_POOL_MODE": "demo", "SDD_PORT": str(PORT),
           "PYTHONIOENCODING": "utf-8", "PYTHONUNBUFFERED": "1"}
    logf = open(OUT / "server.log", "w", encoding="utf-8")
    proc = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "src.app:app",
         "--host", "127.0.0.1", "--port", str(PORT)],
        cwd=str(PROD), env=env, stdout=logf, stderr=subprocess.STDOUT)
    t0 = time.time()
    while time.time() - t0 < 120:
        try:
            st, _hd, _b = http_get("/", timeout=5)
            if st == 200:
                log(f"服务就绪（{BASE}，启动 {time.time()-t0:.1f}s，pid={proc.pid}）")
                return proc
        except Exception:
            pass
        if proc.poll() is not None:
            raise RuntimeError(f"服务进程提前退出（code={proc.returncode}），见 server.log")
        time.sleep(1)
    proc.terminate()
    raise RuntimeError("服务 120s 内未就绪，见 server.log")


# --------------------------------------------------------------- 各案例 E2E 执行器
def e2e_l6_020(case, ctx):
    rows, ok = {}, True
    for v in case["input"]["variants"]:
        st, html = diagnose(v, timeout=30)
        hit = "格式无效" in html
        rows[v or "(空)"] = {"http_status": st, "format_invalid": hit}
        ok &= (st == 400 and hit)
    ctx.check("http_400", ok,
              {"variants": rows,
               "contract": "GET /diagnose?code=（产品真实契约；冻结案例元数据标 POST，"
                           "与 T-07 实现不一致，已按真实契约执行并披露）"}, risk="P1")


def e2e_l6_021(case, ctx):
    rows, ok = {}, True
    for code in case["input"]["codes"]:
        st, html = diagnose(code, timeout=30)
        hit = ("暂不支持" in html and "当前支持" in html)
        rows[code] = {"http_status": st, "out_of_pool_text": hit}
        ok &= (st == 400 and hit)
    ctx.check("http_400", ok, {"codes": rows}, risk="P1")


def _first_report_request(code: str):
    """首次报告请求（含真实网络取数，可能耗时数分钟）。数据暂不可用时限幅重试 1 次。"""
    st, html = diagnose(code)
    if st == 400 and "数据暂时不可用" in html:
        log(f"  {code} 首次取数失败（数据暂时不可用），30s 后重试 1 次")
        time.sleep(30)
        st, html = diagnose(code)
    return st, html


def e2e_l6_023(case, ctx):
    # payload_only_code：首页唯一输入字段 = code；仅携带 code 的请求走通完整链路
    st_i, _hd, index_html_raw = http_get("/", timeout=30)
    index_html = index_html_raw.decode("utf-8", "replace")
    inputs = INDEX_INPUT_RE.findall(index_html)
    st_d, hd_d, body_d = http_get("/diagnose?code=603799", timeout=900)
    diag_html = body_d.decode("utf-8", "replace")
    ctx.check("payload_only_code",
              st_i == 200 and inputs == ["code"] and st_d == 200,
              {"index_inputs": inputs, "diagnose_status": st_d,
               "request_query": "code=603799（唯一字段）"}, risk="P0")
    # no_cookie_no_tracking：无 Set-Cookie / 无 <script> / 无外链资源 / 无跟踪特征
    no_cookie = ("set-cookie" not in hd_d)
    no_script = ("<script" not in index_html.lower()
                 and "<script" not in diag_html.lower())
    no_ext = not re.search(r'(src|href)="https?://', index_html + diag_html)
    no_track = not re.search(r"analytics|gtag|hm\.baidu|cnzz|beacon",
                             index_html + diag_html, re.I)
    ctx.check("no_cookie_no_tracking",
              no_cookie and no_script and no_ext and no_track,
              {"no_set_cookie": no_cookie, "no_script_tag": no_script,
               "no_external_resources": no_ext, "no_tracking_tokens": no_track},
              risk="P0")


def e2e_e2e_001(case, ctx):
    code = case["input"]["code"]                      # 603799
    st, html = _first_report_request(code)
    parsed = parse_report(html)
    text = RB._html_to_text(html)
    ctx.check("chapters_17_complete",
              st == 200 and RB.check_sections(html) == [] and len(RB.HTML_SECTION_KEYS) == 17,
              {"http_status": st, "sections_total": len(RB.HTML_SECTION_KEYS),
               "missing": RB.check_sections(html),
               "observe_date": parsed["observe_date"],
               "pool_expected": case["input"]["expected_pool"],
               "pool_in_report": case["input"]["expected_pool"] in html}, risk="P1")
    ok_color, ev_color = coloring_pairs_ok(parsed)
    ctx.check("coloring_consistent", st == 200 and ok_color, ev_color, risk="P1")
    ok_scan, hits = CM.scan(text)
    ctx.check("forbidden_scan_zero_hit", st == 200 and ok_scan,
              {"hits": hits, "scanner": "compliance.T01（spec impl）"}, risk="P0")
    rc = CM.check_report(text)
    ctx.check("boundary_phrases_present", st == 200 and not rc.missing_phrases,
              {"missing_phrases": rc.missing_phrases,
               "required": CM.REQUIRED_PHRASES}, risk="P1")


def e2e_e2e_002(case, ctx):
    code = case["input"]["code"]                      # 000858
    # ① 温态请求：建立当日报告（池成员面板已在 E2E-001/L6-023 阶段缓存）
    st_w, html_w = _first_report_request(code)
    parsed_w = parse_report(html_w)
    ctx.check("chapters_17_complete",
              st_w == 200 and RB.check_sections(html_w) == [],
              {"http_status": st_w, "missing": RB.check_sections(html_w),
               "S": parsed_w["S"], "H": parsed_w["H"], "rank": parsed_w["rank"]},
              risk="P1")

    # ② 冷跑：备份并删除 000858 当日报告目录 + 当日面板缓存 → 重新请求（目标股真实网络重取）
    report_dir = PROD / "out" / "reports" / code
    backup_dir = OUT / f"backup_reports_{code}"
    panel_today = PROD / "data_cache" / f"panel_{code}_20240901_{TODAY_COMPACT}.parquet"
    cold_removed = {"report_dir": report_dir.exists(), "panel_today": panel_today.exists()}
    if report_dir.exists():
        shutil.move(str(report_dir), str(backup_dir))
    panel_backup = None
    if panel_today.exists():
        panel_backup = OUT / f"backup_panel_{code}_{TODAY_COMPACT}.parquet"
        shutil.move(str(panel_today), str(panel_backup))
    t0 = time.time()
    st_c, html_c = _first_report_request(code)
    cold_secs = round(time.time() - t0, 1)
    parsed_c = parse_report(html_c)
    same_keys = (
        parsed_c["S"] is not None and parsed_w["S"] is not None
        and abs(parsed_c["S"] - parsed_w["S"]) <= 0.5
        and parsed_c["H"] is not None and parsed_w["H"] is not None
        and abs(parsed_c["H"] - parsed_w["H"]) <= 0.5
        and parsed_c["rank"] == parsed_w["rank"]
        and parsed_c["review_date"] == parsed_w["review_date"]
        and parsed_c["observe_date"] == parsed_w["observe_date"])
    ctx.check("cold_run_no_cache",
              st_c == 200 and RB.check_sections(html_c) == [] and same_keys,
              {"removed": cold_removed, "http_status": st_c, "seconds": cold_secs,
               "warm": {"S": parsed_w["S"], "H": parsed_w["H"], "rank": parsed_w["rank"]},
               "cold": {"S": parsed_c["S"], "H": parsed_c["H"], "rank": parsed_c["rank"]},
               "review_date": parsed_c["review_date"],
               "note": "报告缓存+目标股当日面板双删冷跑；同池其余成员走当日缓存"}, risk="P1")

    # ③ 数据新鲜度：冷跑重取面板 = 最新交易日、fetch_date=当日、行数充足
    fresh_ev = {"panel_exists": panel_today.exists()}
    ok_fresh = False
    if panel_today.exists():
        import pandas as pd
        df = pd.read_parquet(panel_today)
        maxd = str(df.index.max().date())
        trade_dates = TC.get_trade_dates()
        latest_trade = max(d for d in trade_dates if d <= date.today())
        fresh_ev.update({"max_date": maxd, "latest_trade_date": str(latest_trade),
                         "fetch_date": str(df["fetch_date"].iloc[0]), "rows": len(df)})
        ok_fresh = (maxd == str(latest_trade)
                    and str(df["fetch_date"].iloc[0]) == TODAY_DASH and len(df) >= 250)
    ctx.check("cold_run_data_freshness", ok_fresh, fresh_ev, risk="P1")

    # ④ docx 与在线版一致性（Gate V 11 项口径）
    st_dl, items = _docx_11_items(code, html_c, parsed_c,
                                  OUT / f"download_{code}_因子诊断报告_合并版.docx")
    ctx.check("docx_online_consistency",
              st_dl == 200 and all(items.values()) and len(items) == 11,
              {"http_status": st_dl, "items": items,
               "docx_saved": f"download_{code}_因子诊断报告_合并版.docx"}, risk="P1")


def e2e_pool_report(case, ctx):
    """E2E-003a/b（demo 支持池：真实报告）与 E2E-004a/b、E2E-005a/b（demo 范围外池：透明 400）。"""
    code = case["input"]["code"]
    pool = case["input"]["expected_pool"]
    st, html = _first_report_request(code)
    report_dir = PROD / "out" / "reports" / code
    if st == 200:                                     # demo 支持池（沪深300/中证500）
        parsed = parse_report(html)
        text = RB._html_to_text(html)
        ok = (pool in html and RB.check_sections(html) == []
              and CM.check_report(text).passed and report_dir.exists())
        ctx.check("report_e2e", ok,
                  {"http_status": st, "pool": pool, "pool_in_report": pool in html,
                   "missing_sections": RB.check_sections(html),
                   "S": parsed["S"], "H": parsed["H"], "rank": parsed["rank"],
                   "report_generated": report_dir.exists()}, risk="P1")
    else:                                             # demo 范围外池（中证1000/中证2000）
        ok = (st == 400 and "演示模式" in html and pool in html
              and "完整版支持" in html and not report_dir.exists())
        ctx.check("report_e2e", ok,
                  {"http_status": st, "pool": pool, "pool_in_response": pool in html,
                   "demo_range_notice": "演示模式" in html,
                   "report_generated": False,
                   "note": "demo 部署配置下的正确契约行为（400+池归属+透明提示，"
                           "expected_output.report_generated=null）；full 模式报告生成"
                           "链路（该池全量成分预热）属 M2 范围，本轮未覆盖"}, risk="P1")


def e2e_l2_008(case, ctx):
    """取数防泄漏护栏：以 as_of=2026-06-30 为结束日真实取数，面板不得含 as_of 之后数据。"""
    as_of = case["input"]["as_of_truncate"]           # 2026-06-30
    end = as_of.replace("-", "")
    try:
        df, meta = DF.fetch_panel("603799", "20240901", end, use_cache=True)
        maxd = df.index.max().date()
        ok = (maxd <= date.fromisoformat(as_of) and len(df) > 0)
        ctx.check("data_fetcher_asof_guard", ok,
                  {"as_of": as_of, "max_date": str(maxd), "rows": len(df),
                   "from_cache": meta["from_cache"], "source": meta["source"],
                   "note": "真实网络取数（新浪主源）；end=as_of 时返回面板最大日期 ≤ as_of"},
                  risk="P0")
    except Exception as exc:  # noqa: BLE001
        ctx.check("data_fetcher_asof_guard", None, None, risk="P0",
                  not_run=f"网络取数失败，护栏行为无法验证：{exc!r}"[:160])


def e2e_l1_029(case, ctx):
    """过期数据拒用：将当日面板缓存 fetch_date 篡改为过期日期 → fetch_panel 必须拒用并重新取数。"""
    code = "000021"                                  # E2E-003b 已取数，面板存在
    panel = PROD / "data_cache" / f"panel_{code}_20240901_{TODAY_COMPACT}.parquet"
    if not panel.exists():
        ctx.check("stale_data_rejected", None, None, risk="P1",
                  not_run=f"当日面板 {panel.name} 不存在（前置 E2E-003b 未产生缓存）")
        return
    import pandas as pd
    backup = OUT / f"backup_panel_{code}_stale_test.parquet"
    shutil.copy2(str(panel), str(backup))
    df = pd.read_parquet(panel)
    orig_max = df.index.max()
    df["fetch_date"] = "2000-01-01"                  # 人为过期
    df.to_parquet(panel)
    try:
        df2, meta = DF.fetch_panel(code, "20240901", TODAY_COMPACT, use_cache=True)
        ok = (meta["from_cache"] is False
              and str(df2["fetch_date"].iloc[0]) == TODAY_DASH
              and df2.index.max() == orig_max)
        ctx.check("stale_data_rejected", ok,
                  {"stale_fetch_date_set": "2000-01-01",
                   "from_cache": meta["from_cache"],
                   "refetched_fetch_date": str(df2["fetch_date"].iloc[0]),
                   "data_unchanged": df2.index.max() == orig_max,
                   "note": "app 层缓存有效性判据 fetch_date==today；过期缓存被拒用并真实重取"},
                  risk="P1")
    except Exception as exc:  # noqa: BLE001
        shutil.copy2(str(backup), str(panel))        # 还原，避免残留过期缓存
        ctx.check("stale_data_rejected", None, None, risk="P1",
                  not_run=f"网络重取失败，拒用行为已发生但无法完成验证：{exc!r}"[:160])


def _docx_11_items(code: str, html: str, parsed: dict, docx_path: Path):
    """Gate V 11 项在线/docx 一致性比对（真实 /download）。返回 (http_status, items)。"""
    q = urllib.parse.urlencode({"code": code})
    st_dl, _hd, body = http_get(f"/download?{q}", timeout=300)
    docx_path.write_bytes(body)
    online_text = RB._html_to_text(html)
    items = {}
    if st_dl == 200:
        dtext = docx_text(docx_path)
        s_dx = re.search(r"综合分 S=([\d.]+)", dtext)
        h_dx = re.search(r"H=([\d.]+)", dtext)
        r_dx = re.search(r"同池排名 (\d+)/(\d+)", dtext)
        # 数据源名称含空格（akshare 东财源 000858 → "五 粮 液"）：在线与 docx 同源一致，
        # 名称比对按去空格归一化；该现象作为发现披露（见报告）
        d_norm = dtext.replace(" ", "").replace("\u3000", "")
        o_norm = online_text.replace(" ", "").replace("\u3000", "")
        items["名称+代码"] = (code in dtext
                              and "五粮液" in d_norm
                              and code in online_text
                              and "五粮液" in o_norm)
        items["S值"] = bool(s_dx and parsed["S"] is not None
                            and abs(float(s_dx.group(1)) - parsed["S"]) <= 0.05)
        items["H值"] = bool(h_dx and parsed["H"] is not None
                            and abs(float(h_dx.group(1)) - parsed["H"]) <= 0.05)
        items["同池标注"] = "沪深300" in dtext and "沪深300" in online_text
        items["排名"] = bool(r_dx and parsed["rank"] is not None
                             and (int(r_dx.group(1)), int(r_dx.group(2))) == parsed["rank"])
        rv_dx = re.search(r"复核日[：:]\s*(\d{4}-\d{2}-\d{2})", dtext)
        items["复核日"] = bool(rv_dx and rv_dx.group(1) == parsed["review_date"])
        ob_dx = re.search(r"观察日（出分日）：(\d{4}-\d{2}-\d{2})", dtext)
        items["观察日"] = bool(ob_dx and ob_dx.group(1) == parsed["observe_date"])
        fids = [f"F{i}" for i in range(1, 19)]
        on_fids = {row[0] for row in parsed["factors"]}
        items["18因子分齐全"] = (all(f in dtext for f in fids)
                                and on_fids == set(fids) and len(parsed["factors"]) == 18)
        items["四侧面均值(4项)"] = all(s in dtext for s in ("估值特征", "价格行为", "交易行为", "风险特征"))
        items["章节:怎么读这个分数"] = "怎么读这个分数" in dtext
        items["章节:风险与边界"] = "风险与边界" in dtext
    return st_dl, items


def e2e_l5_031(case, ctx):
    """L5-031：在线报告与 DOCX 关键数据一致（AC-011，Gate V 11 项口径，regression）。"""
    code = case["input"]["code"]                       # 000858（当日报告已由 E2E-002 建立）
    st, html = _first_report_request(code)
    parsed = parse_report(html)
    st_dl, items = _docx_11_items(code, html, parsed,
                                  OUT / f"download_{code}_L5-031.docx")
    ctx.check("online_docx_consistency",
              st == 200 and st_dl == 200 and all(items.values()) and len(items) == 11,
              {"http_status": st_dl, "items": items,
               "comparison_scope": case["input"]["comparison_scope"]}, risk="P1")


def e2e_l6_038(case, ctx):
    """L6-038：限流 429/服务不崩（E-10）。cross_day_reset 需跨自然日窗口，本轮不可执行。"""
    statuses = []
    for _ in range(60):
        st, _html = diagnose("abc12", timeout=15)      # 低成本非法代码：400 且计数
        statuses.append(st)
        if st == 429:
            break
    st_home, _h, _b = http_get("/", timeout=15)
    ok = (429 in statuses and st_home == 200
          and all(s in (400, 429) for s in statuses))
    ctx.check("rate_limit_429", ok,
              {"requests_sent": len(statuses),
               "statuses_head": statuses[:3], "statuses_tail": statuses[-3:],
               "first_429_at": (statuses.index(429) + 1) if 429 in statuses else None,
               "server_alive_after": st_home == 200,
               "note": "真实服务压测：耗尽单 IP 当日 50 次配额后触发 429（E-10）"}, risk="P2")
    ctx.check("cross_day_reset", None, None, risk="P2",
              not_run="需跨自然日窗口验证配额重置（真实日历翻转），单日内不可执行；"
                      "T-08 单测与 Gate V B 部分已覆盖 rollover 语义")


E2E_HANDLERS = {
    "L6-020": e2e_l6_020, "L6-021": e2e_l6_021, "L6-023": e2e_l6_023,
    "E2E-001": e2e_e2e_001, "E2E-002": e2e_e2e_002,
    "E2E-003a": e2e_pool_report, "E2E-003b": e2e_pool_report,
    "E2E-004a": e2e_pool_report, "E2E-004b": e2e_pool_report,
    "E2E-005a": e2e_pool_report, "E2E-005b": e2e_pool_report,
    "L2-008": e2e_l2_008, "L1-029": e2e_l1_029,
    "L5-031": e2e_l5_031, "L6-038": e2e_l6_038,
}


# --------------------------------------------------------------- 与离线运行合并
def latest_offline_results():
    root = HERE / "results"
    runs = sorted(p for p in root.glob("run_*/results.json"))
    if not runs:
        return None, None
    p = runs[-1]
    return p.parent.name, json.loads(p.read_text(encoding="utf-8"))


def combined_case_status(offline_case: dict | None, e2e_assertions: list[dict],
                         frozen: bool = True):
    """合并口径：离线断言 + E2E 断言（同名断言以 E2E 实测覆盖 NOT_RUN 项）后重新五状态聚合。

    未冻结案例（离线 BLOCKED）保持 BLOCKED——合并不得 launder 案例的冻结状态。
    """
    if not frozen:
        return "BLOCKED", None, list(offline_case["detail"]) if offline_case else []
    merged = list(offline_case["detail"]) if offline_case else []
    e2e_names = {a["assertion"] for a in e2e_assertions}
    merged = [a for a in merged if a["assertion"] not in e2e_names] + list(e2e_assertions)
    st, agg = aggregate_status(merged, frozen=True)
    return st, agg, merged


# --------------------------------------------------------------- 主流程
def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    # ---- 启动门禁（与 run_evals 相同）
    try:
        cases = load_cases()
        manifest = json.loads((DATASET / "frozen_manifest.json").read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        log(f"[门禁失败] 数据集/冻结清单不可用：{exc!r}")
        return 2
    errs = validate_freeze_gate(cases, manifest, DATASET)
    if errs:
        for e in errs:
            log(f"[门禁失败] {e}")
        return 2
    log(f"E2E Runner 启动 | run_id={RUN_TS} | 冻结案例 {len(cases)} | "
        f"manifest v{manifest['manifest_version']} | PEG {manifest['peg_report_overall']}")

    results = {"run_id": RUN_TS, "mode": "e2e_real_web",
               "started_at": datetime.now(timezone.utc).isoformat(),
               "server": {"url": BASE, "config": "SDD_POOL_MODE=demo, SDD_DAILY_LIMIT=50 "
                         "(与 deploy/start_server.bat 一致)",
                          "entry": "uvicorn src.app:app"},
               "cases": {}}
    proc = None
    try:
        proc = start_server()
        t_start = time.time()
        for cid in sorted(E2E_HANDLERS):
            case = cases[cid]
            ctx = Ctx()
            try:
                E2E_HANDLERS[cid](case, ctx)
            except Exception as exc:  # noqa: BLE001
                import traceback
                ctx.check("runner_internal_error", False,
                          {"exception": repr(exc),
                           "traceback": traceback.format_exc()[-1500:]}, risk="P0")
            case_status, agg = aggregate_status(ctx.results, frozen=True)
            n_pass = sum(1 for r in ctx.results if r["status"] == "PASS")
            n_fail = sum(1 for r in ctx.results if r["status"] == "FAIL")
            n_nr = sum(1 for r in ctx.results if r["status"] == "NOT_RUN")
            results["cases"][cid] = {
                "status": case_status, "risk_level": case["risk_level"],
                "split": case["split"], "layer": case["layer"],
                "assertions": {"pass": n_pass, "fail": n_fail, "not_run": n_nr},
                "aggregation": {"required_pass": agg["required"]["PASS"],
                                "required_fail": agg["required"]["FAIL"],
                                "required_not_run": agg["required"]["NOT_RUN"],
                                "optional_not_run": agg["optional"]["NOT_RUN"],
                                "optional_fail": agg["optional"]["FAIL"]},
                "detail": ctx.results}
            log(f"  {cid:<10} {case_status:<8} (P{n_pass}/F{n_fail}/NR{n_nr}) "
                f"risk={case['risk_level']} elapsed={time.time()-t_start:.0f}s")
    finally:
        if proc is not None:
            proc.terminate()
            try:
                proc.wait(timeout=10)
            except Exception:  # noqa: BLE001
                proc.kill()
            log("服务进程已停止")

    # ---- E2E 范围汇总
    states = ("PASS", "FAIL", "PARTIAL", "BLOCKED", "NOT_RUN")
    summary = {s: sum(1 for c in results["cases"].values() if c["status"] == s)
               for s in states}
    a_pass = sum(c["assertions"]["pass"] for c in results["cases"].values())
    a_fail = sum(c["assertions"]["fail"] for c in results["cases"].values())
    a_nr = sum(c["assertions"]["not_run"] for c in results["cases"].values())
    results["summary"] = {**summary, "assertions": {"pass": a_pass, "fail": a_fail,
                                                    "not_run": a_nr},
                          "exit_code": exit_code_from_summary(summary)}

    # ---- 与最近一次离线运行合并（案例级合并状态）
    off_run, off = latest_offline_results()
    combined = {}
    if off:
        for cid, oc in off["cases"].items():
            ec = results["cases"].get(cid)
            e2e_detail = ec["detail"] if ec else []
            frozen = manifest["cases"].get(cid, {}).get("status") == "FROZEN"
            st, agg, merged = combined_case_status(oc, e2e_detail, frozen=frozen)
            combined[cid] = {"offline_status": oc["status"],
                             "e2e_status": ec["status"] if ec else None,
                             "combined_status": st,
                             "risk_level": oc["risk_level"], "split": oc["split"]}
        c_summary = {s: sum(1 for c in combined.values() if c["combined_status"] == s)
                     for s in states}
        results["combined_with_offline"] = {
            "offline_run_id": off_run, "cases": combined, "summary": c_summary,
            "exit_code": exit_code_from_summary(c_summary)}
    log(f"E2E 汇总：PASS={summary['PASS']} FAIL={summary['FAIL']} PARTIAL={summary['PARTIAL']} "
        f"NOT_RUN={summary['NOT_RUN']} | 断言 P{a_pass}/F{a_fail}/NR{a_nr}")
    if off_run:
        cs = results["combined_with_offline"]["summary"]
        log(f"合并（离线 {off_run} + 本轮 E2E）：PASS={cs['PASS']} FAIL={cs['FAIL']} "
            f"PARTIAL={cs['PARTIAL']} BLOCKED={cs['BLOCKED']} NOT_RUN={cs['NOT_RUN']} "
            f"| 退出码 {results['combined_with_offline']['exit_code']}")

    (OUT / "results.json").write_text(json.dumps(results, ensure_ascii=False, indent=1),
                                      encoding="utf-8")
    (OUT / "eval_run.log").write_text("\n".join(LOG_LINES) + "\n", encoding="utf-8")

    # ---- Markdown 摘要
    md = [f"# E2E 真实 Web 端到端执行结果（run_{RUN_TS}）", "",
          f"- 服务：{BASE}（uvicorn src.app:app，demo 配置，真实进程+真实 HTTP+真实网络取数）",
          f"- E2E 范围：{summary['PASS']} PASS / {summary['FAIL']} FAIL / "
          f"{summary['PARTIAL']} PARTIAL / {summary['NOT_RUN']} NOT_RUN（断言 P{a_pass}/F{a_fail}/NR{a_nr}）",
          "", "| case | E2E 状态 | 风险 | 断言 | 关键证据 |", "|---|---|---|---|---|"]
    for cid, c in results["cases"].items():
        key = ""
        for r in c["detail"]:
            if r["status"] == "FAIL":
                key = f"FAIL@{r['assertion']}: {str(r.get('evidence'))[:70]}"
                break
        md.append(f"| {cid} | {c['status']} | {c['risk_level']} | "
                  f"{c['assertions']['pass']}/{c['assertions']['fail']}/{c['assertions']['not_run']} | {key} |")
    if off_run:
        md += ["", f"## 与离线运行合并（{off_run}）", "",
               f"- 合并五状态：PASS={c_summary['PASS']} / FAIL={c_summary['FAIL']} / "
               f"PARTIAL={c_summary['PARTIAL']} / BLOCKED={c_summary['BLOCKED']} / "
               f"NOT_RUN={c_summary['NOT_RUN']}",
               "", "| case | 离线 | E2E | 合并 | 风险 |", "|---|---|---|---|---|"]
        for cid, c in combined.items():
            if c["offline_status"] != c["combined_status"] or c["e2e_status"]:
                md.append(f"| {cid} | {c['offline_status']} | {c['e2e_status'] or '-'} | "
                          f"**{c['combined_status']}** | {c['risk_level']} |")
    (OUT / "results.md").write_text("\n".join(md) + "\n", encoding="utf-8")
    log(f"结果已写入 {OUT}")
    return results["summary"]["exit_code"]


if __name__ == "__main__":
    sys.exit(main())
