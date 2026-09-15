from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from apps.l4_workbench.service import WorkbenchService
from apps.l4_workbench.store import JsonStore


ROOT = Path(__file__).resolve().parents[1]
SEED = ROOT / "sample_data" / "l4_workbench" / "seed.json"
STAGES = ["standardization", "diagnosis", "selection", "mother_question", "lesson_plan", "transcript", "storyboard"]


class ServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.service = WorkbenchService(JsonStore(Path(self.temp.name) / "state.json", SEED))

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_full_auto_reaches_artifact(self) -> None:
        self.service.update_project({"intervention_strategies": {stage: "auto" for stage in STAGES}})
        run = self.service.start_run()
        self.assertEqual(run["status"], "completed")
        self.assertEqual(len(self.service.get_state()["artifacts"]), 1)

    def test_confirm_strategy_pauses_only_at_configured_stage(self) -> None:
        strategies = {stage: "auto" for stage in STAGES}
        strategies["lesson_plan"] = "confirm"
        self.service.update_project({"intervention_strategies": strategies})
        run = self.service.start_run()
        self.assertEqual(run["status"], "waiting")
        self.assertEqual(run["current_stage"], "lesson_plan")
        review = self.service.get_state()["reviews"][-1]
        resumed = self.service.approve_review(review["id"])
        self.assertEqual(resumed["status"], "completed")

    def test_final_feedback_routes_and_creates_experiment(self) -> None:
        self.service.update_project({"intervention_strategies": {stage: "auto" for stage in STAGES}})
        self.service.start_run()
        artifact = self.service.get_state()["artifacts"][-1]
        result = self.service.add_artifact_feedback(artifact["id"], "逐字稿不够口语，过渡也太硬")
        self.assertEqual(result["feedback"]["root_stage"], "transcript")
        self.assertEqual(result["run"]["stage_states"]["selection"], "not_affected")
        self.assertEqual(result["artifact"]["version"], 2)
        self.assertIn("非正式生产成品", result["artifact"]["status"])
        rerun_jobs = [job for job in self.service.get_state()["jobs"] if job["run_id"] == result["run"]["id"]]
        self.assertEqual([job["stage"] for job in rerun_jobs], ["transcript", "storyboard"])
        self.assertTrue(all(job["execution_mode"] == "dry_run" for job in rerun_jobs))
        self.assertIn("待真实保留集回测", result["rule"]["status"])
        self.assertIsNone(result["rule"]["experiment_precision"])

    def test_public_rule_request_is_never_auto_approved(self) -> None:
        rule = self.service.get_state()["rules"][-1]
        publication = self.service.request_publication(rule["id"])
        self.assertEqual(publication["status"], "待人工批准")

    def test_teacher_reason_is_saved_with_question_feedback(self) -> None:
        question = self.service.update_question("q-water-01", {
            "teacher_quality_conclusion": "是",
            "teacher_coverage_conclusion": "部分覆盖",
            "teacher_feedback_reason": "第三问缺完整迁移台阶",
        })
        self.assertEqual(question["quality"]["teacher_conclusion"], "是")
        self.assertEqual(question["coverage"]["teacher_conclusion"], "部分覆盖")
        self.assertEqual(question["teacher_feedback_reason"], "第三问缺完整迁移台阶")

    def test_create_tag_configuration_activates_it_for_current_project(self) -> None:
        config = self.service.create_tag_configuration({
            "name": "无既有标签的初中物理项目",
            "subject": "初中物理",
            "onboarding_mode": "no_labels",
            "selected_dimensions": ["knowledge", "question", "experiment_name"],
        })
        state = self.service.get_state()
        self.assertEqual(state["project"]["active_tag_configuration_id"], config["id"])
        self.assertEqual(state["tag_configurations"][-1]["status"], "AI初版·可运行")


if __name__ == "__main__":
    unittest.main()
