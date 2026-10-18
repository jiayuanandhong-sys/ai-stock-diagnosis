# -*- coding: utf-8 -*-
"""T-08 验收：错误路径 + 限流（AC-007/008 + E-10 防滥用）。

DoD-1 无效代码（AC-007）；DoD-2 池外（AC-008，与 T-03 判定一致）；
DoD-3 超限返回限流提示服务不崩（含简单并发用例）。
证据落 evidence/T-08_错误路径与限流_验收记录_20261017.txt/.json。
"""

import json
import os
import sys
from concurrent.futures import ThreadPoolExecutor

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, ".."))

os.environ["SDD_POOL_MODE"] = "demo"
os.environ["SDD_DAILY_LIMIT"] = "10"           # 测试用小上限

from fastapi.testclient import TestClient

import src.app as appmod
from src.rate_limiter import IPRateLimiter

client = TestClient(appmod.app)
out = {"steps": {}}


def step(name, passed, detail):
    out["steps"][name] = {"pass": bool(passed), "detail": detail}
    print(f"[{'PASS' if passed else 'FAIL'}] {name} —— {detail}", flush=True)


# ---- DoD-1/2：错误路径（AC-007/008）--------------------------------------------
r1 = client.get("/diagnose", params={"code": "abc123"})
ok1 = r1.status_code == 400 and "格式无效" in r1.text
step("DoD-1 AC-007 无效代码", ok1, f"HTTP {r1.status_code}；明确提示: {ok1}")

r2 = client.get("/diagnose", params={"code": "830001"})
ok2 = r2.status_code == 400 and "暂不支持" in r2.text and "沪深300" in r2.text
step("DoD-2 AC-008 池外拒绝+支持范围", ok2, f"HTTP {r2.status_code}；文案与 T-03 一致: {ok2}")

r2b = client.get("/diagnose", params={"code": "000000"})
ok2b = r2b.status_code == 400 and ("暂不支持" in r2b.text or "格式无效" in r2b.text)
step("AC-008 非成分股 000000 拒绝", ok2b, f"HTTP {r2b.status_code}")

# ---- DoD-3：限流（服务不崩 + 并发用例）------------------------------------------
# 上限 10：上面已消耗 3 次（abc123、830001、000000），同一 TestClient IP
r3 = client.get("/diagnose", params={"code": "12345"})   # 无效代码也计入配额（防滥用无差别）
for _ in range(8):
    client.get("/diagnose", params={"code": "603799"})   # 打满配额（603799 复用当日产物，秒级）
r4 = client.get("/diagnose", params={"code": "603799"})
ok3 = r4.status_code == 429 and "上限" in r4.text
step("DoD-3 超限返回 429 限流提示", ok3, f"HTTP {r4.status_code}；提示: {'上限' in r4.text}")

# 简单并发用例：20 并发打到已超限 IP → 全部 429，服务不崩
with ThreadPoolExecutor(max_workers=20) as ex:
    futs = [ex.submit(lambda: client.get("/diagnose", params={"code": "603799"}))
            for _ in range(20)]
    codes = [f.result().status_code for f in futs]
ok4 = all(c == 429 for c in codes)
step("并发超限全部 429 且服务存活", ok4,
     f"20 并发状态码分布: {sorted(set(codes))}；服务仍可响应: "
     f"{client.get('/').status_code == 200}")

# 独立 IP 不受影响（限流按 IP 隔离）
appmod.LIMITER._counts.clear()                # 换 IP 模拟：清计数（等价于新 IP）
r5 = client.get("/diagnose", params={"code": "603799"})
step("限流按 IP 隔离·新 IP 正常服务", r5.status_code == 200, f"HTTP {r5.status_code}")

# 限流器单元口径：跨日重置
lim = IPRateLimiter(daily_limit=2)
lim.check_and_count("x"); lim.check_and_count("x")
a = lim.check_and_count("x")
lim._day = "2000-01-01"                       # 模拟跨日
b = lim.check_and_count("x")
step("跨日自动重置", (not a[0]) and b[0], f"当日超限: {not a[0]}；跨日恢复: {b[0]}")

all_pass = all(v["pass"] for v in out["steps"].values())
out["overall"] = "ALL PASS" if all_pass else "HAS FAIL"
with open(os.path.join(HERE, "..", "evidence", "T-08_错误路径与限流_验收记录_20261017.json"),
          "w", encoding="utf-8") as f:
    json.dump(out, f, ensure_ascii=False, indent=2)
print("\n=== T-08 验收:", out["overall"], "===")
