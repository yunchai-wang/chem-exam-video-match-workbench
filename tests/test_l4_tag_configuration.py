from __future__ import annotations

import unittest

from apps.l4_workbench.domain import ValidationError
from apps.l4_workbench.tag_configuration import build_tag_configuration
from apps.l4_workbench.standardization import apply_answer_section_boundary


class TagConfigurationTests(unittest.TestCase):
    def test_no_label_mode_builds_non_blocking_ai_baseline(self) -> None:
        config = build_tag_configuration({
            "name": "物理期末题库初版",
            "subject": "初中物理",
            "onboarding_mode": "no_labels",
            "selected_dimensions": ["knowledge", "question"],
        })
        self.assertEqual(config["status"], "AI初版·可运行")
        self.assertTrue(config["ai_fill_missing"])
        self.assertFalse(config["blocks_pipeline"])
        self.assertEqual(config["source_field_mapping"], {})

    def test_partial_mode_preserves_source_mapping_and_project_extensions(self) -> None:
        config = build_tag_configuration({
            "name": "化学既有表接入",
            "subject": "初中化学",
            "onboarding_mode": "partial_labels",
            "selected_dimensions": ["knowledge", "question_type"],
            "source_field_mapping": {"旧知识点": "knowledge", "考法": "question"},
            "custom_dimensions": ["实验名称", "概念选项归一标签"],
        })
        self.assertEqual(config["source_field_mapping"]["旧知识点"], "knowledge")
        self.assertEqual(config["project_extensions"], ["实验名称", "概念选项归一标签"])
        self.assertEqual(config["unmatched_label_policy"], "进入待映射队列，不静默丢弃")
        self.assertTrue(config["ai_fill_missing"])

    def test_rich_mode_requires_at_least_one_explicit_mapping(self) -> None:
        with self.assertRaises(ValidationError):
            build_tag_configuration({
                "name": "完整标签库",
                "subject": "初中化学",
                "onboarding_mode": "rich_labels",
                "selected_dimensions": ["knowledge"],
            })

    def test_answer_heading_stops_following_blocks_but_not_inline_phrase(self) -> None:
        blocks = [
            {"type": "text", "text": "1. 阅读答案与解析栏目后回答问题", "sequence": 1},
            {"type": "text", "text": "2. 第二题", "sequence": 2},
            {"type": "text", "text": "答案与解析\n1.A 2.B", "sequence": 3},
            {"type": "text", "text": "后续答案", "sequence": 4},
        ]
        kept, boundary = apply_answer_section_boundary(blocks)
        self.assertEqual([item["sequence"] for item in kept], [1, 2])
        self.assertEqual(boundary["heading"], "答案与解析")


if __name__ == "__main__":
    unittest.main()
