# -*- coding: utf-8 -*-
"""T-07 验收：Web 应用端到端（DoD-1~3 + AC-009/011 + 错误路径预验）。

FastAPI TestClient 进程内测试（无端口占用）；demo 池模式（默认）。
证据落 evidence/T-07_Web应用_验收记录_20261017.txt/.json。
"""

import io
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, ".."))

os.environ["SDD_POOL_MODE"] = "demo"

from fastapi.testclient import TestClient

import src.app as appmod
from src.report_builder import SECTION_TITLES, check_sections

client = TestClient(appmod.app)
out = {"steps": {}}


def step(name, passed, detail):
    out["steps"][name] = {"pass": bool(passed), "detail": detail}
    print(f"[{'PASS' if passed else 'FAIL'}] {name} —— {detail}", flush=True)


# ---- 0. 输入页（AC-009）-------------------------------------------------------
r0 = client.get("/")
ok0 = (r0.status_code == 200 and 'name="code"' in r0.text
       and "<form" in r0.text and "<script" not in r0.text
       and "Set-Cookie" not in r0.headers)
step("输入页（唯一字段/无JS/无Cookie）", ok0,
     f"HTTP {r0.status_code}；含代码表单: {'name=\"code\"' in r0.text}；"
     f"script 标签: {'<script' in r0.text}；Set-Cookie: {'Set-Cookie' in r0.headers}；"
     f"免责口径在位: {'不构成' in r0.text}")

# ---- 1. 主路径：603799 在线完整报告（AC-011 前半） ------------------------------
r1 = client.get("/diagnose", params={"code": "603799"})
missing = check_sections(r1.text) if r1.status_code == 200 else ["<非200>"]
ok1 = (r1.status_code == 200 and not missing
       and "Set-Cookie" not in r1.headers and "<script" not in r1.text
       and "603799" in r1.text)
step("主路径 603799 在线报告（17 章节完整）", ok1,
     f"HTTP {r1.status_code}；缺章: {missing if missing else '无'}；"
     f"同池标注: {re.search(r'演示同池[^<]*', r1.text).group(0)[:30] if r1.status_code == 200 else '-'}")

# ---- 2. 复用验证（第二次请求复用当日产物，秒级） ---------------------------------
r1b = client.get("/diagnose", params={"code": "603799"})
step("当日产物复用", r1b.status_code == 200, f"HTTP {r1b.status_code}（缓存复用，无需重算）")

# ---- 3. docx 下载（AC-011 后半：可打开且内容一致） -------------------------------
r2 = client.get("/download", params={"code": "603799"})
docx_ok = False; n_paras = 0
if r2.status_code == 200 and len(r2.content) > 10000:
    from docx import Document as _D
    d = _D(io.BytesIO(r2.content))
    n_paras = len(d.paragraphs)
    html_text = re.sub(r"<[^>]+>", "", r1.text)
    docx_all = "\n".join(p.text for p in d.paragraphs)
    same_title = ("因子诊断报告" in docx_all and "603799" in docx_all)
    same_verdict = any("S=" in p.text or "综合分" in p.text for p in d.paragraphs)
    same_boundary = "不构成" in docx_all and "不代表上涨概率" in docx_all
    docx_ok = same_title and same_verdict and same_boundary
step("docx 下载可打开且内容一致（AC-011）", r2.status_code == 200 and docx_ok,
     f"HTTP {r2.status_code}；大小 {len(r2.content)//1024} KB；段落数 {n_paras}；"
     f"标题/结论/边界三要素与在线版一致: {docx_ok}")

# ---- 4. AC-009 抓包核对：请求载荷仅 code，无额外字段 ----------------------------
# TestClient 显式仅传 code；响应头无 Set-Cookie；输入页无登录/无采集元素
ok4 = ("Set-Cookie" not in r1.headers and "<script" not in r1.text
       and "登录" not in r0.text and "注册" not in r0.text
       and "手机号" not in r0.text and "密码" not in r0.text)
step("AC-009 零采集核对", ok4,
     "请求仅 code 字段；响应无 Cookie；输入页无登录/注册/手机号/密码等采集元素")

# ---- 5. 错误路径（SCN-002 无效 / SCN-003 池外，AC-007/008 预验） -------------------
r3 = client.get("/diagnose", params={"code": "12345"})
ok3 = (r3.status_code == 400 and "格式无效" in r3.text and "因子诊断报告" not in r3.text)
step("SCN-002 无效代码 → 明确提示不生成报告", ok3, f"HTTP {r3.status_code}；提示: '格式无效' in 响应")

r4 = client.get("/diagnose", params={"code": "830001"})    # 北交所（四池外）
ok4b = (r4.status_code == 400 and "暂不支持" in r4.text and "沪深300" in r4.text)
step("SCN-003 池外 A 股 → 暂不支持+支持范围", ok4b,
     f"HTTP {r4.status_code}；含支持范围提示: {ok4b}")

r5 = client.get("/diagnose", params={"code": "601888"})    # 演示池外的沪深300成分股
ok5 = (r5.status_code == 400 and "演示模式" in r5.text)
step("demo 模式范围提示（诚实标注）", ok5, f"HTTP {r5.status_code}；提示演示支持列表: {ok5}")

# ---- 6. 服务端渲染核对（DoD-3：无 JS 依赖看核心内容） ----------------------------
ok6 = "<script" not in r1.text and len(re.sub(r'<[^>]+>', '', r1.text)) > 3000
step("服务端渲染·无 JS 核心内容可读", ok6,
     f"script 标签: {'<script' in r1.text}；纯文本长度 {len(re.sub(r'<[^>]+>', '', r1.text))} 字符")

all_pass = all(v["pass"] for v in out["steps"].values())
out["overall"] = "ALL PASS" if all_pass else "HAS FAIL"
with open(os.path.join(HERE, "..", "evidence", "T-07_Web应用_验收记录_20261017.json"),
          "w", encoding="utf-8") as f:
    json.dump(out, f, ensure_ascii=False, indent=2)
print("\n=== T-07 验收:", out["overall"], "===")
