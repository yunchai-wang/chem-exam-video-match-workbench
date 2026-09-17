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

    def test_demo_project_without_real_candidates_keeps_dry_run_executors(self) -> None:
        self.service.update_project({"intervention_strategies": {stage: "auto" for stage in STAGES}})
        run = self.service.start_run({"deliverables": ["storyboard"]})
        self.assertEqual(run["status"], "completed")
        modes = {job["stage"]: job["execution_mode"] for job in self.service.get_state()["jobs"] if job["run_id"] == run["id"]}
        self.assertEqual(modes["mother_question"], "dry_run")
        self.assertEqual(modes["storyboard"], "dry_run")
        artifact = self.service.get_state()["artifacts"][-1]
        self.assertIsNone(artifact["skill_packet_job_id"])

    def test_auto_mother_groups_prepare_lesson_plan_without_teacher_gate(self) -> None:
        selection_id = self._seed_selection_run()
        self.service.create_mother_question_run({"selection_run_id": selection_id})
        self.service.update_project({"intervention_strategies": {stage: "auto" for stage in STAGES}})
        run = self.service.start_run({"deliverables": ["lesson_plan"]})
        self.assertEqual(run["status"], "completed")
        self.assertEqual(run["stage_states"]["mother_question"], "completed")
        self.assertEqual(run["stage_states"]["lesson_plan"], "completed")
        mother_job = next(job for job in self.service.get_state()["jobs"] if job["run_id"] == run["id"] and job["stage"] == "mother_question")
        self.assertEqual(mother_job["execution_mode"], "skill_packet")
        self.assertTrue(mother_job["output"]["lesson_plan_ready"])
        self.assertEqual(self.service.get_state()['mother_question_reviews'], [])

    def test_single_group_production_excludes_other_groups_and_keeps_scope_on_revision(self) -> None:
        selection_id = self._seed_selection_run()
        with self.service.store.transaction() as state:
            for candidate in state["selection_runs"][-1]["results"]:
                candidate["ai_next_route"] = "进入课程生产"
            state["selection_runs"][-1]["results"][-1]["structural_keys"] = ["another structure"]
        mother = self.service.create_mother_question_run({"selection_run_id": selection_id})
        self.assertGreater(len(mother["groups"]), 1)
        group = next(g for g in mother["groups"] if not g["exception"])
        self.service.update_project({"intervention_strategies": {stage: "auto" for stage in STAGES}})
        run = self.service.start_run({"deliverables": ["lesson_plan"], "mother_group_ids": [group["id"]]})
        job = next(j for j in self.service.get_state()["jobs"] if j["run_id"] == run["id"] and j["stage"] == "lesson_plan")
        self.assertEqual([g["group_id"] for g in job["output"]["inputs"]["confirmed_groups"]], [group["id"]])
        self.assertEqual(set(run["selected_question_ids"]), {m["asset_id"] for m in group["members"]})
        artifact = self.service.get_state()["artifacts"][-1]
        result = self.service.add_artifact_feedback(artifact["id"], "教案补上判断理由")
        self.assertEqual(result["run"]["mother_group_ids"], [group["id"]])
        all_run = self.service.start_run({"deliverables": ["lesson_plan"]})
        self.assertNotEqual(run["production_input_fingerprint"], all_run["production_input_fingerprint"])
        with self.assertRaisesRegex(ValueError, "题组已变化"):
            self.service.start_run({"mother_group_ids": ["not-a-group"]})

    def test_confirmed_mother_groups_freeze_skill_packets_down_to_storyboard(self) -> None:
        selection_id = self._seed_selection_run()
        mother = self.service.create_mother_question_run({"selection_run_id": selection_id})
        self.service.batch_confirm_mother_question_groups({"mother_question_run_id": mother["id"]})
        self.service.update_project({"intervention_strategies": {stage: "auto" for stage in STAGES}})
        run = self.service.start_run({"deliverables": ["transcript", "html"]})
        self.assertEqual(run["status"], "completed")
        jobs = {job["stage"]: job for job in self.service.get_state()["jobs"] if job["run_id"] == run["id"]}
        for stage in ("mother_question", "lesson_plan", "transcript", "storyboard"):
            self.assertEqual(jobs[stage]["execution_mode"], "skill_packet", stage)
            self.assertFalse(jobs[stage]["output"]["production_ready"])
            self.assertIn("agent_prompt", jobs[stage]["output"])
        mother_packet = jobs["mother_question"]["output"]
        self.assertEqual(mother_packet["inputs"]["confirmed_group_count"], 1)
        self.assertEqual(mother_packet["inputs"]["member_count"], 2)
        self.assertEqual(mother_packet["figure_retention"], {"source_count": 2, "retained_count": 2, "policy": "全部原题图表随题保留"})
        member = mother_packet["inputs"]["confirmed_groups"][0]["members"][0]
        self.assertEqual(member["figures"][0]["path"], f"{member['asset_id']}.png")
        self.assertEqual(jobs["transcript"]["output"]["skills"][0]["skill"], "chemistry-problem-script")
        self.assertEqual(jobs["storyboard"]["output"]["inputs"]["mode"], "html")
        self.assertEqual(jobs["storyboard"]["output"]["skills"][0]["skill"], "onion-problem-storyboard")
        artifacts = [item for item in self.service.get_state()["artifacts"] if item["run_id"] == run["id"]]
        self.assertEqual({item["deliverable_id"] for item in artifacts}, {"transcript", "html"})
        for artifact in artifacts:
            self.assertEqual(artifact["skill_packet_job_id"], jobs[artifact["target_stage"]]["id"])
            self.assertIn("等待 Agent 执行", artifact["status"])

    def test_concept_lesson_type_routes_to_concept_skills(self) -> None:
        selection_id = self._seed_selection_run()
        mother = self.service.create_mother_question_run({"selection_run_id": selection_id})
        self.service.batch_confirm_mother_question_groups({"mother_question_run_id": mother["id"]})
        self.service.update_project({"lesson_type": "concept", "intervention_strategies": {stage: "auto" for stage in STAGES}})
        run = self.service.start_run({"deliverables": ["storyboard"]})
        jobs = {job["stage"]: job for job in self.service.get_state()["jobs"] if job["run_id"] == run["id"]}
        self.assertEqual(jobs["lesson_plan"]["output"]["skills"][0]["skill"], "chemistry-concept-lesson-framework")
        self.assertEqual(jobs["transcript"]["output"]["skills"][0]["skill"], "chemistry-concept-script")
        self.assertEqual(jobs["storyboard"]["output"]["skills"][0]["skill"], "onion-concept-storyboard")
        self.assertEqual(self.service.get_state()["skill_catalog"]["lesson_type"], "concept")

    def test_exception_groups_pause_mother_stage_under_exceptions_strategy(self) -> None:
        selection_id = self._seed_selection_run()
        with self.service.store.transaction() as state:
            run = state["selection_runs"][-1]
            for candidate in run["results"]:
                candidate["tag_profile"]["question"] = []
        mother = self.service.create_mother_question_run({"selection_run_id": selection_id})
        self.assertTrue(mother["groups"][0]["exception"])
        strategies = {stage: "auto" for stage in STAGES}
        strategies["mother_question"] = "exceptions"
        self.service.update_project({"intervention_strategies": strategies})
        run = self.service.start_run({"deliverables": ["lesson_plan"]})
        self.assertEqual(run["status"], "waiting")
        review = self.service.get_state()["reviews"][-1]
        self.assertEqual(review["stage"], "mother_question")
        self.assertEqual(review["item_ids"], [mother["groups"][0]["id"]])

    def test_confirmed_lesson_plan_output_feeds_the_transcript_packet(self) -> None:
        selection_id = self._seed_selection_run()
        mother = self.service.create_mother_question_run({"selection_run_id": selection_id})
        self.service.batch_confirm_mother_question_groups({"mother_question_run_id": mother["id"]})
        self.service.update_project({"intervention_strategies": {stage: "auto" for stage in STAGES}})
        first = self.service.start_run({"deliverables": ["lesson_plan"]})
        plan = next(item for item in self.service.get_state()["artifacts"] if item["run_id"] == first["id"])
        with self.assertRaises(Exception):
            self.service.confirm_artifact(plan["id"], {})
        plan_path = Path(self.temp.name) / "教案初稿.docx"
        plan_path.write_bytes(b"docx")
        registered = self.service.register_artifact_outputs(plan["id"], {
            "skill": "onion-chemistry-course-design-review",
            "outputs": [{"path": str(plan_path), "kind": "docx"}, {"path": str(Path(self.temp.name) / "missing.md"), "kind": "markdown"}],
        })
        self.assertEqual([item["exists_on_register"] for item in registered["outputs"]], [True, False])
        self.assertIn("等待 AI 质量检查", registered["status"])
        confirmed = self.service.confirm_artifact(plan["id"], {"reason": "目标与例题功能核对通过"})
        self.assertEqual(confirmed["status"], "教师已确认 V1")
        self.assertEqual(confirmed["confirmation"]["primary_output_id"], registered["outputs"][0]["id"])

        second = self.service.start_run({"deliverables": ["transcript"]})
        transcript_job = next(job for job in self.service.get_state()["jobs"] if job["run_id"] == second["id"] and job["stage"] == "transcript")
        inputs = transcript_job["output"]["inputs"]
        self.assertTrue(inputs["lesson_plan_confirmed"])
        self.assertEqual(inputs["lesson_plan_document"], registered["outputs"][0]["stored_path"])
        self.assertEqual(inputs["lesson_plan_artifact_id"], plan["id"])
        self.assertIn("已找到教师确认的教案", transcript_job["output"]["gates"][0])

    def test_feedback_rerun_invalidates_a_previous_confirmation(self) -> None:
        self.service.update_project({"intervention_strategies": {stage: "auto" for stage in STAGES}})
        run = self.service.start_run({"deliverables": ["lesson_plan"]})
        artifact = next(item for item in self.service.get_state()["artifacts"] if item["run_id"] == run["id"])
        path = Path(self.temp.name) / "plan.docx"
        path.write_bytes(b"x")
        self.service.register_artifact_outputs(artifact["id"], {"outputs": [{"path": str(path), "kind": "docx"}]})
        self.service.confirm_artifact(artifact["id"], {})
        result = self.service.add_artifact_feedback(artifact["id"], "教案里例题顺序不对")
        self.assertIsNone(result["artifact"]["confirmation"])
        self.assertEqual(result["artifact"]["version"], 2)
        with self.assertRaises(Exception):
            self.service.confirm_artifact(artifact["id"], {})

    def test_registered_output_preserves_original_bytes_and_detects_archive_changes(self) -> None:
        self.service.update_project({"intervention_strategies": {stage: "auto" for stage in STAGES}})
        self.service.start_run({"deliverables": ["lesson_plan"]})
        artifact = self.service.get_state()["artifacts"][-1]
        source = Path(self.temp.name) / "plan.md"
        source.write_text("Original lesson", encoding="utf-8")
        output = self.service.register_artifact_outputs(artifact["id"], {
            "outputs": [{"path": str(source), "kind": "markdown"}],
        })["outputs"][0]
        source.write_text("External edit", encoding="utf-8")
        stored, name = self.service.artifact_output_file(artifact["id"], output["id"])
        self.assertEqual(stored.read_text(), "Original lesson")
        self.assertEqual(name, "plan.md")
        self.service.confirm_artifact(artifact["id"], {})
        stored.write_text("Tampered archive", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "存档已变化"):
            self.service.artifact_output_file(artifact["id"], output["id"])
        with self.assertRaisesRegex(ValueError, "存档已变化"):
            self.service.confirm_artifact(artifact["id"], {})

    def test_repeated_feedback_keeps_real_skill_packet_and_previous_version_context(self) -> None:
        selection_id = self._seed_selection_run()
        self.service.create_mother_question_run({"selection_run_id": selection_id})
        self.service.update_project({"intervention_strategies": {stage: "auto" for stage in STAGES}})
        run = self.service.start_run({"deliverables": ["lesson_plan", "transcript"]})
        artifact = next(a for a in self.service.get_state()["artifacts"] if a["run_id"] == run["id"] and a["target_stage"] == "lesson_plan")
        previous_run = run["id"]
        for version in (1, 2):
            source = Path(self.temp.name) / "plan.md"
            source.write_text(f"Plan V{version}", encoding="utf-8")
            output = self.service.register_artifact_outputs(artifact["id"], {
                "outputs": [{"path": str(source), "kind": "markdown"}],
            })["outputs"][-1]
            self.service.confirm_artifact(artifact["id"], {})
            feedback = f"教案第{version}处缺少控制变量的理由"
            result = self.service.add_artifact_feedback(artifact["id"], feedback)
            self.assertEqual(result["artifact"]["version"], version + 1)
            self.assertIsNone(result["artifact"]["confirmation"])
            self.assertEqual(result["run"]["parent_run_id"], previous_run)
            jobs = [j for j in self.service.get_state()["jobs"] if j["run_id"] == result["run"]["id"]]
            self.assertEqual([j["stage"] for j in jobs], ["lesson_plan"])
            self.assertEqual(jobs[0]["execution_mode"], "skill_packet")
            self.assertEqual(result["artifact"]["skill_packet_job_id"], jobs[0]["id"])
            self.assertIn(feedback, jobs[0]["output"]["agent_prompt"])
            context = jobs[0]["output"]["revision_context"]
            self.assertEqual(context["previous_version"], version)
            self.assertEqual(context["previous_outputs"][0]["stored_path"], output["stored_path"])
            self.assertEqual(context["previous_confirmation"]["version"], version)
            previous_run = result["run"]["id"]
        first_output = self.service.get_state()["artifacts"][0]["outputs"][0]
        self.assertEqual(Path(first_output["stored_path"]).read_text(), "Plan V1")

    def test_failed_revision_still_invalidates_confirmation(self) -> None:
        self.service.update_project({"intervention_strategies": {stage: "auto" for stage in STAGES}})
        self.service.start_run({"deliverables": ["lesson_plan"]})
        artifact = self.service.get_state()["artifacts"][-1]
        source = Path(self.temp.name) / "plan.md"
        source.write_text("Original", encoding="utf-8")
        self.service.register_artifact_outputs(artifact["id"], {"outputs": [{"path": str(source), "kind": "markdown"}]})
        self.service.confirm_artifact(artifact["id"], {})
        def fail(state, run):
            raise RuntimeError("executor unavailable")
        self.service.jobs.executors["lesson_plan"] = fail
        result = self.service.add_artifact_feedback(artifact["id"], "教案需要修改")
        self.assertEqual(result["run"]["status"], "failed")
        self.assertIsNone(result["artifact"]["confirmation"])
        self.assertEqual(result["artifact"]["version"], 2)
        self.assertEqual(result["artifact"]["revision_notes"][-1]["previous_confirmation"]["version"], 1)

    def test_quality_review_obeys_strategy_requires_evidence_and_withdraws_failed_acceptance(self) -> None:
        self.service.update_project({"intervention_strategies": {stage: "auto" for stage in STAGES}})
        self.service.start_run({"deliverables": ["lesson_plan"]})
        artifact = self.service.get_state()["artifacts"][-1]
        source = Path(self.temp.name) / "plan.md"
        source.write_text("Lesson draft", encoding="utf-8")
        output = self.service.register_artifact_outputs(artifact["id"], {"outputs": [{"path": str(source), "kind": "markdown"}]})["outputs"][0]
        checks = {key: {"status": "pass", "evidence": f"Checked {key} against fixture"} for key in (
            "scientific_accuracy", "source_fidelity", "figure_retention", "teaching_alignment", "feedback_resolution")}
        request = {"primary_output_id": output["id"], "reviewed_by": "test-agent", "checks": checks}
        with self.assertRaisesRegex(ValueError, "五项检查"):
            self.service.review_artifact_quality(artifact["id"], {**request, "checks": {}})
        accepted = self.service.review_artifact_quality(artifact["id"], request)
        self.assertEqual(accepted["confirmation"]["confirmed_by"], "agent_quality_check")
        self.assertEqual(accepted["quality_reviews"][-1]["sha256"], output["sha256"])
        self.service.update_project({"intervention_strategies": {"lesson_plan": "confirm"}})
        waiting = self.service.review_artifact_quality(artifact["id"], request)
        self.assertIsNone(waiting["confirmation"])
        self.assertIn("教师确认", waiting["status"])
        self.service.update_project({"intervention_strategies": {"lesson_plan": "auto"}})
        checks["scientific_accuracy"] = {"status": "fail", "evidence": "Equation not balanced"}
        failed = self.service.review_artifact_quality(artifact["id"], request)
        self.assertIsNone(failed["confirmation"])
        self.assertIn("需修改", failed["status"])

    def test_docx_exports_cover_selection_question_set_and_mother_run(self) -> None:
        selection_id = self._seed_selection_run()
        mother = self.service.create_mother_question_run({"selection_run_id": selection_id})
        with self.service.store.transaction() as state:
            for candidate in state["selection_runs"][-1]["results"]:
                candidate.update({
                    "ai_role_labels": ["核心例题"], "ai_usage_scenarios": ["习题册"], "title": candidate["id"],
                    "frequency": {"level": "高频", "numerator": 6, "denominator": 10, "reason": "6/10"},
                    "quality": {"recommendation": "AI候选好题", "reason": "3/4", "score": 4},
                    "coverage": {"status": "证据不足", "reason": "无强证据"},
                    "production_priority": {"recommendation": "P2", "status": "教研预测", "reason": "两项成立"},
                })
            state["selection_runs"][-1]["rule_version"] = "production-selection-v0.3"
            state["selection_runs"][-1]["summary"] = {"evaluated_count": 3, "high_frequency_count": 3, "good_question_count": 3, "high_frequency_and_good_count": 3, "p1_count": 0, "p2_count": 3, "p3_count": 0, "not_produce_count": 0}
            state["selection_runs"][-1]["evidence_limits"] = []
        task = self.service.create_downstream_task({"selection_run_id": selection_id, "task_type": "习题册", "candidate_ids": ["candidate-mq-a", "candidate-mq-b"]})
        for exporter, identifier in (
            (self.service.export_selection_docx, selection_id),
            (self.service.export_question_set_docx, task["question_set"]["id"]),
            (self.service.export_mother_question_docx, mother["id"]),
        ):
            payload, filename, report = exporter(identifier)
            self.assertTrue(filename.endswith(".docx"))
            self.assertEqual(payload[:2], b"PK")
            self.assertGreater(report["figure_count"], 0)
            self.assertEqual(report["missing_figure_count"], report["figure_count"])  # seeded assets have no real image files

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
