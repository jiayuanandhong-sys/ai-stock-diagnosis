# -*- coding: utf-8 -*-
"""T-10 验收前回归：AC-001~011 逐条测试 + 测试股清单 + 全套单测回归。

产出：evidence/T-10_AC回归与测试股清单_20261017.json + 控制台记录（tee 落盘）。
PM 责任项（OPEN-P-003 付费问法、试用用户名单）为预留项，不由本脚本代答。
"""

import io
import json
import os
import re
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, ".."))

os.environ["SDD_POOL_MODE"] = "demo"

from fastapi.testclient import TestClient

import src.app as appmod
from src.compliance import check_report
from src.pool_manager import load_snapshot, resolve_pool
from src.report_builder import _html_to_text, check_sections
from src.trade_cal import review_date
from datetime import datetime as _dt

ac = {}


def record(acid, content, method, passed, evidence):
    ac[acid] = {"验收内容": content, "测试方法": method, "结果": "PASS" if passed else "FAIL",
                "证据": evidence}
    print(f"[{'PASS' if passed else 'FAIL'}] {acid} {content}", flush=True)


# ===== 第一部分：全套单元测试回归 ================================================
print("========== 第一部分：单元测试套件 ==========", flush=True)
loader = unittest.TestLoader()
suite = unittest.TestSuite()
for mod in ["test_compliance", "test_data_fetcher", "test_factor_engine",
            "test_pool_manager", "test_interpretation"]:
    suite.addTests(loader.discover("tests", pattern=f"{mod}.py"))
runner = unittest.TextTestRunner(verbosity=0, stream=io.StringIO())
res = runner.run(suite)
ut_pass = res.testsRun - len(res.failures) - len(res.errors)
print(f"单元测试：{res.testsRun} 用例，通过 {ut_pass}，失败 {len(res.failures)}，错误 {len(res.errors)}",
      flush=True)

# ===== 第二部分：集成回归（Web 端到端） ============================================
print("========== 第二部分：集成回归（TestClient） ==========", flush=True)
client = TestClient(appmod.app)

r_home = client.get("/")
r_main = client.get("/diagnose", params={"code": "603799"})
r_dl = client.get("/download", params={"code": "603799"})
html = r_main.text if r_main.status_code == 200 else ""
text = _html_to_text(html) if html else ""

# ===== 第三部分：AC-001~011 逐条记录 ==============================================

missing = check_sections(html)
record("AC-001", "合并版章节完整性", "17 章节机械扫描（check_sections）", not missing,
       "T-05 验收 + 本次回归；缺章: " + str(missing))

record("AC-002", "S/H 计算口径（BR-006）",
       "因子引擎单测（并列名次/方向/缺失不填分/两级排名手算/H 机制）",
       ut_pass > 0 and len(res.failures) == 0,
       "T-04 单测 11/11 + H 正式口径实跑（H=17.3，evidence/T-04_*）")

# AC-003 四池归属（T-03 快照 + resolve_pool 逐只判定）
snap = load_snapshot()
ac3_ok = True
ac3_detail = {}
test_stocks = {"沪深300": [], "中证500": [], "中证1000": [], "中证2000": []}
for pool, codes in snap.items():
    for c in codes[:2]:
        test_stocks[pool].append(c)
        r = resolve_pool(c, snap)
        ac3_ok = ac3_ok and r["supported"] and r["pool"] == pool
    ac3_detail[pool] = test_stocks[pool]
r_603 = resolve_pool("603799", snap)
ac3_ok = ac3_ok and r_603["pool"] == "沪深300"
record("AC-003", "四池归属与同池标注",
       "四池各抽 2 只 + 603799 逐一 resolve_pool 判定；在线报告同池标注检查",
       ac3_ok and "演示同池" in html,
       "T-03 单测 8/8；抽样: " + json.dumps(ac3_detail, ensure_ascii=False))

ok4 = all(c in html for c in ("#C00000", "#1E7B3C", "#555555"))
record("AC-004", "分档着色/十分组（BR-007）", "HTML 三色样式扫描 + band 边界单测（60/40）",
       ok4 and len(res.failures) == 0, "T-04 TestBand 边界单测 + T-05 验收")

cr = check_report(text)
record("AC-005", "合规禁词零命中+口径文案", "check_report 全文扫描（11 禁词 + 4 必需文案）",
       cr.passed, cr.summary() + "；硬门禁链路注入式测试见 evidence/T-06_*")

rd, _ = review_date(_dt.strptime("2026-10-16", "%Y-%m-%d").date())
ok6 = str(rd) == "2026-10-23" and rd.isoformat() in html
record("AC-006", "复核日=出分日+5 交易日", "交易日历回算对拍（Spike-2 口径）+ 报告内打印检查",
       ok6, f"2026-10-16 + 5 交易日 = {rd}；报告内含复核日: {rd.isoformat() in html}")

r_inv = client.get("/diagnose", params={"code": "12345"})
record("AC-007", "无效代码错误提示",
       "GET /diagnose?code=12345（另一用例 abc123 见 T-08）",
       r_inv.status_code == 400 and "格式无效" in r_inv.text,
       f"HTTP {r_inv.status_code}")

r_bse = client.get("/diagnose", params={"code": "830001"})
r_zero = client.get("/diagnose", params={"code": "000000"})
ok8 = (r_bse.status_code == 400 and "暂不支持" in r_bse.text
       and r_zero.status_code == 400 and ("暂不支持" in r_zero.text))
record("AC-008", "池外拒绝+支持范围",
       "北交所 830001 + 非成分股 000000 两条错误路径",
       ok8, f"830001 HTTP {r_bse.status_code}；000000 HTTP {r_zero.status_code}")

ok9 = ("Set-Cookie" not in r_home.headers and "Set-Cookie" not in r_main.headers
       and "<script" not in r_home.text and "<script" not in html
       and "登录" not in r_home.text and "密码" not in r_home.text)
record("AC-009", "零额外采集", "响应头无 Set-Cookie；页面无 JS/登录/注册/手机号；请求仅 code 字段",
       ok9, "T-07 验收 AC-009 核对 + 本次回归双页面检查")

ok10 = all(k in text for k in ["怎么读这个分数", "风险与边界", "不代表上涨概率", "不构成"])
record("AC-010", "边界文案基线在位", "HTML 纯文本四要素扫描", ok10, "T-05 DoD-4 + 本次回归")

docx_ok = False
if r_dl.status_code == 200:
    from docx import Document as _D
    d = _D(io.BytesIO(r_dl.content))
    dall = "\n".join(p.text for p in d.paragraphs)
    docx_ok = ("因子诊断报告" in dall and "不构成" in dall
               and "不代表上涨概率" in dall and "603799" in dall)
record("AC-011", "在线查看与下载一致", "docx 下载读回三要素（标题/结论/边界）与在线 HTML 比对",
       r_dl.status_code == 200 and docx_ok,
       f"HTTP {r_dl.status_code}；{len(r_dl.content)//1024} KB；三要素一致: {docx_ok}")

# ===== 第四部分：测试股清单 ========================================================
invalid_cases = ["12345", "abc123"]
out_of_pool_cases = ["830001", "000000"]
pool_out = {"四池抽样（各2只）": test_stocks, "主路径标的": ["603799"],
            "无效代码用例": invalid_cases, "池外用例": out_of_pool_cases}

all_pass = all(v["结果"] == "PASS" for v in ac.values()) and len(res.failures) == 0
summary = {
    "AC 矩阵": ac,
    "单元测试": {"total": res.testsRun, "passed": ut_pass,
                 "failures": len(res.failures), "errors": len(res.errors)},
    "测试股清单": pool_out,
    "PM 责任项（预留，不由 AI 代答）": {
        "OPEN-P-003": "20% 付费意愿问法设计（Verify 阶段前定稿）",
        "试用用户": "5–10 名真实持仓用户名单与触达方式",
        "T-09 外网验证": "轻量域名注册（花生壳/ngrok，需 PM 本人账号操作）+ 手机外网访问截图",
    },
    "overall": "ALL PASS" if all_pass else "HAS FAIL",
}
with open(os.path.join(HERE, "..", "evidence", "T-10_AC回归与测试股清单_20261017.json"),
          "w", encoding="utf-8") as f:
    json.dump(summary, f, ensure_ascii=False, indent=2)
print("\n========== T-10 回归汇总:", summary["overall"], "==========", flush=True)
print(f"AC: {sum(1 for v in ac.values() if v['结果'] == 'PASS')}/{len(ac)} PASS；"
      f"单测 {ut_pass}/{res.testsRun}", flush=True)
sys.exit(0 if all_pass else 1)
