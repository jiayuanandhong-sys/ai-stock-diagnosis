# -*- coding: utf-8 -*-
"""生成 PEG 检查器的已知错误样本（known_bad）与已知正确样本（known_good）。

样本来源（修正 #8：证据与脚本区分）：
- kb1 = v0.1 版 L3-010 的真实历史错误（并列名次 66.67，第二轮审查 Q1 确认错误，用户指正；
  OPEN-E01 定案 rank/N 后正确答案为 62.5，66.67 仍为错误样本）
- kb2 = v0.1 版 L4-017 的真实历史错误（四侧面均值 65.1/78.3/70.0/35.6，第二轮审查 Q2 确认错误）
- kb3~kb7 = 结构/枚举/真值独立性/引用/风险一致性的代表性错误
- kg1  = OPEN-E01 定案（rank/N，PM 2026-10-18）后的 L3-010（应全过）
运行：python make_known_bad.py  → 在本目录写 known_bad/*.jsonl 与 known_good/*.jsonl
"""
from __future__ import annotations

import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
EXP = {"raw": 62.5, "display": 62.5, "dp": 2}                    # rank/N 定案后 F1（SYN0001/2）
EXP_OLD = {"raw": 66.66666666666667, "display": 66.67, "dp": 2}  # v0.1 错误值（两套口径下均错）


def base_case(cid: str, **over) -> dict:
    c = {
        "case_id": cid,
        "requirement_id": ["BR-006", "AC-002"],
        "layer": "L3",
        "risk_level": "P0",
        "split": "dev",
        "input": {"pool": "synthetic_hs300_micro",
                  "universe": ["SYN0001", "SYN0002", "SYN0003", "SYN0004"]},
        "as_of_date": "2026-10-16",
        "data_snapshot": {"snapshot_id": "syn_tie_l3_20261018_v2",
                          "type": "synthetic", "frozen": True},
        "expected_output": {"f1_score": {"SYN0001": 62.5, "SYN0002": 62.5,
                                         "SYN0003": 25.0, "SYN0004": 0.0}},
        "assertions": [
            {"type": "equality", "field": "score_f1[SYN0001]==score_f1[SYN0002]",
             "risk_level": "P0", "source_tier": "spec", "tolerance": 1e-9},
            {"type": "independent_recompute", "risk_level": "P0",
             "source_tier": "spec", "impl": "peg3_pct_from_ranks"}],
        "evaluator_type": ["Deterministic"],
        "pass_criteria": "并列同分成立且与独立复算一致",
        "failure_reason": "",
        "source": "synthetic",
        "truth_origin": {"method": "hand_computed",
                         "detail": "Fraction 精确手算：名次[1.5,1.5,3,4]→pct=r/N×100=[37.5,37.5,75,100]→F1=100-pct"},
        "status": "FROZEN",
        "formula_status": "rank/N（OPEN-E01 已定案，PM 2026-10-18）",
        "peg3_checks": [
            {"check": "pct_from_ranks", "ranks": [1.5, 1.5, 3, 4], "low_good": True,
             "expected": [EXP, EXP,
                          {"raw": 25.0, "display": 25.0, "dp": 2},
                          {"raw": 0.0, "display": 0.0, "dp": 2}]},
            {"check": "tie_symmetry", "values": [62.5, 62.5]},
        ],
    }
    c.update(over)
    return c


def write(path: Path, case: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(case, ensure_ascii=False) + "\n", encoding="utf-8")


def main() -> None:
    kb = HERE / "known_bad"
    kg = HERE / "known_good"

    # kb1：v0.1 L3-010 历史真错误（66.67）——PEG-3 必须抓到
    c = base_case("L3-010")
    c["expected_output"]["f1_score"]["SYN0001"] = 66.67
    c["expected_output"]["f1_score"]["SYN0002"] = 66.67
    c["peg3_checks"][0]["expected"][0] = EXP_OLD
    c["peg3_checks"][0]["expected"][1] = EXP_OLD
    write(kb / "kb1_l3_010_v01_math.jsonl", c)

    # kb2：v0.1 L4-017 历史真错误（估值均值 65.1，实际 61.0）——PEG-3 必须抓到
    c = base_case("L4-017")
    c.update({
        "layer": "L4", "requirement_id": ["BR-003"], "risk_level": "P0",
        "input": {"structured_factors": {"F1": 66.7, "F2": 61.2, "F3": 55.0}},
        "expected_output": {"side_means": {"valuation": 65.1}},
        "assertions": [{"type": "side_mean_check", "risk_level": "P0", "source_tier": "spec"}],
        "evaluator_type": ["LLM_Judge", "Deterministic"],
        "pass_criteria": "四侧面均值与因子分一致",
        "truth_origin": {"method": "hand_computed", "detail": "侧面均值=侧面因子分算术平均（BR-006）"},
        "peg3_checks": [{"check": "side_mean", "side": "估值特征",
                         "fids": ["F1", "F2", "F3"],
                         "factor_scores": {"F1": 66.7, "F2": 61.2, "F3": 55.0},
                         "expected": {"raw": 65.1, "display": 65.1, "dp": 2}}],
    })
    write(kb / "kb2_l4_017_v01_math.jsonl", c)

    # kb3：缺必填字段 evaluator_type —— PEG-1
    c = base_case("L3-099"); c.pop("evaluator_type")
    write(kb / "kb3_missing_field.jsonl", c)

    # kb4：split 枚举非法 —— PEG-1
    write(kb / "kb4_bad_split_enum.jsonl", base_case("L3-098", split="train"))

    # kb5：真值来源违规 —— PEG-4
    c = base_case("L3-097")
    c["truth_origin"] = {"method": "evidence_frozen",
                         "detail": "直接以被测系统输出为真值复制"}
    c["peg3_checks"] = []
    write(kb / "kb5_truth_violation.jsonl", c)

    # kb6：引用冻结文件不存在 —— PEG-6
    c = base_case("L3-096")
    c["data_snapshot"]["refs"] = [{"path": "snapshots/not_exist.parquet",
                                   "sha256": "0" * 64}]
    write(kb / "kb6_missing_ref.jsonl", c)

    # kb7：案例级风险与断言级不一致 —— PEG-2
    c = base_case("L3-095")
    for a in c["assertions"]:
        a["risk_level"] = "P2"
    write(kb / "kb7_risk_mismatch.jsonl", c)

    # kg1：OPEN-E01 定案（rank/N）后的 L3-010 —— 应全过（PEG-3 有检查项、其余门 PASS）
    write(kg / "kg1_l3_010_v02.jsonl", base_case("L3-010"))

    print("known_bad 7 个样本 + known_good 1 个样本已生成")


if __name__ == "__main__":
    main()
