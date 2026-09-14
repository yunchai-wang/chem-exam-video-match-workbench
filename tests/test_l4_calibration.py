from __future__ import annotations

import unittest

from apps.l4_workbench.calibration import build_calibration_review
from apps.l4_workbench.domain import ValidationError


class CalibrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.asset = {"id": "q1", "issue_codes": []}
        self.diagnostic = {
            "id": "diagnosis-1",
            "results": [{
                "asset_id": "q1", "source_name": "2026 A卷", "question_no": "1",
                "structural_keys": ["控制变量实验"],
                "frequency": {"level": "高频"},
                "quality": {
                    "recommendation": "AI候选好题",
                    "dimensions": {"科学性": {"status": "待人工核验"}},
                },
            }],
        }
        self.sample = {"id": "sample-1", "items": [{"asset_id": "q1"}]}
        self.coverage = {
            "id": "coverage-1", "diagnostic_run_id": "diagnosis-1", "gold_sample_id": "sample-1",
            "results": [{"asset_id": "q1", "status": "部分覆盖候选"}],
        }

    def test_accepting_ai_values_does_not_create_rule_proposal(self) -> None:
        review = build_calibration_review(
            self.diagnostic, self.sample, self.coverage, self.asset, {}, mode="batch_pass",
        )
        self.assertEqual(review["status"], "accepted")
        self.assertEqual(review["corrected_fields"], [])
        self.assertEqual(review["rule_proposals"], [])
        self.assertFalse(review["ai_flow_blocked"])

    def test_correction_requires_reason_and_is_attributed(self) -> None:
        with self.assertRaises(ValidationError):
            build_calibration_review(
                self.diagnostic, self.sample, self.coverage, self.asset,
                {"frequency": "低频"},
            )
        review = build_calibration_review(
            self.diagnostic, self.sample, self.coverage, self.asset,
            {"frequency": "低频", "reason": "同结构实际上只在一个可比地区出现。"},
        )
        self.assertEqual(review["status"], "corrected")
        self.assertEqual(review["rule_families"], ["frequency_rule"])
        self.assertEqual(review["rule_proposals"][0]["status"], "隔离实验候选")


if __name__ == "__main__":
    unittest.main()
