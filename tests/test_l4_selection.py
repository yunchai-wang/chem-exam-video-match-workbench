from __future__ import annotations

import unittest

from apps.l4_workbench.domain import ValidationError
from apps.l4_workbench.selection import build_selection_review, build_selection_run


class SelectionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.asset = {
            "id": "q1", "source_snapshot_id": "snap", "source_name": "2026 南京中考", "question_no": "16",
            "title": "综合实验题", "raw_text": "（1）解释现象（2）设计方案", "content_blocks": [{"type": "image", "path": "q1.png"}],
            "issue_codes": [], "units": [],
        }
        self.diagnosis = {
            "id": "diagnosis", "source_snapshot_id": "snap",
            "results": [{
                "asset_id": "q1", "source_name": "2026 南京中考", "question_no": "16", "difficulty": 4,
                "structural_keys": ["控制变量实验"], "task_tags": ["解释原因", "设计方案"],
                "frequency": {"level": "高频", "numerator": 8, "denominator": 20, "rate": .4, "reason": "8/20", "scope": "同年跨地区"},
                "quality": {"recommendation": "AI候选好题", "score": 4, "score_denominator": 4, "reason": "4/4", "dimensions": {
                    "结构完整": {"status": "支持"}, "典型性": {"status": "支持"},
                    "认知价值": {"status": "支持"}, "迁移价值": {"status": "支持"},
                    "科学性": {"status": "待人工核验"},
                }},
            }],
        }
        self.sample = {"id": "sample", "diagnostic_run_id": "diagnosis", "items": [{"asset_id": "q1"}]}
        self.coverage = {"id": "coverage", "diagnostic_run_id": "diagnosis", "gold_sample_id": "sample", "results": [{
            "asset_id": "q1", "status": "部分覆盖候选", "reason": "结构与任务命中，作答边界待核", "candidates": [{"evidence_level": "E2"}],
        }]}

    def test_builds_independent_fields_and_predicted_priority(self) -> None:
        run = build_selection_run(self.diagnosis, self.sample, self.coverage, [self.asset], [])
        item = run["results"][0]
        self.assertEqual(item["frequency"]["level"], "高频")
        self.assertTrue(item["quality"]["is_good_candidate"])
        self.assertEqual(item["production_priority"]["recommendation"], "P1")
        self.assertEqual(item["production_priority"]["status"], "教研预测")
        self.assertFalse(item["learner_value"]["student_data_available"])
        self.assertEqual([unit["label"] for unit in item["units"]], ["小问（1）", "小问（2）"])

    def test_simple_question_stays_p3_even_with_a_video_gap(self) -> None:
        self.diagnosis["results"][0]["difficulty"] = 1
        self.coverage["results"][0]["status"] = "未覆盖"
        run = build_selection_run(self.diagnosis, self.sample, self.coverage, [self.asset], [])
        self.assertEqual(run["results"][0]["production_priority"]["recommendation"], "P3")
        self.assertIn("视频缺口不会", run["results"][0]["production_priority"]["reason"])

    def test_frequency_does_not_turn_a_low_quality_question_into_production(self) -> None:
        self.diagnosis["results"][0]["quality"]["recommendation"] = "暂不推荐"
        run = build_selection_run(self.diagnosis, self.sample, self.coverage, [self.asset], [])
        self.assertEqual(run["results"][0]["production_priority"]["recommendation"], "暂不生产")
        self.assertFalse(run["results"][0]["quality"]["is_good_candidate"])

    def test_teacher_can_select_units_but_route_change_requires_reason(self) -> None:
        run = build_selection_run(self.diagnosis, self.sample, self.coverage, [self.asset], [])
        candidate = run["results"][0]
        with self.assertRaises(ValidationError):
            build_selection_review(run, {"candidate_id": candidate["id"], "decision": "仅保留好题池"})
        review = build_selection_review(run, {
            "candidate_id": candidate["id"], "decision": "仅保留好题池",
            "selected_unit_ids": [candidate["units"][0]["id"]], "reason": "第二问超出本项目范围",
        })
        self.assertEqual(review["status"], "corrected")
        self.assertFalse(review["ai_flow_blocked"])
        self.assertEqual(review["rule_proposals"][0]["status"], "隔离实验候选")


if __name__ == "__main__":
    unittest.main()
