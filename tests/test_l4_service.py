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

    def test_candidate_pool_run_prunes_later_production_stages(self) -> None:
        self.service.update_project({"intervention_strategies": {stage: "auto" for stage in STAGES}})
        run = self.service.start_run({"deliverables": ["candidate_pool"]})
        self.assertEqual(run["status"], "completed")
        self.assertEqual(run["required_stages"], STAGES[:3])
        self.assertEqual(run["stage_states"]["lesson_plan"], "not_requested")
        jobs = [item["stage"] for item in self.service.get_state()["jobs"] if item["run_id"] == run["id"]]
        self.assertEqual(jobs, STAGES[:3])
        artifact = self.service.get_state()["artifacts"][-1]
        self.assertEqual(artifact["deliverable_id"], "candidate_pool")

    def test_transcript_dependency_does_not_create_lesson_plan_artifact(self) -> None:
        self.service.update_project({"intervention_strategies": {stage: "auto" for stage in STAGES}})
        run = self.service.start_run({"deliverables": ["transcript"]})
        artifacts = [item for item in self.service.get_state()["artifacts"] if item["run_id"] == run["id"]]
        self.assertEqual(run["required_stages"][-1], "transcript")
        self.assertEqual([item["deliverable_id"] for item in artifacts], ["transcript"])

    def test_one_off_output_override_does_not_change_project_default(self) -> None:
        self.service.update_project({
            "default_deliverables": ["lesson_plan"],
            "intervention_strategies": {stage: "auto" for stage in STAGES},
        })
        self.service.start_run({"deliverables": ["candidate_pool"]})
        self.assertEqual(self.service.get_state()["project"]["default_deliverables"], ["lesson_plan"])

        default_run = self.service.start_run()
        self.assertEqual(default_run["requested_deliverables"], ["lesson_plan"])

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

    def _seed_selection_run(self) -> str:
        def candidate(cid: str, route: str = "进入课程生产") -> dict:
            return {
                "id": f"candidate-{cid}", "asset_id": cid, "source_name": f"2026 {cid}", "question_no": "16",
                "title": f"{cid} 题", "units": [{"id": f"{cid}-whole", "label": "整题", "kind": "whole_question"}],
                "ai_next_route": route, "structural_keys": ["控制变量实验"],
                "tag_profile": {"question_type": "科学探究题", "question": ["设计方案"], "context": [], "knowledge": {"core": ["控制变量法"]}},
                "difficulty": {"level": "中等"}, "quality": {"score": 4}, "frequency": {"numerator": 6},
                "production_priority": {"recommendation": "P2"},
            }
        with self.service.store.transaction() as state:
            state["question_assets"].extend([
                {"id": cid, "source_snapshot_id": "snap-mq", "image_integrity": "preserved", "issue_codes": [],
                 "content_blocks": [{"type": "image", "path": f"{cid}.png"}], "fingerprint": cid, "duplicate_group_id": None}
                for cid in ("mq-a", "mq-b", "mq-c")
            ])
            state["selection_runs"].append({
                "id": "selection-mq", "diagnostic_run_id": "diagnosis-mq", "source_snapshot_id": "snap-mq",
                "results": [candidate("mq-a"), candidate("mq-b"), candidate("mq-c", "暂不使用")],
            })
        return "selection-mq"

    def test_mother_question_run_is_idempotent_and_refreshes_after_route_changes(self) -> None:
        selection_id = self._seed_selection_run()
        first = self.service.create_mother_question_run({"selection_run_id": selection_id})
        again = self.service.create_mother_question_run({"selection_run_id": selection_id})
        self.assertEqual(first["id"], again["id"])
        self.assertEqual(first["summary"]["eligible_candidate_count"], 2)
        self.assertEqual(first["summary"]["mother_group_count"], 1)
        self.assertEqual(len(self.service.get_state()["mother_question_runs"]), 1)

        with self.service.store.transaction() as state:
            state["selection_reviews"].append({
                "id": "selection-review-mq", "selection_run_id": selection_id, "candidate_id": "candidate-mq-c",
                "decision": "进入课程生产", "selected_unit_ids": ["mq-c-whole"],
            })
        refreshed = self.service.create_mother_question_run({"selection_run_id": selection_id})
        self.assertNotEqual(refreshed["id"], first["id"])
        self.assertEqual(refreshed["summary"]["eligible_candidate_count"], 3)
        self.assertEqual(self.service.get_state()["summary"]["mother_question_run_count"], 2)

    def test_mother_question_review_and_batch_confirm_skip_corrected_groups(self) -> None:
        selection_id = self._seed_selection_run()
        run = self.service.create_mother_question_run({"selection_run_id": selection_id})
        group = run["groups"][0]
        review = self.service.save_mother_question_review({
            "mother_question_run_id": run["id"], "group_id": group["id"],
            "decision": "递进题组", "reason": "两题作答边界不同",
        })
        self.assertEqual(review["status"], "corrected")
        result = self.service.batch_confirm_mother_question_groups({"mother_question_run_id": run["id"]})
        self.assertEqual(result["passed_count"], 0)
        self.assertEqual(result["skipped_group_ids"], [group["id"]])
        reviews = self.service.get_state()["mother_question_reviews"]
        self.assertEqual(len(reviews), 1)
        self.assertEqual(reviews[0]["mode"], "递进题组")


if __name__ == "__main__":
    unittest.main()
