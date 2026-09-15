from __future__ import annotations

import unittest

from apps.l4_workbench.domain import ValidationError
from apps.l4_workbench.downstream import create_downstream_task


class DownstreamTaskTests(unittest.TestCase):
    def setUp(self) -> None:
        self.selection = {
            "id": "selection-1", "source_snapshot_id": "snapshot-1",
            "results": [{
                "id": "candidate-q1", "asset_id": "q1", "source_name": "南京中考", "question_no": "16",
                "units": [{"id": "q1-a"}, {"id": "q1-b"}],
                "ai_role_labels": ["母题候选", "核心例题"],
                "ai_usage_scenarios": ["视频生产", "习题册"],
            }],
        }
        self.assets = [{
            "id": "q1", "image_integrity": "preserved",
            "content_blocks": [{"type": "image", "path": "snapshots/q1.png"}],
            "tag_profile": {
                "library_snapshot_id": "junior-chem-label-library-2026-01-28",
                "knowledge": {"all": ["控制变量法"], "core": ["控制变量法"], "distractor": []},
                "question": ["求实验方案/实验设计"], "solution": ["根据控制变量法设计实验"],
                "condition": [], "context": [], "thinking_method": ["控制变量法"],
            },
        }]
        self.project = {"target_students": "中等及以上", "target_region": "南京", "target_exam_type": "中考", "content_scope": "实验探究"}

    def test_freezes_question_units_roles_and_image_references(self) -> None:
        result = create_downstream_task(self.selection, [], self.assets, self.project, {
            "task_type": "习题册", "name": "实验探究习题册", "candidate_ids": ["candidate-q1"],
        })
        question_set = result["question_set"]
        task = result["task"]
        self.assertEqual(question_set["item_count"], 1)
        self.assertEqual(question_set["items"][0]["selected_unit_ids"], ["q1-a", "q1-b"])
        self.assertEqual(question_set["items"][0]["content_blocks"][0]["path"], "snapshots/q1.png")
        self.assertEqual(question_set["items"][0]["tag_profile"]["knowledge"]["core"], ["控制变量法"])
        self.assertIn("视频覆盖诊断", task["dependency_plan"]["skipped"])
        self.assertEqual(task["execution_mode"], "contract_only")

    def test_rejects_candidate_outside_frozen_selection(self) -> None:
        with self.assertRaises(ValidationError):
            create_downstream_task(self.selection, [], self.assets, self.project, {
                "task_type": "作业", "candidate_ids": ["candidate-other"],
            })

    def test_custom_task_requires_a_goal(self) -> None:
        with self.assertRaises(ValidationError):
            create_downstream_task(self.selection, [], self.assets, self.project, {
                "task_type": "自定义", "candidate_ids": ["candidate-q1"],
            })

    def test_rejects_a_reviewed_candidate_with_no_selected_units(self) -> None:
        with self.assertRaises(ValidationError):
            create_downstream_task(self.selection, [{
                "selection_run_id": "selection-1", "candidate_id": "candidate-q1",
                "selected_unit_ids": [], "role_labels": [], "usage_scenarios": ["习题册"],
            }], self.assets, self.project, {
                "task_type": "习题册", "candidate_ids": ["candidate-q1"],
            })


if __name__ == "__main__":
    unittest.main()
