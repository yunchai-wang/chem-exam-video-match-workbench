from __future__ import annotations

import unittest

from apps.l4_workbench.engine import affected_stages, classify_feedback, recommend_priority, release_decision


def question(**updates):
    item = {
        "content_health": {"status": "无明显问题"},
        "coverage": {"status": "未覆盖"},
        "quality": {"ai_conclusion": "是", "teacher_conclusion": "待复核"},
        "frequency": {"level": "低频"},
        "trend": {"label": "稳定"},
        "learner_value": {"stable_barrier": False, "key_prerequisite": False},
        "difficulty": {"level": "中等"},
        "migration_value": "中",
        "score": 4,
    }
    item.update(updates)
    return item


class EngineTests(unittest.TestCase):
    def test_simple_gap_for_middle_plus_is_p3(self) -> None:
        item = question(difficulty={"level": "基础"})
        priority, _, intervention = recommend_priority(item, {"target_students": "中等及以上"})
        self.assertEqual(priority, "P3")
        self.assertEqual(intervention, "仅题库保留")

    def test_integrated_gap_can_be_p1(self) -> None:
        item = question(
            coverage={"status": "组合支撑但缺综合迁移"},
            frequency={"level": "高频"},
            learner_value={"stable_barrier": True},
            score=8,
        )
        priority, _, _ = recommend_priority(item, {"target_students": "中等及以上"})
        self.assertEqual(priority, "P1")

    def test_content_problem_uses_non_priority_stop(self) -> None:
        item = question(content_health={"status": "已确认需纠错"})
        priority, reason, _ = recommend_priority(item, {"target_students": "中等及以上"})
        self.assertEqual(priority, "暂不生产")
        self.assertIn("纠错", reason)

    def test_release_strategies(self) -> None:
        self.assertEqual(release_decision("auto", True), "continue")
        self.assertEqual(release_decision("exceptions", False), "continue")
        self.assertEqual(release_decision("exceptions", True), "wait")
        self.assertEqual(release_decision("confirm", False), "wait")
        self.assertEqual(release_decision("blocked", False), "blocked")

    def test_feedback_routes_only_affected_downstream(self) -> None:
        stage, _ = classify_feedback("逐字稿不够口语，过渡太硬")
        self.assertEqual(stage, "transcript")
        self.assertEqual(affected_stages(stage), ["transcript", "storyboard"])


if __name__ == "__main__":
    unittest.main()
