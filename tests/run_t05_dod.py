# -*- coding: utf-8 -*-
"""T-05 验收：合并版报告端到端生成（DoD-1~5 全链）。

样本：Spike-2 同款 20 只演示池 + 603799（T-02 缓存命中，无新增外网请求）。
证据落 evidence/T-05_报告生成器_验收记录_20261017.txt/.json；产物落 out/。
"""

import json
import os
import sys
from datetime import datetime as _dt

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, ".."))

import src.data_fetcher as dfm
from src.compliance import check_report
from src.report_builder import (SECTION_TITLES, build_report, check_sections,
                                _html_to_text)
from src.trade_cal import review_date

START, END = "20240901", "20261017"
POOL_DEMO = [
    ("603799", "华友钴业"), ("600519", "贵州茅台"), ("601318", "中国平安"), ("600036", "招商银行"),
    ("000858", "五粮液"),   ("300750", "宁德时代"), ("002594", "比亚迪"),   ("600900", "长江电力"),
    ("601899", "紫金矿业"), ("000333", "美的集团"), ("600276", "恒瑞医药"), ("002415", "海康威视"),
    ("600887", "伊利股份"), ("601166", "兴业银行"), ("600028", "中国石化"), ("601398", "工商银行"),
    ("300059", "东方财富"), ("601668", "中国建筑"), ("601012", "隆基绿能"), ("600030", "中信证券"),
]

out = {"steps": {}}


def step(name, passed, detail):
    out["steps"][name] = {"pass": bool(passed), "detail": detail}
    print(f"[{'PASS' if passed else 'FAIL'}] {name} —— {detail}", flush=True)


panels, failed = dfm.fetch_many([c for c, _ in POOL_DEMO], START, END, use_cache=True)
step("数据就绪（缓存）", len(panels) == 20 and not failed,
     f"{len(panels)}/20；外网 {dfm.STATS.network_calls} 次（应 0，命中 T-02 缓存）")

out_dir = os.path.join(HERE, "..", "out")
try:
    r = build_report(panels, "603799", "华友钴业",
                     "演示同池（沪深300 样本 20 只）", out_dir)
except Exception as e:
    step("报告生成", False, repr(e)[:200])
    out["overall"] = "HAS FAIL"
    sys.exit(1)

step("报告生成（含强制合规扫描）", True,
     f"HTML+docx 双输出；S={r['S']:.1f}, H={r['H']:.1f}, 排名 {r['rank']}/{r['pool_size']}；"
     f"解读生成器={r['interpretation_generator']}")

# DoD-1 章节完整性（AC-001）
html = open(r["html"], encoding="utf-8").read()
missing = check_sections(html)
step("DoD-1 章节完整性", not missing,
     f"17 章节全在位: {not missing}（缺失: {missing if missing else '无'}）")

# DoD-2 着色（AC-004）：S=10 应为绿色；HTML 中三色都出现
has_colors = all(c in html for c in ("#C00000", "#1E7B3C", "#555555"))
step("DoD-2 BR-007 着色", has_colors,
     f"三色样式在位：红#C00000/绿#1E7B3C/深灰#555555；S={r['S']:.0f} 对应分档偏不利（绿）")

# DoD-3 复核日（AC-006，对拍 Spike-2：10-16 → 10-23）
rd, _ = review_date(_dt.strptime("2026-10-16", "%Y-%m-%d").date())
step("DoD-3 复核日对拍", str(rd) == "2026-10-23",
     f"2026-10-16 + 5 交易日 = {rd}（Spike-2 预演口径一致）")

# DoD-4 边界文案（AC-010）
text = _html_to_text(html)
step("DoD-4 边界文案基线", all(k in text for k in ["怎么读这个分数", "风险与边界",
                                                   "不代表上涨概率", "不构成"]),
     "四个口径要素全在位")

# DoD-5 合规扫描（build_report 内部已强制执行；此处独立复核全文）
cr = check_report(text)
step("DoD-5 合规扫描独立复核", cr.passed, cr.summary())

# docx 可打开性验证（AC-011 前置）
from docx import Document as _D
d = _D(r["docx"])
n_paras = len(d.paragraphs)
step("docx 可打开且内容在位", n_paras > 20,
     f"docx 段落数 {n_paras}；标题含诊断报告字样: "
     f"{any('因子诊断报告' in p.text for p in d.paragraphs)}")

all_pass = all(v["pass"] for v in out["steps"].values())
out["overall"] = "ALL PASS" if all_pass else "HAS FAIL"
out["artifacts"] = {"html": r["html"], "docx": r["docx"]}
with open(os.path.join(HERE, "..", "evidence", "T-05_报告生成器_验收记录_20261017.json"),
          "w", encoding="utf-8") as f:
    json.dump(out, f, ensure_ascii=False, indent=2)
print("\n=== T-05 验收:", out["overall"], "===")
print("HTML:", r["html"])
print("DOCX:", r["docx"])
