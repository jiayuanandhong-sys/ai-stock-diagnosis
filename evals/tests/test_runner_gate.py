# -*- coding: utf-8 -*-
"""Bug 1 回归测试（Runner 侧）：启动前强制验证冻结数据集。

覆盖验收要求：
- PEG 报告/冻结清单不存在或损坏 → Runner 拒绝运行（exit 2）；
- 清单 valid=false 或 peg_report_overall != PASS → 拒绝运行；
- 数据集文件被篡改、哈希不匹配 → 拒绝运行；
- 案例集与清单不一致 → 拒绝运行；
- BLOCKED 案例伪装 FROZEN → 拒绝运行；
- 合法 BLOCKED 案例存在时不阻断其他案例执行。
"""
import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

TESTS = Path(__file__).resolve().parent
EVALS = TESTS.parent
sys.path.insert(0, str(EVALS / "dataset"))
sys.path.insert(0, str(EVALS / "runner"))

import run_evals as RE  # noqa: E402


def make_case(cid, status="FROZEN", split="dev"):
    return {"case_id": cid, "split": split, "risk_level": "P1", "layer": "L6",
            "status": status, "assertions": [{"type": "demo", "risk_level": "P1",
                                              "source_tier": "spec"}],
            "blocked_reason": "待定口径" if status == "BLOCKED" else None}


def line(c):
    return json.dumps(c, ensure_ascii=False)


def write_dataset(tmp, cases):
    """写全部三个 split 文件（无案例的 split 写空文件，符合数据集契约）。"""
    by_split = {"dev": [], "regression": [], "holdout": []}
    for c in cases:
        by_split.setdefault(c["split"], []).append(c)
    for split, cs in by_split.items():
        (tmp / f"cases_{split}.jsonl").write_text(
            "".join(line(c) + "\n" for c in cs), encoding="utf-8")


def manifest_for(cases, **over):
    m = {"manifest_version": "1.1", "valid": True, "peg_report_overall": "PASS",
         "peg_checker_version": "test",
         "cases": {c["case_id"]: {
             "status": "BLOCKED" if c["status"] == "BLOCKED" else "FROZEN",
             "sha256": hashlib.sha256(line(c).encode("utf-8")).hexdigest(),
             "split": c["split"], "risk_level": c["risk_level"]} for c in cases}}
    m.update(over)
    return m


class TestValidateFreezeGate(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def _gate(self, cases, manifest):
        write_dataset(self.tmp, cases)
        loaded = {c["case_id"]: c for c in cases}
        return RE.validate_freeze_gate(loaded, manifest, self.tmp)

    def test_valid_manifest_passes(self):
        cases = [make_case("C1"), make_case("C2", "BLOCKED")]
        self.assertEqual(self._gate(cases, manifest_for(cases)), [])

    def test_invalid_flag_rejected(self):
        cases = [make_case("C1")]
        errs = self._gate(cases, manifest_for(cases, valid=False))
        self.assertTrue(any("valid" in e for e in errs), errs)

    def test_peg_not_pass_rejected(self):
        cases = [make_case("C1")]
        errs = self._gate(cases, manifest_for(cases, peg_report_overall="FAIL"))
        self.assertTrue(any("PEG" in e for e in errs), errs)

    def test_tampered_dataset_rejected(self):
        """数据集行被篡改 → 哈希不匹配 → 拒绝。"""
        cases = [make_case("C1"), make_case("C2")]
        m = manifest_for(cases)
        # 篡改：重写 C1 行（改断言内容），清单哈希仍是旧值
        write_dataset(self.tmp, cases)
        tampered = make_case("C1")
        tampered["assertions"][0]["risk_level"] = "P0"
        (self.tmp / "cases_dev.jsonl").write_text(line(tampered) + "\n" + line(cases[1]) + "\n",
                                                  encoding="utf-8")
        loaded = {c["case_id"]: c for c in [tampered, cases[1]]}
        errs = RE.validate_freeze_gate(loaded, m, self.tmp)
        self.assertTrue(any("C1" in e and "哈希" in e for e in errs), errs)

    def test_case_set_mismatch_rejected(self):
        """清单缺一个案例 → 案例集不一致 → 拒绝。"""
        cases = [make_case("C1"), make_case("C2")]
        m = manifest_for([cases[0]])           # 清单只含 C1
        errs = self._gate(cases, m)
        self.assertTrue(any("不一致" in e for e in errs), errs)

    def test_blocked_disguised_as_frozen_rejected(self):
        """数据集 BLOCKED 案例被清单标成 FROZEN → 伪装冻结 → 拒绝。"""
        cases = [make_case("C1", "BLOCKED")]
        m = manifest_for(cases)
        m["cases"]["C1"]["status"] = "FROZEN"  # 伪装
        errs = self._gate(cases, m)
        self.assertTrue(any("伪装" in e for e in errs), errs)


class TestMainGateBlocking(unittest.TestCase):
    """main() 层面：门禁失败必须 return 2 且不产生任何评测结果。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def test_missing_manifest_rejected(self):
        with mock.patch.object(RE, "DATASET", self.tmp):
            rc = RE.main()
        self.assertEqual(rc, 2)

    def test_corrupt_manifest_rejected(self):
        (self.tmp / "frozen_manifest.json").write_text("{不是JSON", encoding="utf-8")
        with mock.patch.object(RE, "DATASET", self.tmp):
            rc = RE.main()
        self.assertEqual(rc, 2)

    def test_invalid_manifest_rejected(self):
        (self.tmp / "frozen_manifest.json").write_text(
            json.dumps({"valid": False, "peg_report_overall": "FAIL", "cases": {}}),
            encoding="utf-8")
        with mock.patch.object(RE, "DATASET", self.tmp):
            rc = RE.main()
        self.assertEqual(rc, 2)

    def test_valid_with_blocked_runs_others(self):
        """合法 BLOCKED 案例存在 → 其他可评测案例正常执行（不因 BLOCKED 阻断）。"""
        cases = [make_case("C1"), make_case("C2", "BLOCKED")]
        write_dataset(self.tmp, cases)
        (self.tmp / "frozen_manifest.json").write_text(
            json.dumps(manifest_for(cases)), encoding="utf-8")
        out = self.tmp / "results"
        out.mkdir()

        def fake_handler(case, ctx):
            ctx.check("demo_assertion", True, {"ok": True}, risk="P1")

        with mock.patch.object(RE, "DATASET", self.tmp), \
             mock.patch.object(RE, "OUT", out), \
             mock.patch.object(RE, "HANDLERS", {"C1": fake_handler}):
            rc = RE.main()
        # C1 全部必需断言 PASS，C2 BLOCKED → 存在 BLOCKED → exit 2（未完整验收），但已真实执行
        self.assertEqual(rc, 2)
        res = json.loads((out / "results.json").read_text(encoding="utf-8"))
        self.assertEqual(res["cases"]["C1"]["status"], "PASS")
        self.assertEqual(res["cases"]["C2"]["status"], "BLOCKED")


if __name__ == "__main__":
    unittest.main(verbosity=2)
