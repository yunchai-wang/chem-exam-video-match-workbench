from __future__ import annotations

import unittest

from apps.l4_workbench.video_evidence import build_coverage_run, build_video_import


class VideoEvidenceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.snapshot = {"id": "snapshot-video", "immutable_checksum": "video-checksum"}

    def test_video_import_deduplicates_and_keeps_evidence_limits(self) -> None:
        rows = [
            {"video_id": "V1", "video_name": "控制变量", "signatures": "控制变量实验", "task_tags": "设计方案", "transcript_match_status_v5": "未匹配", "promotion_visibility": "截图一眼像"},
            {"video_id": "V1", "video_name": "控制变量", "signatures": "控制变量实验", "task_tags": "设计方案", "transcript_match_status_v5": "强匹配-文件名", "screenshot_tokens": "X:token", "promotion_visibility": "截图一眼像"},
        ]
        result = build_video_import(self.snapshot, rows)
        self.assertEqual(result["video_asset_count"], 1)
        self.assertEqual(result["duplicate_video_ids"], ["V1"])
        self.assertEqual(result["video_assets"][0]["transcript_status"], "强匹配-文件名")
        self.assertNotIn("promotion_visibility", result["video_assets"][0])

    def test_cross_catalog_same_title_becomes_one_candidate_with_all_memberships(self) -> None:
        rows = [
            {
                "video_id": "SYNC-12", "video_name": "金属与酸反应图象（上）", "source": "教材同步课",
                "sheet_id": "sync", "source_row": 12, "backend_video_id": "vm-sync-12",
                "signatures": "金属与酸图像", "task_tags": "信息提取",
                "transcript_match_status_v5": "强匹配-文件名", "screenshot_tokens": "X:token",
            },
            {
                "video_id": "REVIEW-3", "video_name": "金属与酸反应图像（上）", "source": "中考总复习培优",
                "sheet_id": "review", "source_row": 3,
                "signatures": "金属与酸图像", "task_tags": "信息提取",
                "transcript_match_status_v5": "强匹配-文件名", "screenshot_tokens": "Y:token",
            },
        ]
        result = build_video_import(self.snapshot, rows)
        self.assertEqual(result["listing_count"], 2)
        self.assertEqual(result["video_asset_count"], 1)
        self.assertEqual(result["cross_catalog_entity_count"], 1)
        self.assertEqual(result["candidate_cross_catalog_entity_count"], 1)
        self.assertEqual(result["confirmed_cross_catalog_entity_count"], 0)
        asset = result["video_assets"][0]
        self.assertEqual(asset["catalogs"], ["中考总复习培优", "教材同步课"])
        self.assertEqual(asset["alias_video_ids"], ["REVIEW-3", "SYNC-12"])
        self.assertEqual(len(asset["catalog_memberships"]), 2)
        self.assertEqual(asset["identity_status"], "候选同一视频")

    def test_explicit_cross_catalog_reuse_confirms_the_identity(self) -> None:
        result = build_video_import(self.snapshot, [
            {"video_id": "SYNC-1", "video_name": "化学符号的意义", "source": "教材同步", "explicit_reuse_source": "中考同步共用"},
            {"video_id": "REVIEW-1", "video_name": "化学符号的意义", "source": "新中考培优"},
        ])
        self.assertEqual(result["confirmed_cross_catalog_entity_count"], 1)
        self.assertEqual(result["candidate_cross_catalog_entity_count"], 0)
        self.assertEqual(result["video_assets"][0]["identity_status"], "已确认同一视频")
        self.assertNotIn("cross_catalog_identity_needs_review", result["video_assets"][0]["issue_codes"])

    def test_conservative_coverage_requires_structure_task_and_strong_transcript(self) -> None:
        video_import = build_video_import(self.snapshot, [
            {"video_id": "V1", "video_name": "控制变量", "signatures": "控制变量实验", "task_tags": "设计方案", "transcript_match_status_v5": "强匹配-文件名", "screenshot_tokens": "X:token"},
            {"video_id": "V2", "video_name": "控制变量信息提取", "signatures": "控制变量实验", "task_tags": "信息提取", "transcript_match_status_v5": "强匹配-文件名", "screenshot_tokens": "X:token"},
        ])
        diagnostic = {
            "id": "diagnosis-1",
            "results": [{"asset_id": "q1", "source_name": "2026 A卷", "question_no": "1", "structural_keys": ["控制变量实验"], "task_tags": ["设计方案"]}],
        }
        sample = {"id": "sample-1", "items": [{"asset_id": "q1"}]}
        run = build_coverage_run(diagnostic, sample, {key: value for key, value in video_import.items() if key != "video_assets"}, video_import["video_assets"])
        self.assertEqual(run["results"][0]["status"], "部分覆盖候选")
        self.assertTrue(run["results"][0]["candidates"][0]["coverage_candidate"])
        self.assertFalse(run["results"][0]["candidates"][1]["coverage_candidate"])
        self.assertIn("原宣传匹配结论未被复用", run["evidence_limits"][0])

    def test_same_structure_without_task_match_is_not_coverage(self) -> None:
        video_import = build_video_import(self.snapshot, [{
            "video_id": "V1", "video_name": "控制变量信息提取", "signatures": "控制变量实验",
            "task_tags": "信息提取", "transcript_match_status_v5": "强匹配-文件名", "screenshot_tokens": "X:token",
        }])
        diagnostic = {
            "id": "diagnosis-1",
            "results": [{"asset_id": "q1", "source_name": "2026 A卷", "question_no": "1", "structural_keys": ["控制变量实验"], "task_tags": ["设计方案"]}],
        }
        sample = {"id": "sample-1", "items": [{"asset_id": "q1"}]}
        run = build_coverage_run(diagnostic, sample, {key: value for key, value in video_import.items() if key != "video_assets"}, video_import["video_assets"])
        self.assertEqual(run["results"][0]["status"], "证据不足")
        self.assertIn("未通过设问任务门禁", run["results"][0]["candidates"][0]["reason"])

    def test_manifest_structure_label_without_content_support_is_rejected(self) -> None:
        video_import = build_video_import(self.snapshot, [{
            "video_id": "V1", "video_name": "探究可燃物燃烧的条件",
            "hierarchy": "跨学科实践活动 - 初识推断题",
            "content_summary": "通过对照实验探究燃烧三要素。",
            "signatures": "实验室制取氧气、燃烧条件探究",
            "task_tags": "设计方案", "transcript_match_status_v5": "强匹配-文件名",
            "screenshot_tokens": "X:token",
        }])
        diagnostic = {
            "id": "diagnosis-1",
            "results": [{
                "asset_id": "q1", "source_name": "2026 A卷", "question_no": "1",
                "structural_keys": ["实验室制取氧气"], "task_tags": ["设计方案"],
            }],
        }
        sample = {"id": "sample-1", "items": [{"asset_id": "q1"}]}
        run = build_coverage_run(
            diagnostic,
            sample,
            {key: value for key, value in video_import.items() if key != "video_assets"},
            video_import["video_assets"],
        )
        self.assertEqual(run["results"][0]["status"], "未发现可核验证据")
        self.assertEqual(run["results"][0]["candidates"], [])


if __name__ == "__main__":
    unittest.main()
