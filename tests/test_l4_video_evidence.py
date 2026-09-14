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
