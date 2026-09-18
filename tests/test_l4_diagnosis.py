from __future__ import annotations

import unittest

from apps.l4_workbench.diagnosis import (
    apply_project_question_filters,
    assess_memory_dependence,
    build_diagnostic_run,
    collect_innovation,
    quantify_difficulty,
    select_gold_sample,
)
from apps.l4_workbench.tagging import build_tag_profile


def asset(identifier: str, paper: str, signature: str, *, difficulty: int = 3, visual: bool = True, issue: str | None = None, **extra_fields):
    fields = {
        "primary_type": "科学探究题" if difficulty >= 4 else "基础题",
        "difficulty": difficulty,
        "has_visual": visual,
        "signatures": signature,
        "method_skeleton": signature or "knowledge优先",
        "task_tags": "解释原因、设计方案" if difficulty >= 3 else "判断入口",
        "knowledge_tags": "水与净化、实验探究与控制变量",
        "visual_forms": "装置图、表格" if visual else "",
    }
    fields.update(extra_fields)
    return {
        "id": identifier,
        "source_snapshot_id": "snapshot-1",
        "source_name": paper,
        "question_no": identifier.removeprefix("q"),
        "raw_text": f"{identifier}. 根据材料分析并回答问题",
        "image_integrity": "preserved" if visual else "no_visual_declared",
        "issue_codes": [issue] if issue else [],
        "source_fields": fields,
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

    def test_overly_broad_structure_cannot_claim_frequency_or_typicality(self) -> None:
        # At corpus scale (>=50 assets) one coarse structure on 20 of 60 questions across
        # 20 papers passes the raw high-frequency thresholds, but a structure covering
        # >=10% of the run is a knowledge block, not a question type.
        assets = [asset(f"q{i}", f"2026 卷{i}", "串并联识别") for i in range(1, 21)]
        assets += [asset(f"q{i}", f"2026 卷{i}", f"细结构{i}") for i in range(21, 61)]
        run = build_diagnostic_run(self.snapshot, assets)
        by_id = {item["asset_id"]: item for item in run["results"]}
        coarse = by_id["q1"]["frequency"]
        self.assertEqual(coarse["level"], "不可判断")
        self.assertEqual(coarse["granularity"]["status"], "过粗")
        self.assertTrue(coarse["granularity"]["guard_armed"])
        self.assertIn("粒度接近知识板块", coarse["reason"])
        self.assertEqual(by_id["q1"]["quality"]["dimensions"]["典型性"]["status"], "证据不足")
        self.assertEqual(by_id["q21"]["frequency"]["granularity"]["status"], "正常")

    def test_granularity_guard_stays_disarmed_on_small_runs(self) -> None:
        assets = [asset("q1", "2026 A卷", "控制变量实验"), asset("q2", "2026 B卷", "控制变量实验"), asset("q3", "2026 C卷", "")]
        run = build_diagnostic_run(self.snapshot, assets)
        result = {item["asset_id"]: item for item in run["results"]}["q1"]
        self.assertFalse(result["frequency"]["granularity"]["guard_armed"])
        self.assertEqual(result["frequency"]["level"], "中频")

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


class DifficultyQuantificationTests(unittest.TestCase):
    def _profile(self, **fields):
        return build_tag_profile(fields)

    def test_explicit_55_95_value_is_snapped_with_high_confidence(self) -> None:
        result = quantify_difficulty({"difficulty": 88}, self._profile())
        self.assertEqual(result["score"], 85)
        self.assertEqual(result["band"], "较难")
        self.assertEqual(result["source"], "explicit-55-95")
        self.assertEqual(result["confidence"], "高")

    def test_legacy_1_to_5_and_stars_map_onto_scale(self) -> None:
        self.assertEqual(quantify_difficulty({"difficulty": 3}, self._profile())["score"], 75)
        starred = quantify_difficulty({"difficulty": "★★★★"}, self._profile())
        self.assertEqual(starred["score"], 85)
        self.assertEqual(starred["source"], "stars")

    def test_estimate_from_steps_and_knowledge_is_low_confidence(self) -> None:
        profile = self._profile(
            question_tags="判断、解释、设计、评价、计算",
            core_knowledge_tags="a、b、c、d、e、f",
        )
        result = quantify_difficulty({}, profile)
        self.assertEqual(result["source"], "estimated-steps-knowledge")
        self.assertEqual(result["confidence"], "低")
        self.assertEqual(result["score"], 95)

    def test_knowledge_breadth_alone_does_not_estimate_difficulty(self) -> None:
        # Only chapter-level knowledge tags, no task/solution/method signal: must stay unknown,
        # not collapse to the easiest band (surfaced by the physics Base run).
        result = quantify_difficulty({}, self._profile(knowledge_tags="第十五章 电流和电路、第十八章 电功率"))
        self.assertIsNone(result["score"])
        self.assertEqual(result["source"], "unknown")
        self.assertIn("没有任务/解法/思想方法信号", result["reason"])

    def test_quantified_difficulty_drives_cognitive_dimension(self) -> None:
        snapshot = {"id": "snapshot-1", "immutable_checksum": "abc"}
        easy = asset("q1", "2026 A卷", "控制变量实验", difficulty=2)
        run = build_diagnostic_run(snapshot, [easy])
        result = run["results"][0]
        self.assertEqual(result["difficulty_profile"]["score"], 65)
        self.assertEqual(result["quality"]["dimensions"]["认知价值"]["status"], "证据不足")


class InnovationTagTests(unittest.TestCase):
    def test_innovation_tags_collected_and_fillers_dropped(self) -> None:
        innovation = collect_innovation({
            "new_material": "工业流程情境",
            "new_form": "价类图",
            "new_questioning": "不符合",
            "new_question_type": "跨学科实践",
        })
        self.assertTrue(innovation["has_innovation"])
        self.assertIn("价类图", innovation["tags"])
        self.assertEqual(innovation["new_questioning"], [])

    def test_innovation_does_not_change_good_question_score(self) -> None:
        snapshot = {"id": "snapshot-1", "immutable_checksum": "abc"}
        plain = asset("q1", "2026 A卷", "控制变量实验")
        flashy = asset("q2", "2026 A卷", "控制变量实验", new_form="价类图", new_material="真实工业情境")
        run = build_diagnostic_run(snapshot, [plain, flashy])
        by_id = {item["asset_id"]: item for item in run["results"]}
        self.assertTrue(by_id["q2"]["innovation"]["has_innovation"])
        self.assertFalse(by_id["q1"]["innovation"]["has_innovation"])
        self.assertEqual(by_id["q1"]["quality"]["score"], by_id["q2"]["quality"]["score"])


class MemoryDependenceTests(unittest.TestCase):
    def _profile(self, **fields):
        return build_tag_profile(fields)

    def test_reasoning_question_labelled_reasoning_type(self) -> None:
        profile = self._profile(
            primary_type="科学探究题",
            question_tags="解释原因、设计方案",
            thinking_method_tags="控制变量",
        )
        result = assess_memory_dependence({"answer_analysis": "..."}, profile, 85)
        self.assertEqual(result["level"], "推理/信息提取型")
        self.assertEqual(result["confidence"], "高")

    def test_basic_recall_question_labelled_memory_type(self) -> None:
        profile = self._profile(primary_type="基础题", question_tags="记忆概念")
        result = assess_memory_dependence({}, profile, 55)
        self.assertEqual(result["level"], "记忆型")

    def test_confidence_downgraded_without_explanation(self) -> None:
        profile = self._profile(primary_type="科学探究题", question_tags="解释、设计", thinking_method_tags="建模")
        result = assess_memory_dependence({}, profile, 85)
        self.assertEqual(result["confidence"], "低")
        self.assertFalse(result["explanation_available"])

    def test_memory_tag_never_vetoes_good_question(self) -> None:
        snapshot = {"id": "snapshot-1", "immutable_checksum": "abc"}
        assets = [asset("q1", "2026 A卷", "控制变量实验", difficulty=4), asset("q2", "2026 B卷", "控制变量实验", difficulty=4)]
        run = build_diagnostic_run(snapshot, assets)
        result = {item["asset_id"]: item for item in run["results"]}["q1"]
        self.assertIn(result["memory_dependence"]["level"], {"记忆型", "记忆+推理", "推理/信息提取型"})
        # verdict comes only from the four scored dimensions, not from the memory tag
        self.assertEqual(result["quality"]["score_denominator"], 4)


class ProjectFilterTests(unittest.TestCase):
    def test_filter_keeps_only_requested_memory_levels_and_innovation(self) -> None:
        results = [
            {"asset_id": "a", "memory_dependence": {"level": "记忆型"}, "innovation": {"has_innovation": False}},
            {"asset_id": "b", "memory_dependence": {"level": "推理/信息提取型"}, "innovation": {"has_innovation": True}},
            {"asset_id": "c", "memory_dependence": {"level": "推理/信息提取型"}, "innovation": {"has_innovation": False}},
        ]
        reasoning_only = apply_project_question_filters(results, {"memory_dependence_keep": ["推理/信息提取型"]})
        self.assertEqual({item["asset_id"] for item in reasoning_only}, {"b", "c"})
        innovative = apply_project_question_filters(results, {"require_innovation": True})
        self.assertEqual({item["asset_id"] for item in innovative}, {"b"})
        self.assertEqual(len(apply_project_question_filters(results)), 3)


if __name__ == "__main__":
    unittest.main()
