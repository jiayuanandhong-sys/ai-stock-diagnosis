# -*- coding: utf-8 -*-
"""Bug 1 回归测试：PEG 失败必须阻断数据集冻结（严格门禁）。

覆盖验收要求：
- PEG 全部 PASS → 正常发布有效冻结清单（valid=true）；
- PEG overall=FAIL → 拒绝发布：写 rejected 清单、不触碰既有有效清单、返回失败（调用方 exit 3）；
- PEG overall=PASS 但个别案例自身门 FAIL → 该案例不得标 FROZEN（防御性）；
- 写入异常 → 拒绝发布。
"""
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

TESTS = Path(__file__).resolve().parent
EVALS = TESTS.parent
sys.path.insert(0, str(EVALS / "dataset"))
sys.path.insert(0, str(EVALS / "peg"))

import build_dataset as BD  # noqa: E402


def make_case(cid, status="FROZEN"):
    return {"case_id": cid, "split": "dev", "risk_level": "P1", "layer": "L6",
            "status": status, "assertions": [{"type": "demo", "risk_level": "P1",
                                              "source_tier": "spec"}],
            "blocked_reason": "待定口径" if status == "BLOCKED" else None}


def fake_report(overall, case_gates=None):
    return {"overall": overall, "totals": {}, "cases": case_gates or {}}


class TestPublishManifest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.here = Path(self._tmp.name)
        (self.here / "dataset_stats.json").write_text("{}", encoding="utf-8")
        self._old_cases = BD.CASES

    def tearDown(self):
        BD.CASES = self._old_cases
        self._tmp.cleanup()

    def test_peg_pass_publishes_valid_manifest(self):
        """PEG 全 PASS → 发布 valid=true 清单，FROZEN/BLOCKED 状态正确。"""
        BD.CASES = [make_case("C1"), make_case("C2", "BLOCKED")]
        report = fake_report("PASS", case_gates={
            "C1": {"PEG-1": {"status": "PASS"}, "PEG-3": {"status": "NA"}},
            "C2": {"PEG-1": {"status": "PASS"}, "PEG-3": {"status": "SKIP"}},
        })
        published, manifest = BD.publish_manifest(report, {"pm_decisions": {}},
                                                  BD.CASES, self.here)
        self.assertTrue(published)
        self.assertIs(manifest["valid"], True)
        mf = json.loads((self.here / "frozen_manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(mf["cases"]["C1"]["status"], "FROZEN")
        self.assertEqual(mf["cases"]["C2"]["status"], "BLOCKED")

    def test_peg_fail_rejects_and_preserves_old_manifest(self):
        """PEG FAIL → 不发布有效清单、旧清单原样保留、rejected 清单落盘。"""
        old = {"manifest_version": "old-valid", "marker": "历史有效清单"}
        (self.here / "frozen_manifest.json").write_text(json.dumps(old), encoding="utf-8")
        BD.CASES = [make_case("C1"), make_case("C2", "BLOCKED")]
        published, manifest = BD.publish_manifest(fake_report("FAIL"), {}, BD.CASES, self.here)
        self.assertFalse(published)
        self.assertIs(manifest["valid"], False)
        # 旧有效清单未被触碰
        self.assertEqual(
            json.loads((self.here / "frozen_manifest.json").read_text(encoding="utf-8")), old)
        # 拒绝清单已写且 valid=False
        rej = json.loads((self.here / "frozen_manifest.rejected.json").read_text(encoding="utf-8"))
        self.assertIs(rej["valid"], False)

    def test_peg_fail_never_marks_frozen(self):
        """PEG FAIL → 任何案例都不得被标 FROZEN（含数据集 status=FROZEN 的案例）。"""
        BD.CASES = [make_case("C1"), make_case("C2"), make_case("C3", "BLOCKED")]
        _, manifest = BD.publish_manifest(fake_report("FAIL"), {}, BD.CASES, self.here)
        frozen = [k for k, v in manifest["cases"].items() if v["status"] == "FROZEN"]
        self.assertEqual(frozen, [], "PEG FAIL 后不得有任何 FROZEN 案例")

    def test_case_gate_fail_not_frozen(self):
        """overall=PASS 但案例自身门 FAIL → 该案例不得冻结（防御性双保险）。"""
        BD.CASES = [make_case("C1"), make_case("C2")]
        report = fake_report("PASS", case_gates={
            "C1": {"PEG-3": {"status": "FAIL", "errors": ["数学不一致"]}},
            "C2": {"PEG-1": {"status": "PASS"}},
        })
        _, manifest = BD.publish_manifest(report, {}, BD.CASES, self.here)
        self.assertNotEqual(manifest["cases"]["C1"]["status"], "FROZEN",
                            "案例级门 FAIL 不得冻结")
        self.assertEqual(manifest["cases"]["C2"]["status"], "FROZEN")

    def test_run_peg_and_freeze_returns_false_on_fail(self):
        """run_peg_and_freeze 在 PEG FAIL 时返回 False（调用方 sys.exit(3)）。"""
        old = {"marker": "old"}
        (self.here / "frozen_manifest.json").write_text(json.dumps(old), encoding="utf-8")
        BD.CASES = [make_case("C1")]
        with mock.patch.object(BD.PC, "run_peg", return_value=fake_report("FAIL")):
            ok = BD.run_peg_and_freeze({}, here=self.here)
        self.assertIs(ok, False)
        # 既有清单未被覆盖
        self.assertEqual(
            json.loads((self.here / "frozen_manifest.json").read_text(encoding="utf-8")), old)

    def test_run_peg_and_freeze_returns_true_on_pass(self):
        """run_peg_and_freeze 在 PEG PASS 时返回 True 并发布有效清单。"""
        BD.CASES = [make_case("C1")]
        report = fake_report("PASS", case_gates={"C1": {"PEG-1": {"status": "PASS"}}})
        with mock.patch.object(BD.PC, "run_peg", return_value=report):
            ok = BD.run_peg_and_freeze({}, here=self.here)
        self.assertIs(ok, True)
        mf = json.loads((self.here / "frozen_manifest.json").read_text(encoding="utf-8"))
        self.assertIs(mf["valid"], True)

    def test_write_exception_rejects_publish(self):
        """清单写入异常 → 拒绝发布，不得留下半成品有效清单。

        用「.tmp 路径被目录占用」确定性触发 OSError（Windows 下 chmod 只读不可靠）。
        """
        BD.CASES = [make_case("C1")]
        report = fake_report("PASS", case_gates={"C1": {"PEG-1": {"status": "PASS"}}})
        # 原子写入的临时路径被目录占用 → write_text 必然抛 OSError
        (self.here / "frozen_manifest.json.tmp").mkdir()
        published, manifest = BD.publish_manifest(report, {}, BD.CASES, self.here)
        self.assertFalse(published)
        self.assertIs(manifest["valid"], False)
        # 有效清单不存在（未留下半成品）
        self.assertFalse((self.here / "frozen_manifest.json").exists())

    def test_real_peg_fail_blocks_freeze_integration(self):
        """集成负向测试（无 mock）：真实 peg_checker 对坏案例 → overall=FAIL → 拒绝冻结。

        在临时目录构造含缺陷案例的数据集（缺 evaluator_type 必填字段），
        走真实 run_peg → run_peg_and_freeze 全链路。
        """
        old = {"marker": "历史有效清单"}
        (self.here / "frozen_manifest.json").write_text(json.dumps(old), encoding="utf-8")
        (self.here / "dataset_stats.json").write_text(
            json.dumps({"total_cases": 1, "by_split_dev": 1, "by_split_regression": 0,
                        "by_split_holdout": 0, "by_risk_P0": 0, "by_risk_P1": 1,
                        "by_risk_P2": 0, "blocked": 0}), encoding="utf-8")
        bad_case = {"case_id": "BAD-001", "split": "dev", "risk_level": "P1",
                    "layer": "L6", "status": "FROZEN",
                    "assertions": [{"type": "x", "risk_level": "P1",
                                    "source_tier": "spec"}]}
        #    ↑ 缺 evaluator_type / expected_output 等必填字段 → PEG-1 必 FAIL
        (self.here / "cases_dev.jsonl").write_text(
            json.dumps(bad_case, ensure_ascii=False) + "\n", encoding="utf-8")
        (self.here / "cases_regression.jsonl").write_text("", encoding="utf-8")
        (self.here / "cases_holdout.jsonl").write_text("", encoding="utf-8")
        BD.CASES = [bad_case]
        ok = BD.run_peg_and_freeze({"total_cases": 1}, here=self.here)
        self.assertIs(ok, False, "真实 PEG FAIL 必须阻断冻结（返回 False → exit 3）")
        # 旧清单未被覆盖；拒绝清单已落盘
        self.assertEqual(
            json.loads((self.here / "frozen_manifest.json").read_text(encoding="utf-8")), old)
        rej = json.loads((self.here / "frozen_manifest.rejected.json").read_text(encoding="utf-8"))
        self.assertIs(rej["valid"], False)
        self.assertEqual(rej["cases"]["BAD-001"]["status"], "NOT_FROZEN")


if __name__ == "__main__":
    unittest.main(verbosity=2)
