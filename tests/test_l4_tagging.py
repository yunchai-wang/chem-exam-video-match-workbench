from __future__ import annotations

import unittest

from apps.l4_workbench.tagging import DEFAULT_LABEL_LIBRARY_SNAPSHOT, attach_unit_tag_profiles, build_tag_profile


class TaggingTests(unittest.TestCase):
    def test_registry_freezes_production_prompt_versions_and_cardinality(self) -> None:
        snapshot = DEFAULT_LABEL_LIBRARY_SNAPSHOT
        self.assertEqual(snapshot["prompt_versions"]["question_type"], "1.5")
        self.assertEqual(snapshot["prompt_versions"]["question"], "2.6")
        self.assertEqual(snapshot["prompt_versions"]["knowledge"], "2.1")
        self.assertEqual(snapshot["policies"]["whole_question_type_cardinality"], [1, 1])
        self.assertEqual(snapshot["policies"]["unit_question_tag_cardinality"], [1, 3])

    def test_profile_separates_core_all_and_distractor_knowledge(self) -> None:
        profile = build_tag_profile({
            "knowledge_tags": "单质的概念、化合物的概念",
            "core_knowledge_tags": "单质的概念",
            "distractor_knowledge_tags": "化合物的概念",
            "question_tags": "判断有关说法是否正确",
            "solution_tags": "根据物质分类进行判断",
            "condition_tags": "给出物质类别",
            "context_tags": "物质分类",
            "thinking_method_tags": "分类与归纳、演绎思想",
        })
        self.assertEqual(profile["knowledge"]["all"], ["单质的概念", "化合物的概念"])
        self.assertEqual(profile["knowledge"]["core"], ["单质的概念"])
        self.assertEqual(profile["knowledge"]["distractor"], ["化合物的概念"])
        self.assertEqual(profile["question"], ["判断有关说法是否正确"])
        self.assertEqual(profile["library_snapshot_id"], DEFAULT_LABEL_LIBRARY_SNAPSHOT["id"])

    def test_all_knowledge_is_not_silently_promoted_to_core(self) -> None:
        profile = build_tag_profile({"knowledge_tags": "单质的概念、化合物的概念"})
        self.assertEqual(profile["knowledge"]["core"], [])
        self.assertEqual(profile["knowledge"]["core_status"], "待识别")

    def test_whole_question_type_must_be_one_known_production_label(self) -> None:
        valid = build_tag_profile({"question_type": "科学探究题"})
        ambiguous = build_tag_profile({"question_type": "基础题、计算题"})
        unknown = build_tag_profile({"question_type": "创新题"})
        self.assertEqual(valid["question_type_status"], "有效")
        self.assertEqual(ambiguous["question_type_status"], "需复核")
        self.assertIsNone(ambiguous["question_type"])
        self.assertEqual(unknown["question_type_status"], "需复核")

    def test_subquestion_tags_require_explicit_unit_mapping(self) -> None:
        units = [{"id": "q1-u1", "label": "一"}, {"id": "q1-u2", "label": "二"}]
        tagged = attach_unit_tag_profiles(units, {
            "question_tags": "整题标签不得下沉",
            "unit_tag_profiles": {"一": {"question_tags": "解释实验现象", "core_knowledge_tags": "氧气的助燃性"}},
        })
        self.assertEqual(tagged[0]["tag_profile"]["question"], ["解释实验现象"])
        self.assertEqual(tagged[0]["tag_profile"]["status"], "已提供")
        self.assertEqual(tagged[1]["tag_profile"]["status"], "待逐小问识别")
        self.assertEqual(tagged[1]["tag_profile"]["question"], [])


if __name__ == "__main__":
    unittest.main()
