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
    enrich_collection_documents,
    parse_collection_document,
)
from apps.l4_workbench.video_evidence import apply_candidate_exclusions, build_coverage_run



def write_sheet(path: Path, annotated_csv: str) -> None:
    path.write_text(json.dumps({"data": {"annotated_csv": annotated_csv, "revision": 1}}, ensure_ascii=False), encoding="utf-8")


class VideoEvidenceIndexTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.raw = Path(self.temp.name) / "raw"
        self.raw.mkdir()

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_new_textbook_real_field_names_and_all_link_columns_are_read(self):
        rows = [{'视频名称': '净水课', '学科': ['初中化学'],
                 '集合文档': '[集合](https://guanghe.feishu.cn/docx/collection)',
                 '教研素材（文档集合）': '[素材](https://guanghe.feishu.cn/docx/material)',
                 '定稿（或录音稿）': [{'name': '净水录音稿.docx', 'file_token': 'f1'}]},
                {'视频名称': '物理课', '学科': ['初中物理'], '集合文档': '[集合](https://guanghe.feishu.cn/docx/physics)'}]
        (self.raw / 'wiki-new-textbook-pmo.records.ndjson').write_text('\n'.join(json.dumps(x, ensure_ascii=False) for x in rows))
        snapshot = build_video_evidence_index(self.raw)
        entry = next(x for x in snapshot['entries'] if x['primary_name'] == '净水课')
        self.assertEqual(len(entry['transcript_candidates']), 3)
        self.assertEqual(entry['preferred_transcript']['kind'], '录音稿')
        self.assertFalse(any(x['primary_name'] == '物理课' for x in snapshot['entries']))

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
        self.assertEqual(assets[0]["evidence_level"], "E1")
        self.assertTrue(assets[0]["evidence_index"]["preferred_transcript"]["title"].startswith("【定稿】"))

    def test_unopened_dinggao_pointer_cannot_pass_coverage_gate(self) -> None:
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
        self.assertEqual(run["results"][0]["status"], "证据不足")
        self.assertFalse(run["results"][0]["candidates"][0]["coverage_candidate"])

    def test_collection_doc_lifts_dinggao_and_segments(self) -> None:
        xml = """
        <title>合集</title>
        <h3>定稿👇</h3>
        <p><source name="【定稿】示例题.docx" mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document" token="tok1"/></p>
        <p><cite doc-id="doc录音" file-type="docx" title="【录音稿】示例题" type="doc"></cite></p>
        <p><source name="【定稿】示例.pptx" mime="application/vnd.openxmlformats-officedocument.presentationml.presentation" token="ppt1"/></p>
        <h1>视频分片段</h1>
        <table><tr><td><p>0:00-0:34</p></td><td><p>引入</p></td></tr>
        <tr><td><p>0:35-8:00</p></td><td><p>例题</p></td></tr></table>
        """
        parsed = parse_collection_document(xml)
        kinds = {item["kind"] for item in parsed["transcript_pointers"]}
        self.assertIn("定稿", kinds)
        self.assertIn("录音稿", kinds)
        self.assertTrue(all("pptx" not in (item.get("title") or "").lower() or item["kind"] != "定稿" or "docx" in item["title"].lower() for item in parsed["transcript_pointers"]))
        self.assertFalse(any("pptx" in (item.get("title") or "").lower() for item in parsed["transcript_pointers"]))
        self.assertIn("0:00-0:34 引入", parsed["segment_locators"])

        write_sheet(self.raw / "sheet-jG6GT9.csv-get.json", "\n".join([
            "知识点名称（视频名称）,视频ID",
            "NaOH变质后的成分分析题,vid-coll",
        ]))
        for sheet_id in ("sheet-e1mSBg", "sheet-L43kZ6"):
            write_sheet(self.raw / f"{sheet_id}.csv-get.json", "视频名称\n")
        (self.raw / "base-chem-pmo.records.ndjson").write_text(json.dumps({
            "fields": {
                "视频名称": "NaOH变质后的成分分析题",
                "视频名称 / 文档链接": "[合集](https://guanghe.feishu.cn/docx/CollTok123)",
            }
        }, ensure_ascii=False) + "\n", encoding="utf-8")
        for table_id in ("base-new-zk-b", "base-hard-b-total", "wiki-new-textbook-pmo"):
            (self.raw / f"{table_id}.records.ndjson").write_text("", encoding="utf-8")

        collections = self.raw / "collections"
        collections.mkdir()
        (collections / "CollTok123.fetch.json").write_text(json.dumps({
            "data": {"document": {"content": xml}}
        }, ensure_ascii=False), encoding="utf-8")

        snapshot = build_video_evidence_index(self.raw)
        enrichment = enrich_collection_documents(self.raw, snapshot, fetch=False)
        self.assertEqual(enrichment["preferred_upgraded_from_collection"], 1)
        entry = next(item for item in snapshot["entries"] if "NaOH" in (item.get("primary_name") or ""))
        self.assertEqual(entry["preferred_transcript"]["kind"], "定稿")
        self.assertTrue(any("0:00-0:34" in locator for locator in entry["segment_locators"]))
        self.assertEqual(snapshot["sync_version"], "video-evidence-index-v0.4")

        assets = [{
            "video_id": "vid-coll", "video_name": "NaOH变质后的成分分析题",
            "alias_video_ids": ["vid-coll"], "transcript_status": "未匹配",
            "transcript_evidence": "", "screenshot_tokens": [], "segment_locator": "",
            "issue_codes": ["transcript_not_verified"], "evidence_level": "E0",
        }]
        apply_evidence_index_to_videos(assets, snapshot)
        self.assertEqual(assets[0]["transcript_status"], INDEXED_DINGGAO)
        self.assertTrue(str(assets[0]["segment_locator"]).startswith("0:00-0:34"))

    def test_exclude_candidate_recomputes_status(self) -> None:
        video_good = {
            "video_id": "v-good", "video_name": "好视频", "catalogs": ["重难点"],
            "structural_keys": ["溶解度曲线"], "task_tags": ["信息提取"],
            "transcript_status": "强匹配-文件名+正文", "screenshot_tokens": ["s"],
            "teaching_target_tags": ["溶解度曲线"], "mentioned_knowledge_tags": [],
            "lesson_mode": "解题课", "knowledge_contract": "problem_lesson_video",
            "prerequisite_knowledge_tags": [], "segment_type": "例题", "segment_locator": "1:00-2:00",
            "evidence_level": "E2", "hierarchy": "溶解度曲线", "content_summary": "溶解度曲线读图",
            "transcript_evidence": "[定稿] x.docx",
            "evidence_index": {"preferred_transcript": {"kind": "定稿", "title": "x.docx", "url": None}},
        }
        video_bad = {**video_good, "video_id": "v-bad", "video_name": "错配视频"}
        diagnostic = {"id": "d1", "label_library_snapshot": {"id": "lib"}, "results": [{
            "asset_id": "q1", "source_name": "卷", "question_no": "1",
            "structural_keys": ["溶解度曲线"], "task_tags": ["信息提取"],
            "tag_profile": {"knowledge": {"core": ["溶解度曲线"]}},
        }]}
        gold = {"id": "g1", "items": [{"asset_id": "q1"}]}
        video_import = {"id": "vi1"}
        run = build_coverage_run(diagnostic, gold, video_import, [video_good, video_bad])
        self.assertEqual(run["results"][0]["status"], "部分覆盖候选")
        apply_candidate_exclusions(run, [{
            "id": "ex1", "asset_id": "q1", "video_id": "v-good", "reason": "考查小问不同", "status": "excluded",
        }])
        active = [item for item in run["results"][0]["candidates"] if not item.get("excluded")]
        self.assertEqual(len(active), 1)
        self.assertEqual(active[0]["video_id"], "v-bad")
        self.assertEqual(run["exclusion_count"], 1)


if __name__ == "__main__":
    unittest.main()
