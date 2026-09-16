from __future__ import annotations

import unittest

from apps.l4_workbench.domain import ValidationError
from apps.l4_workbench.mother_question import build_mother_question_review, build_mother_question_run


def candidate(cid: str, *, key: str | None, question_type: str = "科学探究题", tasks=("解释原因", "设计方案"),
              route: str = "进入课程生产", difficulty: str = "中等", score: int = 4, core=("控制变量法",),
              context=("生活情境",), unit_tasks=None) -> dict:
    units = [{"id": f"{cid}-sub-1", "label": "小问（1）", "kind": "subquestion",
              "tag_profile": {"question": list(unit_tasks)} if unit_tasks is not None else {}}]
    return {
        "id": f"candidate-{cid}", "asset_id": cid, "source_name": f"2026 {cid} 中考", "question_no": "16",
        "title": f"{cid} 题", "units": units, "ai_next_route": route,
        "structural_keys": [key] if key else [],
        "tag_profile": {
            "question_type": question_type, "question": list(tasks), "context": list(context),
            "knowledge": {"core": list(core), "all": list(core)},
        },
        "difficulty": {"level": difficulty}, "quality": {"score": score},
        "frequency": {"numerator": 6},
    }


def asset(cid: str, *, figures: int = 1, integrity: str = "preserved", duplicate: str | None = None) -> dict:
    return {
        "id": cid, "image_integrity": integrity, "duplicate_group_id": duplicate, "issue_codes": [],
        "content_blocks": [{"type": "text", "text": "题干"}] + [{"type": "image", "path": f"{cid}-{i}.png"} for i in range(figures)],
    }


class MotherQuestionRunTests(unittest.TestCase):
    def setUp(self) -> None:
        self.selection = {"id": "selection-1", "diagnostic_run_id": "diagnosis-1", "source_snapshot_id": "snap"}

    def run_with(self, candidates, assets, reviews=()):
        return build_mother_question_run({**self.selection, "results": candidates}, list(reviews), assets)

    def test_same_structure_and_boundary_forms_a_mother_question_with_all_figures(self) -> None:
        run = self.run_with(
            [candidate("a", key="控制变量实验", context=("厨房",)), candidate("b", key="控制变量实验", context=("实验室",), score=3)],
            [asset("a", figures=2), asset("b", figures=1)],
        )
        self.assertEqual(run["summary"]["mother_group_count"], 1)
        group = run["groups"][0]
        self.assertEqual(group["mode"], "整合成母题")
        self.assertEqual(group["proposal"]["anchor_member_id"], "member-candidate-a")
        actions = {member["id"]: member["action"] for member in group["members"]}
        self.assertEqual(actions, {"member-candidate-a": "保留", "member-candidate-b": "合并"})
        self.assertEqual(group["figure_retention"]["source_count"], 3)
        self.assertEqual(group["figure_retention"]["retained_count"], 3)
        self.assertTrue(run["summary"]["figures_fully_retained"])
        self.assertEqual(group["proposal"]["visual_policy"], "并列原图")
        self.assertFalse(group["exception"])

    def test_same_structure_but_different_tasks_becomes_a_progressive_group_not_a_mother(self) -> None:
        run = self.run_with(
            [candidate("a", key="控制变量实验", tasks=("解释原因",), difficulty="较难"),
             candidate("b", key="控制变量实验", tasks=("解释原因", "设计方案"), difficulty="基础")],
            [asset("a"), asset("b")],
        )
        group = run["groups"][0]
        self.assertEqual(group["mode"], "递进题组")
        self.assertEqual(len(group["boundary_clusters"]), 2)
        self.assertEqual([step["difficulty"] for step in group["proposal"]["steps"]], ["基础", "较难"])
        self.assertTrue(all(member["action"] == "保留" for member in group["members"]))

    def test_shared_structure_with_disjoint_tasks_and_knowledge_stays_independent(self) -> None:
        run = self.run_with(
            [candidate("a", key="图像分析", question_type="计算题", tasks=("计算质量",), core=("质量守恒",)),
             candidate("b", key="图像分析", question_type="基础题", tasks=("判断溶解度变化",), core=("溶解度曲线",))],
            [asset("a"), asset("b")],
        )
        self.assertEqual(run["groups"][0]["mode"], "保持独立")

    def test_missing_task_tags_never_merge_and_are_flagged_as_exception(self) -> None:
        run = self.run_with(
            [candidate("a", key="控制变量实验", tasks=()), candidate("b", key="控制变量实验", tasks=())],
            [asset("a"), asset("b")],
        )
        group = run["groups"][0]
        self.assertEqual(group["mode"], "递进题组")
        self.assertIn("作答边界待识别", group["exception_flags"])
        self.assertTrue(group["exception"])

    def test_same_knowledge_without_structure_is_never_grouped(self) -> None:
        run = self.run_with([candidate("a", key=None), candidate("b", key=None)], [asset("a"), asset("b")])
        self.assertEqual(run["summary"]["independent_count"], 2)
        self.assertTrue(all("缺底层结构" in group["exception_flags"] for group in run["groups"]))

    def test_unit_level_tasks_override_whole_question_tasks(self) -> None:
        run = self.run_with(
            [candidate("a", key="控制变量实验", unit_tasks=("设计方案",)), candidate("b", key="控制变量实验", unit_tasks=("解释原因",))],
            [asset("a"), asset("b")],
        )
        group = run["groups"][0]
        self.assertEqual(group["mode"], "递进题组")
        self.assertEqual(group["members"][0]["answer_boundary"]["task_source"], "逐小问标签")

    def test_only_effective_production_routes_enter_and_rewrite_route_is_marked(self) -> None:
        reviews = [{"selection_run_id": "selection-1", "candidate_id": "candidate-c", "decision": "暂不使用", "selected_unit_ids": ["c-sub-1"]}]
        run = self.run_with(
            [candidate("a", key="控制变量实验"), candidate("b", key="控制变量实验", route="进入母题改造"), candidate("c", key="控制变量实验")],
            [asset("a"), asset("b"), asset("c")], reviews,
        )
        self.assertEqual(run["summary"]["eligible_candidate_count"], 2)
        group = run["groups"][0]
        actions = {member["id"]: member["action"] for member in group["members"]}
        self.assertEqual(actions["member-candidate-b"], "改写")
        self.assertNotIn("member-candidate-c", actions)

    def test_duplicate_assets_are_discarded_once_but_kept_as_evidence(self) -> None:
        run = self.run_with(
            [candidate("a", key="控制变量实验"), candidate("b", key="控制变量实验")],
            [asset("a", duplicate="dup-1"), asset("b", duplicate="dup-1")],
        )
        actions = {member["id"]: member["action"] for member in run["groups"][0]["members"]}
        self.assertEqual(sorted(actions.values()), ["保留", "舍弃"])
        self.assertEqual(run["groups"][0]["figure_retention"]["retained_count"], 2)

    def test_incomplete_original_figures_flag_the_group(self) -> None:
        run = self.run_with(
            [candidate("a", key="控制变量实验"), candidate("b", key="控制变量实验")],
            [asset("a"), asset("b", integrity="missing")],
        )
        group = run["groups"][0]
        self.assertIn("原题图不完整", group["exception_flags"])
        self.assertEqual(group["proposal"]["anchor_member_id"], "member-candidate-a")

    def test_falls_back_to_diagnostic_structural_keys_for_older_selection_runs(self) -> None:
        legacy = candidate("a", key=None)
        legacy.pop("structural_keys")
        diagnostic = {"id": "diagnosis-1", "results": [{"asset_id": "a", "structural_keys": ["控制变量实验"]}]}
        run = build_mother_question_run({**self.selection, "results": [legacy]}, [], [asset("a")], diagnostic)
        self.assertEqual(run["groups"][0]["structural_key"], "控制变量实验")

    def test_run_id_is_stable_for_the_same_reviews(self) -> None:
        candidates = [candidate("a", key="控制变量实验"), candidate("b", key="控制变量实验")]
        first = self.run_with(candidates, [asset("a"), asset("b")])
        second = self.run_with(candidates, [asset("a"), asset("b")])
        self.assertEqual(first["id"], second["id"])


class MotherQuestionReviewTests(unittest.TestCase):
    def setUp(self) -> None:
        self.run = build_mother_question_run(
            {"id": "selection-1", "diagnostic_run_id": "diagnosis-1", "source_snapshot_id": "snap",
             "results": [candidate("a", key="控制变量实验"), candidate("b", key="控制变量实验", score=3)]},
            [], [asset("a"), asset("b")],
        )
        self.group = self.run["groups"][0]

    def test_accepting_the_proposal_needs_no_reason_and_opens_the_lesson_plan_gate(self) -> None:
        review = build_mother_question_review(self.run, {"group_id": self.group["id"]})
        self.assertEqual(review["status"], "accepted")
        self.assertEqual(review["mode"], "整合成母题")
        self.assertEqual(review["lesson_plan_gate"], "已确认，可进入教案")
        self.assertTrue(review["figure_retention_confirmed"])
        self.assertFalse(review["ai_flow_blocked"])
        self.assertEqual(review["rule_proposals"], [])

    def test_changing_grouping_requires_a_reason_and_creates_an_isolated_rule_candidate(self) -> None:
        with self.assertRaises(ValidationError):
            build_mother_question_review(self.run, {"group_id": self.group["id"], "decision": "递进题组"})
        review = build_mother_question_review(self.run, {
            "group_id": self.group["id"], "decision": "递进题组", "reason": "第二题作答边界含定量计算",
        })
        self.assertEqual(review["status"], "corrected")
        self.assertEqual(review["rule_proposals"][0]["family"], "mother_question_grouping_rule")
        self.assertEqual(review["rule_proposals"][0]["status"], "隔离实验候选")

    def test_visual_policy_is_a_project_preference_not_a_rule_correction(self) -> None:
        review = build_mother_question_review(self.run, {"group_id": self.group["id"], "visual_policy": "典型原图＋并列附图"})
        self.assertEqual(review["status"], "accepted")
        self.assertTrue(review["visual_adjusted"])
        self.assertEqual(review["preference_signals"][0]["scope"], "当前生产项目")

    def test_cannot_discard_every_question_or_the_anchor(self) -> None:
        actions = {member["id"]: "舍弃" for member in self.group["members"]}
        with self.assertRaises(ValidationError):
            build_mother_question_review(self.run, {"group_id": self.group["id"], "member_actions": actions, "reason": "x"})
        with self.assertRaises(ValidationError):
            build_mother_question_review(self.run, {
                "group_id": self.group["id"], "member_actions": {"member-candidate-a": "舍弃"}, "reason": "x",
            })

    def test_mother_question_needs_two_retained_questions(self) -> None:
        with self.assertRaises(ValidationError):
            build_mother_question_review(self.run, {
                "group_id": self.group["id"], "member_actions": {"member-candidate-b": "舍弃"}, "reason": "b 题图模糊",
            })

    def test_rejects_unknown_group_action_or_policy(self) -> None:
        with self.assertRaises(ValidationError):
            build_mother_question_review(self.run, {"group_id": "group-missing"})
        with self.assertRaises(ValidationError):
            build_mother_question_review(self.run, {"group_id": self.group["id"], "member_actions": {"member-candidate-a": "拼接"}, "reason": "x"})
        with self.assertRaises(ValidationError):
            build_mother_question_review(self.run, {"group_id": self.group["id"], "visual_policy": "自动拼接"})


if __name__ == "__main__":
    unittest.main()
