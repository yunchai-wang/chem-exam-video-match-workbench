from __future__ import annotations

import unittest

from apps.l4_workbench.diagnosis import build_diagnostic_run, select_gold_sample


def asset(identifier: str, paper: str, signature: str, *, difficulty: int = 3, visual: bool = True, issue: str | None = None):
    return {
        "id": identifier,
        "source_snapshot_id": "snapshot-1",
        "source_name": paper,
        "question_no": identifier.removeprefix("q"),
        "raw_text": f"{identifier}. 根据材料分析并回答问题",
        "image_integrity": "preserved" if visual else "no_visual_declared",
        "issue_codes": [issue] if issue else [],
        "source_fields": {
            "primary_type": "科学探究题" if difficulty >= 4 else "基础题",
            "difficulty": difficulty,
            "has_visual": visual,
            "signatures": signature,
            "method_skeleton": signature or "knowledge优先",
            "task_tags": "解释原因、设计方案" if difficulty >= 3 else "判断入口",
            "knowledge_tags": "水与净化、实验探究与控制变量",
            "visual_forms": "装置图、表格" if visual else "",
        },
    }


class DiagnosisTests(unittest.TestCase):
    def setUp(self) -> None:
        self.snapshot = {"id": "snapshot-1", "immutable_checksum": "abc"}

    def test_frequency_uses_structure_but_never_knowledge_alone(self) -> None:
        assets = [
            asset("q1", "2026 A卷", "控制变量实验"),
            asset("q2", "2026 B卷", "控制变量实验"),
            asset("q3", "2026 C卷", ""),
        ]
        run = build_diagnostic_run(self.snapshot, assets)
        by_id = {item["asset_id"]: item for item in run["results"]}
        self.assertEqual(by_id["q1"]["frequency"]["numerator"], 2)
        self.assertEqual(by_id["q1"]["frequency"]["level"], "中频")
        self.assertEqual(by_id["q3"]["frequency"]["level"], "不可判断")
        self.assertIn("不会仅凭同一知识点", by_id["q3"]["frequency"]["reason"])

    def test_one_year_does_not_claim_trend_or_final_priority(self) -> None:
        run = build_diagnostic_run(self.snapshot, [asset("q1", "2026 A卷", "控制变量实验")])
        result = run["results"][0]
        self.assertEqual(run["status"], "completed_with_evidence_limits")
        self.assertEqual(result["trend"]["status"], "证据不足")
        self.assertEqual(result["coverage"]["status"], "无法判断")
        self.assertIsNone(result["production_priority"]["recommendation"])
        self.assertEqual(result["quality"]["dimensions"]["结构完整"]["status"], "支持")
        self.assertEqual(result["quality"]["dimensions"]["科学性"]["status"], "待人工核验")

    def test_gold_sample_is_balanced_and_includes_exception(self) -> None:
        assets = []
        for index in range(1, 16):
            assets.append(asset(
                f"q{index}", f"2026 {chr(64 + ((index - 1) % 5) + 1)}卷",
                f"结构{(index - 1) % 4}", difficulty=4 if index % 3 == 0 else 3,
                visual=index % 2 == 0, issue="image_extreme_aspect_ratio" if index == 7 else None,
            ))
        run = build_diagnostic_run(self.snapshot, assets)
        sample = select_gold_sample(run, assets, 10)
        self.assertEqual(sample["actual_size"], 10)
        self.assertEqual(sample["coverage"]["paper_count"], 5)
        self.assertEqual(sample["coverage"]["exception_count"], 1)
        self.assertIn("q7", {item["asset_id"] for item in sample["items"]})


if __name__ == "__main__":
    unittest.main()
