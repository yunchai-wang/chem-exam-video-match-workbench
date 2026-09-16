from __future__ import annotations

import unittest

from apps.l4_workbench.domain import ValidationError
from apps.l4_workbench.output_planning import normalize_deliverables, required_stages_for


class OutputPlanningTests(unittest.TestCase):
    def test_lesson_plan_stops_before_transcript(self) -> None:
        self.assertEqual(required_stages_for(["lesson_plan"]), [
            "standardization", "diagnosis", "selection", "mother_question", "lesson_plan",
        ])

    def test_transcript_includes_lesson_plan_dependency_but_not_storyboard(self) -> None:
        stages = required_stages_for(["transcript"])
        self.assertIn("lesson_plan", stages)
        self.assertNotIn("storyboard", stages)

    def test_multiple_outputs_share_one_dependency_path(self) -> None:
        stages = required_stages_for(["candidate_pool", "transcript", "candidate_pool"])
        self.assertEqual(stages[-1], "transcript")
        self.assertEqual(normalize_deliverables(["candidate_pool", "transcript", "candidate_pool"]), [
            "candidate_pool", "transcript",
        ])

    def test_empty_or_unknown_output_is_rejected(self) -> None:
        with self.assertRaises(ValidationError):
            required_stages_for([])
        with self.assertRaises(ValidationError):
            required_stages_for(["mystery"])


if __name__ == "__main__":
    unittest.main()
