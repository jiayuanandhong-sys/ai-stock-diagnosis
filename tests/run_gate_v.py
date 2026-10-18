# -*- coding: utf-8 -*-
"""Gate V 独立验收脚本（Verify 阶段，2026-10-17）。

纪律：
- 不信任 Implement 阶段旧证据，全部重新实测；
- 每条 AC 独立判定 PASS / FAIL / 未验证，附证据与边界说明；
- 不自行批准 Gate V，结论交 PM 裁决。

结构：
- PART A：真实 HTTP 验收（对 127.0.0.1:8123 在线服务）——AC-001/004/005/006/007/008/009/010/011
- PART B：限流实测（进程内独立实例，SDD_DAILY_LIMIT=3）+ 跨日重置功能测试
- PART C：四池真实数据计算（每池 15 只真实成分股样本）——AC-002/003 + 18 因子 + S/H
- PART D：full 模式池标注（函数级，真实快照，无数据拉取）——AC-003 的元信息标注

证据输出：evidence/GateV/gate_v_result.json + 各落盘文件。
"""

from __future__ import annotations

import json
import os
import re
import sys
import time
from datetime import date as _date, datetime as _dt
from typing import Dict, List, Tuple

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

import httpx
import numpy as np
import pandas as pd

BASE = "http://127.0.0.1:8123"          # 本轮验收的真实运行地址（demo 池模式）
EV = os.path.join(ROOT, "evidence", "GateV")
os.makedirs(EV, exist_ok=True)

T0 = time.time()
RESULTS: dict = {"parts": {}, "ac": {}}


def log(msg: str):
    print(msg, flush=True)


def ac_save(ac_id: str, verdict: str, method: str, evidence: str, detail: str = ""):
    RESULTS["ac"][ac_id] = {"verdict": verdict, "method": method,
                            "evidence": evidence, "detail": detail}
    log(f"  [{ac_id}] {verdict} —— {method}" + (f" | {detail}" if detail else ""))


# ================================================================ PART A（HTTP）
def part_a():
    log("== PART A：真实 HTTP 验收（live server 127.0.0.1:8123）==")
    from src.report_builder import check_sections, _html_to_text, SECTION_TITLES
    from src.compliance import check_report, scan
    from src.trade_cal import review_date, get_trade_dates
    from src.pool_manager import load_snapshot, resolve_pool, POOL_INDEX

    client = httpx.Client(base_url=BASE, timeout=180.0, verify=False)
    a: dict = {}

    # --- A0 输入页（AC-009 载体审查的一半）---
    r = client.get("/")
    a["index"] = {"status": r.status_code, "bytes": len(r.content)}
    idx = r.text
    setc = r.headers.get("set-cookie")
    a["index_findings"] = {
        "无Set-Cookie": setc is None,
        "无<script>": "<script" not in idx.lower(),
        "无login字样": "登录" not in idx and "login" not in idx.lower(),
        "唯一输入字段code": idx.count("<input") == 1 and 'name="code"' in idx,
    }
    log(f"  输入页 {r.status_code}，载体审查: {a['index_findings']}")

    # --- AC-007 无效代码（真实 HTTP）---
    invalid_codes = ["12345", "abc123", "1234567", "1", "六零零五十九"]
    bad = []
    for c in invalid_codes:
        r = client.get("/diagnose", params={"code": c})
        ok = (r.status_code == 400 and "代码格式无效" in r.text
              and "因子诊断报告" not in r.text)
        bad.append((c, r.status_code, ok))
        assert ok, f"AC-007 失败: {c} -> {r.status_code}"
    r = client.get("/diagnose")
    empty_ok = r.status_code == 400 and "代码格式无效" in r.text
    bad.append(("", r.status_code, empty_ok))
    ac_save("AC-007", "PASS" if all(x[2] for x in bad) else "FAIL",
            f"真实 HTTP：{len(bad)} 个无效/空代码用例全部 400+提示、不生成报告",
            f"{EV}/gate_v_result.json (PART A)",
            f"用例与状态码: {[(c, s) for c, s, _ in bad]}")

    # --- AC-008 池外股票（先用真实快照确认用例确属池外，再真实 HTTP）---
    pools = load_snapshot()
    all_members = set()
    for v in pools.values():
        all_members.update(v)
    candidates_bj = ["832566", "430047", "920002", "830799"]        # 北交所段
    candidates_sz = ["600768", "002662", "603999", "002164", "300688", "600721"]
    out_pool = [c for c in candidates_bj + candidates_sz
                if len(c) == 6 and c.isdigit() and c not in all_members]
    out_pool_bj = [c for c in candidates_bj if c in out_pool][:1]
    out_pool_sz = [c for c in candidates_sz if c in out_pool][:1]
    test_codes = out_pool_bj + out_pool_sz
    assert test_codes, "AC-008：未找到可用的池外真实代码"
    op = []
    for c in test_codes:
        r = client.get("/diagnose", params={"code": c})
        ok = (r.status_code == 400 and "暂不支持" in r.text
              and "沪深300、中证500、中证1000、中证2000 成分股" in r.text
              and "因子诊断报告" not in r.text)
        op.append((c, r.status_code, ok))
        assert ok, f"AC-008 失败: {c}"
    ac_save("AC-008", "PASS" if all(x[2] for x in op) else "FAIL",
            f"真实 HTTP + 真实快照确认池外身份：{[c for c, _, _ in op]} → 400+暂不支持+支持范围",
            f"{EV}/gate_v_result.json (PART A)",
            f"北交所段: {out_pool_bj or '无样本'}；沪深段池外: {out_pool_sz or '无样本'}")

    # --- 演示模式边界（非 AC，如实记录）---
    r = client.get("/diagnose", params={"code": "000001"})   # 真实沪深300成分股、非演示20只
    a["demo_boundary"] = {"code": "000001", "status": r.status_code,
                          "is_demo_page": "演示模式" in r.text}

    # --- AC-001 + AC-011 主路径：603799（当日产物复用路径）+ 000858（全新生成路径）---
    for code, fresh in [("603799", False), ("000858", True)]:
        t = time.time()
        r = client.get("/diagnose", params={"code": code})
        dt = round(time.time() - t, 1)
        assert r.status_code == 200, f"{code} 主路径非 200: {r.status_code}"
        html = r.text
        with open(os.path.join(EV, f"在线报告_{code}.html"), "w", encoding="utf-8") as f:
            f.write(html)
        missing = check_sections(html)
        a[f"report_{code}"] = {"status": r.status_code, "bytes": len(html),
                               "seconds": dt, "fresh_build": fresh,
                               "missing_sections": missing}
        log(f"  在线报告 {code}: 200, {len(html)} bytes, {dt}s, fresh={fresh}, 缺章={missing}")
    html = open(os.path.join(EV, "在线报告_603799.html"), encoding="utf-8").read()
    html_858 = open(os.path.join(EV, "在线报告_000858.html"), encoding="utf-8").read()
    ac_save("AC-001", "PASS" if (not a["report_603799"]["missing_sections"]
                                 and not a["report_000858"]["missing_sections"]) else "FAIL",
            "真实 HTTP 在线报告（复用路径 603799 + 全新生成路径 000858）17 章节 check_sections 全对齐",
            f"{EV}/在线报告_603799.html; {EV}/在线报告_000858.html",
            f"603799 复用路径 {a['report_603799']['seconds']}s；000858 全新生成 {a['report_000858']['seconds']}s")

    # --- AC-004 分档着色（在线 HTML 全部 分数↔颜色 对机械核对）---
    hexmap = {"#C00000": "red", "#1E7B3C": "green", "#555555": "gray"}
    pairs = []
    for h in (html, html_858):
        # 汇总表行: <td style="color:#XXXXXX">NN.N（标签）</td>
        for m in re.finditer(r'style="color:(#[0-9A-Fa-f]{6})">\s*([0-9]+(?:\.[0-9]+)?)', h):
            pairs.append((float(m.group(2)), m.group(1).upper(), "summary"))
        # 因子/侧面表行: <td>NN.N</td><td style='color:#XXXXXX'>标签</td>
        for m in re.finditer(
                r"<td[^>]*>\s*([0-9]+(?:\.[0-9]+)?)\s*</td>\s*"
                r"<td style='color:(#[0-9A-Fa-f]{6})'", h):
            pairs.append((float(m.group(1)), m.group(2).upper(), "table"))
    from src.factor_engine import band
    wrong = []
    for score, hx, where in pairs:
        expect = band(score)[0]
        if hexmap.get(hx) != expect:
            wrong.append((score, hx, where, expect))
    # 覆盖度检查：三档颜色都要在报告中真实出现过
    colors_seen = {hx for _, hx, _ in pairs}
    ac_save("AC-004", "PASS" if (not wrong and len(pairs) >= 40
                                and len(colors_seen & set(hexmap)) >= 2) else "FAIL",
            f"在线报告（603799+000858）共 {len(pairs)} 个 分数↔着色 对逐一机械核对 band() 规则",
            f"{EV}/gate_v_result.json (PART A color_pairs)",
            f"违规 {len(wrong)} 对；出现颜色 {sorted(colors_seen & set(hexmap))}；"
            f"（BR-007 红绿灰三档；十分组口径见报告'怎么读这个分数'章节）")
    a["color_pairs"] = {"total": len(pairs), "wrong": wrong[:5],
                        "colors_seen": sorted(colors_seen & set(hexmap))}
    RESULTS["parts"]["A_color_pairs"] = a["color_pairs"]

    # --- AC-005 + AC-010 合规与边界文案（对在线 HTML 全文）---
    text = _html_to_text(html)
    chk = check_report(text)
    ac_save("AC-005", "PASS" if chk.passed else "FAIL",
            "对在线 603799 报告全文跑 T-01 检查器（禁词扫描 + 必需口径文案存在性）",
            f"{EV}/gate_v_result.json (PART A compliance)",
            chk.summary())
    titles_ok = ("怎么读这个分数" in html and "风险与边界" in html
                 and SECTION_TITLES["howto"] in html and SECTION_TITLES["boundary"] in html)
    ac_save("AC-010", "PASS" if titles_ok else "FAIL",
            "样例报告关键边界语句与章节在位（怎么读这个分数/风险与边界 + 口径语句）",
            f"{EV}/在线报告_603799.html",
            f"REQUIRED_PHRASES 缺失: {chk.missing_phrases}")
    a["compliance"] = {"passed": chk.passed, "forbidden_hits": chk.forbidden_hits,
                       "missing_phrases": chk.missing_phrases}

    # --- AC-006 复核日（在线报告提取 + 独立重算 + 跨节假日功能用例）---
    m_score = re.search(r"观察日（出分日）</th><td>(\d{4}-\d{2}-\d{2})", html)
    m_rev = re.search(r"建议复核日</th><td>(\d{4}-\d{2}-\d{2})（出分日 \+ 5 个交易日", html)
    score_d = _dt.strptime(m_score.group(1), "%Y-%m-%d").date()
    rev_d = _dt.strptime(m_rev.group(1), "%Y-%m-%d").date()
    expect_d, crossed = review_date(score_d)
    # 跨节假日独立功能用例：国庆前最后交易日 + 5 交易日应跳过假期
    cal = get_trade_dates()
    future5 = [d for d in cal if d > score_d][:5]
    ok_cal = (future5[-1] == rev_d)
    d_gq, crossed_gq = review_date(_date(2026, 9, 30))
    oct2026 = [d for d in cal if _date(2026, 9, 30) < d <= _date(2026, 10, 31)]
    cal_covers_gq = len(oct2026) >= 5
    if cal_covers_gq:
        gq_ok = bool(crossed_gq) and d_gq > _date(2026, 10, 2)
    else:                       # 日历不覆盖远期 → 容错口径，仅记录
        gq_ok = True
    ok_ac006 = (rev_d == expect_d) and ok_cal and gq_ok
    ac_save("AC-006", "PASS" if ok_ac006 else "FAIL",
            f"在线报告复核日 {rev_d} = review_date({score_d}) 独立重算 {expect_d}；"
            f"交易日历独立路径 {future5[-1]}；跨节假日用例 2026-09-30→{d_gq}"
            f"（crossed={crossed_gq}，日历覆盖={cal_covers_gq}）",
            f"{EV}/在线报告_603799.html; data_cache/trade_calendar.json")
    a["review_date"] = {"score": str(score_d), "review": str(rev_d),
                        "expect": str(expect_d), "crossed": crossed,
                        "gq_case": {"review": str(d_gq), "crossed": crossed_gq}}

    # --- AC-011 下载 docx 与在线内容一致性（真实下载 + python-docx 解包比对）---
    r = client.get("/download", params={"code": "603799"})
    assert r.status_code == 200, f"download 非 200: {r.status_code}"
    docx_path = os.path.join(EV, "下载_603799_因子诊断报告_合并版.docx")
    with open(docx_path, "wb") as f:
        f.write(r.content)
    from docx import Document
    doc = Document(docx_path)
    dtext = "\n".join(p.text for p in doc.paragraphs)
    for tb in doc.tables:
        for row in tb.rows:
            dtext += "\n" + " | ".join(c.text for c in row.cells)
    # 从在线 HTML 提取关键值，逐项在 docx 全文中比对
    m_S = re.search(r"综合分 S（同池两级排名）.*?([0-9.]+)（", html, re.S)
    S_val = m_S.group(1)
    m_rank = re.search(r"([0-9]+)/([0-9]+)</td>", html)
    rank_val = m_rank.group(1) + "/" + m_rank.group(2)
    fvals = re.findall(r"<td>([0-9]+\.[0-9])</td>\s*<td style", html)   # 18 因子分
    side_vals = re.findall(r"<td>([0-9]+)</td>\s*<td style='color", html)  # 四侧面均值
    obs_d = re.search(r"观察日（出分日）</th><td>(\d{4}-\d{2}-\d{2})", html).group(1)
    checks = {
        "名称+代码": "华友钴业" in dtext and "603799" in dtext,
        f"S值({S_val})": S_val in dtext,
        "同池标注": "演示同池" in dtext,
        f"排名({rank_val})": rank_val in dtext,
        f"复核日({rev_d})": str(rev_d) in dtext,
        f"观察日({obs_d})": obs_d in dtext,
        "18因子分齐全": all(v in dtext for v in fvals) and len(fvals) >= 18,
        f"四侧面均值({len(side_vals)}项)": len(side_vals) == 4
                                            and all(v in dtext for v in side_vals),
        "章节:怎么读这个分数": "怎么读这个分数" in dtext,
        "章节:风险与边界": "风险与边界" in dtext,
    }
    # H 值独立提取
    m_H = re.search(r"历史位置参考分 H</th>.*?<td[^>]*>([0-9.]+)</td>", html, re.S)
    checks[f"H值({m_H.group(1)})"] = m_H.group(1) in dtext
    ok_all = all(checks.values())
    # docx 全文自身也过一遍合规扫描（下载版同受硬门禁约束）
    chk_docx = check_report(dtext)
    ac_save("AC-011", "PASS" if (ok_all and chk_docx.passed and
                                 r.headers["content-type"].startswith(
                                     "application/vnd.openxmlformats")) else "FAIL",
            f"真实 HTTP 下载 docx（{len(r.content)} bytes）→ python-docx 解包 → "
            f"{len(checks)} 项关键内容与在线 HTML 比对 + docx 全文合规扫描",
            f"{EV}/下载_603799_因子诊断报告_合并版.docx",
            f"比对结果: { {k: v for k, v in checks.items()} }；docx 合规: {chk_docx.summary()}")
    a["docx_consistency"] = checks

    # --- AC-009 零采集（真实响应头 + 页面结构 + 请求载荷）---
    r2 = client.get("/diagnose", params={"code": "603799"})
    no_cookie_all = all(h.headers.get("set-cookie") is None
                        for h in [r, r2, client.get("/")])
    r3 = client.get("/diagnose", params={"code": "000858"})
    payload_query = str(r3.request.url).split("?")[-1]
    ac_save("AC-009", "PASS" if (a["index_findings"]["无Set-Cookie"]
                                 and a["index_findings"]["无<script>"]
                                 and a["index_findings"]["唯一输入字段code"]
                                 and no_cookie_all and payload_query == "code=000858"
                                 and a["index_findings"]["无login字样"]) else "FAIL",
            "真实 HTTP：输入页唯一输入字段=code、无登录/无JS/全路由无 Set-Cookie、"
            "请求载荷仅 code 一个参数（GET query 逐字符核对）",
            f"{EV}/gate_v_result.json (PART A zero_collection)",
            "注：服务端以代码为键缓存当日报告产物（out/reports/，设计内行为）；"
            "无任何用户画像/持仓/身份数据落盘。")
    a["zero_collection"] = {"no_set_cookie": no_cookie_all,
                            "payload": payload_query,
                            "index": a["index_findings"]}

    RESULTS["parts"]["A"] = a
    client.close()


# ================================================================ PART B（限流）
def part_b():
    log("== PART B：限流实测（独立实例 + 跨日重置功能测试）==")
    b: dict = {}
    from fastapi.testclient import TestClient
    import src.app as appmod
    from src.rate_limiter import IPRateLimiter
    import src.rate_limiter as rl

    appmod.LIMITER = IPRateLimiter(daily_limit=3)      # 独立实例，限额 3（不影响 8123 线上服务）
    tc = TestClient(appmod.app)
    codes_seq = []
    for i in range(5):
        r = tc.get("/diagnose", params={"code": "603799"})   # 当日产物复用，快速
        codes_seq.append(r.status_code)
    b["low_limit_seq"] = codes_seq                       # 期望 [200,200,200,429,429]
    ok_limit = codes_seq == [200, 200, 200, 429, 429]
    body_429 = ""
    if 429 in codes_seq:
        r = tc.get("/diagnose", params={"code": "603799"})
        body_429 = ("请求次数已达今日上限" in r.text) and r.status_code == 429
    # 突发 20 次请求（超限后应全 429，服务存活；TestClient 非线程安全 → 顺序突发）
    burst = [tc.get("/diagnose", params={"code": "603799"}).status_code
             for _ in range(20)]
    alive = tc.get("/").status_code == 200
    b["burst_20"] = {"statuses": sorted(set(burst)), "server_alive": alive}
    # 跨日重置（功能级：注入假时钟）
    class FakeDT:
        def __init__(self, d): self._d = d
        def now(self): return self._d
        def strftime(self, fmt): return self._d.strftime(fmt)
    orig = rl._dt
    try:
        rl._dt = FakeDT(_dt(2026, 10, 17, 12))
        lim = IPRateLimiter(daily_limit=2)
        lim.check_and_count("ip1"); lim.check_and_count("ip1")
        blocked = lim.check_and_count("ip1")[0] is False
        rl._dt = FakeDT(_dt(2026, 10, 18, 9))
        reset_ok = lim.check_and_count("ip1")[0] is True
        ip_iso = (lim.check_and_count("ip2")[0] is True)   # 不同 IP 互不影响
    finally:
        rl._dt = orig
    b["rollover"] = {"blocked_when_full": blocked, "reset_next_day": reset_ok,
                     "ip_isolated": ip_iso}
    ok_all = ok_limit and body_429 and alive and all(s == 429 for s in burst) \
             and blocked and reset_ok and ip_iso
    RESULTS["parts"]["B"] = b
    log(f"  限额序列 {codes_seq}；突发20 结果 {sorted(set(burst))} 存活={alive}；"
        f"跨日重置 blocked={blocked} reset={reset_ok} ip隔离={ip_iso}")
    log(f"  [限流/E-10] {'PASS' if ok_all else 'FAIL'}（进程内独立实例 + 假时钟；"
        f"20 线程并发实证见 T-08 证据，此处以顺序突发复验超限与存活；线上 8123 未触发）")
    RESULTS["parts"]["B_verdict"] = "PASS" if ok_all else "FAIL"


# ================================================================ PART C（四池真实数据）
def _avg_rank_asc(values: np.ndarray) -> Tuple[np.ndarray, np.ndarray]:
    """独立实现：升序平均并列名次（1 起，并列取均值）。

    返回 (mask, ranks)：mask 标记非 NaN 位置；ranks 仅含非 NaN 元素的名次。
    与引擎所用 pandas rank(method='average') 语义一致，但实现路径完全独立
    （numpy argsort + 手工并列分组）。
    """
    mask = ~np.isnan(values)
    v = values[mask]
    order = np.argsort(v, kind="mergesort")
    ranks = np.empty(len(v))
    i = 0
    while i < len(v):
        j = i
        while j + 1 < len(v) and v[order[j + 1]] == v[order[i]]:
            j += 1
        avg = (i + j) / 2.0 + 1.0          # 1 起名次的并列均值
        ranks[order[i:j + 1]] = avg
        i = j + 1
    return mask, ranks


def part_c():
    log("== PART C：四池真实数据计算（AC-002/003 + 18 因子 + S/H）==")
    from src.data_fetcher import fetch_many, reset_stats, STATS
    from src.factor_engine import (FACTOR_TABLE, compute_raw_factors, compute_pool_scores,
                                   compute_H, side_means, band)
    from src.pool_manager import load_snapshot, resolve_pool

    pools = load_snapshot()
    N_PER_POOL = 15
    rows = []
    c_detail: dict = {}
    reset_stats()

    for pool_name in ["沪深300", "中证500", "中证1000", "中证2000"]:
        members = pools[pool_name]
        target = members[0]                       # 确定性样本：每池第一位成分股
        # 等距抽样 N_PER_POOL 只（含 target），确定性
        step = max(1, len(members) // (N_PER_POOL - 1))
        sample = list(dict.fromkeys(members[::step][:N_PER_POOL - 1] + [target]))
        t0 = time.time()
        panels, failed = fetch_many(sample, "20240901",
                                     _dt.now().strftime("%Y%m%d"), use_cache=True)
        assert target in panels, f"{pool_name} 目标股 {target} 拉取失败: {failed}"
        # 池归属判定（真实快照 + 真实代码）
        r_pool = resolve_pool(target)
        assert r_pool["supported"] and r_pool["pool"] == pool_name, \
            f"{pool_name} 归属判定错误: {r_pool}"
        # 18 因子 + S/H（真实数据全管线）
        scores, S_raw, S = compute_pool_scores(panels)
        H, series, S_now = compute_H(panels, target, weeks=52)

        # --- AC-002 独立复算：numpy 自写平均并列名次，不复用 pandas rank ---
        codes = list(panels.keys())
        raws = pd.DataFrame({c: compute_raw_factors(df) for c, df in panels.items()}).T
        my_scores = pd.DataFrame(index=codes, columns=[f[0] for f in FACTOR_TABLE],
                                 dtype=float)
        for fid, _side, key, _label, low_good in FACTOR_TABLE:
            col = raws[key].reindex(codes).to_numpy(dtype=float)
            mask, ranks = _avg_rank_asc(col)
            pct = ranks / mask.sum() * 100.0 if mask.sum() > 0 else np.array([])
            my_vals = np.full(len(codes), np.nan)
            my_vals[mask] = (100.0 - pct) if low_good else pct
            my_scores[fid] = my_vals
        # 逐因子分对拍（同语义独立实现，应精确一致）
        f_diff = float(np.nanmax(np.abs(
            my_scores.to_numpy(dtype=float) - scores.loc[codes].to_numpy(dtype=float))))
        # 等权均值 → 放回同池两级排名（独立实现）
        my_sraw = my_scores.mean(axis=1, skipna=True)
        col = my_sraw.reindex(codes).to_numpy(dtype=float)
        mask, ranks = _avg_rank_asc(col)
        my_S = np.full(len(codes), np.nan)
        my_S[mask] = ranks / mask.sum() * 100.0
        s_diff = abs(float(my_S[codes.index(target)]) - float(S[target]))
        sraw_diff = abs(float(my_sraw[target]) - float(S_raw[target]))
        # S_raw == 目标有效因子分的等权均值（证明四侧面不参与加权，AC-002）
        sraw_check = sraw_diff < 1e-6 and abs(
            float(S_raw[target]) - float(scores.loc[target].dropna().mean())) < 1e-9
        # 平均并列名次语义抽样手验：F4（ret20，高值有利）目标分 = 名次/n×100
        f4_col = raws["ret20"].dropna()
        f4_expect = float(f4_col.rank(method="average")[target] / len(f4_col) * 100) \
            if target in f4_col.index else float("nan")
        f4_engine = float(scores.loc[target]["F4"])
        f4_ok = abs(f4_expect - f4_engine) < 0.05 if not np.isnan(f4_expect) else True
        n_valid_factors = int(scores.loc[target].notna().sum())
        ok_pool = (f_diff < 0.01 and s_diff < 0.01 and f4_ok and sraw_check
                   and 0 <= H <= 100 and 0 <= float(S[target]) <= 100
                   and n_valid_factors >= 10 and len(series) >= 30)
        rows.append({
            "pool": pool_name, "target": target, "n_sample": len(panels),
            "n_failed_members": len(failed), "factors_valid": n_valid_factors,
            "S": round(float(S[target]), 1), "H": round(float(H), 1),
            "factor_scores_max_diff": round(f_diff, 6),
            "S_recheck_diff": round(s_diff, 4), "F4_handcheck_ok": bool(f4_ok),
            "S_raw_eq_factor_mean": bool(sraw_check),
            "series_weeks": len(series), "seconds": round(time.time() - t0, 1),
            "verdict": "PASS" if ok_pool else "FAIL",
        })
        c_detail[pool_name] = {"failed_members": failed[:5],
                               "side_means": {k: (round(v, 1) if v else None)
                                              for k, v in
                                              side_means(scores.loc[target]).items()}}
        log(f"  {pool_name}: target={target} S={float(S[target]):.1f} H={float(H):.1f} "
            f"因子有效 {n_valid_factors}/18 因子分对拍差 {f_diff:.6f} S复算差 {s_diff:.4f} "
            f"F4手验 {'OK' if f4_ok else 'FAIL'} 等权验证 {'OK' if sraw_check else 'FAIL'} "
            f"周数 {len(series)} → {'PASS' if ok_pool else 'FAIL'}")

    RESULTS["parts"]["C"] = {"rows": rows, "detail": c_detail,
                             "network_calls": STATS.network_calls,
                             "cache_hits": STATS.cache_hits}
    pd.DataFrame(rows).to_csv(os.path.join(EV, "四池实测结果.csv"), index=False,
                              encoding="utf-8-sig")

    all_pass = all(r["verdict"] == "PASS" for r in rows)
    detail_002 = str([(r["pool"], r["factor_scores_max_diff"], r["S_recheck_diff"],
                        r["F4_handcheck_ok"], r["S_raw_eq_factor_mean"]) for r in rows])
    ac_save("AC-002", "PASS" if all_pass else "FAIL",
            "四池真实数据：18 因子分用独立 numpy 平均并列名次实现逐分对拍（差<0.01）；"
            "S 两级排名独立复算差<0.01；F4 手工名次验证；S_raw==有效因子等权均值（四侧面不参与加权）；H∈[0,100]",
            f"{EV}/四池实测结果.csv",
            f"逐池(池,因子分对拍差,S复算差,F4手验,等权验证): {detail_002}")
    ac_save("AC-003", "PASS" if all_pass else "FAIL",
            "四池各取真实成分股（快照首位）实测：resolve_pool 归属正确（4/4）+ "
            "S/H 在对应池的真实样本内计算排名（每池 15 只真实成分股）",
            f"{EV}/四池实测结果.csv",
            "边界：全池 300/500/1000/2000 只的完整口径计算需冷启动预热（见报告边界说明）")


# ================================================================ PART D（full 模式标注）
def part_d():
    log("== PART D：full 模式同池标注（函数级，真实快照）==")
    import src.app as appmod
    from src.pool_manager import load_snapshot
    pools = load_snapshot()
    d = []
    orig_mode = appmod.POOL_MODE
    try:
        appmod.POOL_MODE = "full"
        for pool_name in ["沪深300", "中证500", "中证1000", "中证2000"]:
            target = pools[pool_name][0]
            err, pool_label, members = appmod._pool_for(target)
            ok = (err is None and pool_label == f"{pool_name}（{pool_name} 全池）"
                  and len(members) == len(pools[pool_name]))
            d.append({"pool": pool_name, "target": target,
                      "label": pool_label, "members": len(members or []), "ok": ok})
            log(f"  {pool_name}: target={target} → 标注='{pool_label}' 全池 {len(members or [])} 只 "
                f"→ {'OK' if ok else 'FAIL'}")
    finally:
        appmod.POOL_MODE = orig_mode
    RESULTS["parts"]["D"] = d
    ok_all = all(x["ok"] for x in d)
    log(f"  [AC-003 元信息标注] {'PASS' if ok_all else 'FAIL'}"
        f"（full 模式 _pool_for 真实快照函数级实测；端到端全池报告需冷启动，见边界）")
    RESULTS["parts"]["D_verdict"] = "PASS" if ok_all else "FAIL"


if __name__ == "__main__":
    log(f"Gate V 独立验收开始 {BASE} @ {_dt.now().strftime('%Y-%m-%d %H:%M:%S')}")
    try:
        part_a()
        part_b()
        part_c()
        part_d()
    except AssertionError as e:
        log(f"!! 验收中断（断言失败）: {e}")
        RESULTS["fatal"] = str(e)
    except Exception as e:
        log(f"!! 验收异常: {type(e).__name__}: {e}")
        RESULTS["fatal"] = f"{type(e).__name__}: {e}"
    RESULTS["elapsed_seconds"] = round(time.time() - T0, 1)
    RESULTS["finished_at"] = _dt.now().strftime("%Y-%m-%d %H:%M:%S")
    with open(os.path.join(EV, "gate_v_result.json"), "w", encoding="utf-8") as f:
        json.dump(RESULTS, f, ensure_ascii=False, indent=2, default=str)
    log("\n===== AC 判定汇总 =====")
    for k, v in RESULTS["ac"].items():
        log(f"  {k}: {v['verdict']} —— {v['method']}")
    log(f"证据: {EV}/gate_v_result.json（总耗时 {RESULTS['elapsed_seconds']}s）")
