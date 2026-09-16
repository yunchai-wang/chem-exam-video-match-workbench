from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from apps.l4_workbench.video_evidence import build_coverage_run
from apps.l4_workbench.video_evidence_index import (
    INDEXED_DINGGAO,
    apply_evidence_index_to_videos,
    build_video_evidence_index,
)


def write_sheet(path: Path, annotated_csv: str) -> None:
    path.write_text(json.dumps({"data": {"annotated_csv": annotated_csv, "revision": 1}}, ensure_ascii=False), encoding="utf-8")


class VideoEvidenceIndexTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.raw = Path(self.temp.name) / "raw"
        self.raw.mkdir()

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_prefers_dinggao_over_collection_and_fills_locator(self) -> None:
        write_sheet(self.raw / "sheet-jG6GT9.csv-get.json", "\n".join([
            "知识点名称（视频名称）,视频ID,视频截图,AI逐字稿匹配状态,AI逐字稿文件",
            "蜡烛燃烧及其探究改进实验题,vid-1,0：25-9：00,已匹配,【定稿】02号+蜡烛燃烧.docx",
        ]))
        (self.raw / "base-chem-pmo.records.ndjson").write_text(json.dumps({
            "fields": {
                "视频名称": "蜡烛燃烧及其探究改进实验题",
                "视频名称 / 文档链接": "[合集](https://guanghe.feishu.cn/docx/abc)",
            }
        }, ensure_ascii=False) + "\n", encoding="utf-8")
        # empty stubs for other configured sources
        for sheet_id in ("sheet-e1mSBg", "sheet-L43kZ6"):
            write_sheet(self.raw / f"{sheet_id}.csv-get.json", "视频名称\n")
        for table_id in ("base-new-zk-b", "base-hard-b-total", "wiki-new-textbook-pmo"):
            (self.raw / f"{table_id}.records.ndjson").write_text("", encoding="utf-8")

        snapshot = build_video_evidence_index(self.raw)
        self.assertGreaterEqual(snapshot["summary"]["entry_count"], 1)
        entry = next(item for item in snapshot["entries"] if "蜡烛" in (item.get("primary_name") or ""))
        self.assertEqual(entry["preferred_transcript"]["kind"], "定稿")
        self.assertIn("0:25-9:00", entry["segment_locators"])

        assets = [{
            "video_id": "vid-1", "video_name": "蜡烛燃烧及其探究改进实验题",
            "alias_video_ids": ["vid-1"], "transcript_status": "未匹配",
            "transcript_evidence": "", "screenshot_tokens": [], "segment_locator": "",
            "issue_codes": ["transcript_not_verified", "video_screenshot_not_materialized"],
            "evidence_level": "E0",
        }]
        join = apply_evidence_index_to_videos(assets, snapshot)
        self.assertEqual(join["matched_assets"], 1)
        self.assertEqual(assets[0]["transcript_status"], INDEXED_DINGGAO)
        self.assertEqual(assets[0]["segment_locator"], "0:25-9:00")
        self.assertEqual(assets[0]["evidence_level"], "E2")
        self.assertTrue(assets[0]["evidence_index"]["preferred_transcript"]["title"].startswith("【定稿】"))

    def test_indexed_dinggao_can_pass_coverage_gate(self) -> None:
        video = {
            "video_id": "v1", "video_name": "溶解度曲线培优", "catalogs": ["重难点"],
            "structural_keys": ["溶解度曲线"], "task_tags": ["信息提取"],
            "transcript_status": INDEXED_DINGGAO, "screenshot_tokens": ["sheet:1:00-2:00"],
            "teaching_target_tags": ["溶解度曲线"], "mentioned_knowledge_tags": [],
            "lesson_mode": "解题课", "knowledge_contract": "problem_lesson_video",
            "prerequisite_knowledge_tags": [], "segment_type": "例题", "segment_locator": "1:00-2:00",
            "evidence_level": "E2", "hierarchy": "溶解度曲线", "content_summary": "溶解度曲线读图",
            "transcript_evidence": "[定稿] x.docx",
            "evidence_index": {"preferred_transcript": {"kind": "定稿", "title": "x.docx", "url": None}},
        }
        diagnostic = {"id": "d1", "label_library_snapshot": {"id": "lib"}, "results": [{
            "asset_id": "q1", "source_name": "卷", "question_no": "1",
            "structural_keys": ["溶解度曲线"], "task_tags": ["信息提取"],
            "tag_profile": {"knowledge": {"core": ["溶解度曲线"]}},
        }]}
        gold = {"id": "g1", "items": [{"asset_id": "q1"}]}
        video_import = {"id": "vi1"}
        run = build_coverage_run(diagnostic, gold, video_import, [video])
        self.assertEqual(run["results"][0]["status"], "部分覆盖候选")
        self.assertTrue(run["results"][0]["candidates"][0]["coverage_candidate"])


if __name__ == "__main__":
    unittest.main()
