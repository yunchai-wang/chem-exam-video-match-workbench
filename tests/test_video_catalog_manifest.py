from __future__ import annotations

import csv
import io
import json
import tempfile
import unittest
from pathlib import Path

from scripts.build_video_catalog_manifest import build_catalog_records, normalize_title, parse_export


def export_payload(path: Path, rows: list[tuple[int, list[str]]], columns: list[str]) -> None:
    chunks = []
    for row_number, values in rows:
        stream = io.StringIO()
        csv.writer(stream, lineterminator="\n").writerow(values)
        chunks.append(f"[row={row_number}] {stream.getvalue()}")
    path.write_text(json.dumps({
        "annotated_csv": "".join(chunks), "col_indices": columns,
    }, ensure_ascii=False), encoding="utf-8")


class VideoCatalogManifestTests(unittest.TestCase):
    def test_parser_keeps_multiline_cells_and_real_row_numbers(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "advanced.json"
            export_payload(path, [
                (1, ["课程名称\n（对应 CB）", "", "章", "大节", "视频名称", "来源", "上线", "视频ID"]),
                (2, ["课包", "", "九上", "空气", "测定空气中氧气含量", "新做", "是", "vm-1"]),
            ], ["A", "B", "C", "D", "E", "F", "G", "H"])
            rows, _ = parse_export(path)
            self.assertEqual(rows[1]["A"], "课程名称\n（对应 CB）")
            self.assertEqual(rows[2]["H"], "vm-1")

    def test_catalog_builder_preserves_membership_fields(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "advanced.json"
            columns = [chr(ord("A") + index) for index in range(8)]
            export_payload(path, [
                (1, ["课程", "", "章", "大节", "知识点名称\n（视频名称）", "知识点来源", "上线", "视频ID"]),
                (2, ["重难点", "", "九上", "空气", "测定空气中氧气含量", "教材同步", "是", "vm-1"]),
                (3, ["", "", "", "", "误差分析", "新做", "是", "vm-2"]),
            ], columns)
            records = build_catalog_records("重难点培优", path, {})
            self.assertEqual(len(records), 2)
            self.assertEqual(records[0]["backend_video_id"], "vm-1")
            self.assertEqual(records[0]["explicit_reuse_source"], "教材同步")
            self.assertEqual(records[1]["hierarchy"], "九上 - 空气")
            self.assertEqual(records[1]["video_id"], "ZND-3")

    def test_enriched_evidence_follows_title_not_a_shifted_sheet_row(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "advanced.json"
            columns = [chr(ord("A") + index) for index in range(8)]
            export_payload(path, [
                (1, ["课程", "", "章", "大节", "视频名称", "来源", "上线", "视频ID"]),
                (2, ["重难点", "", "九上", "空气", "新插入的视频", "新做", "是", "vm-new"]),
                (3, ["", "", "", "", "原有视频", "新做", "是", "vm-old"]),
            ], columns)
            evidence = {
                "source": "重难点培优", "source_row": 2, "video_name": "原有视频",
                "transcript_match_status_v5": "强匹配-文件名",
            }
            lookup = {
                ("重难点培优", "row", 2): evidence,
                ("重难点培优", "title", normalize_title("原有视频")): evidence,
            }
            records = build_catalog_records("重难点培优", path, lookup)
            self.assertNotIn("transcript_match_status_v5", records[0])
            self.assertEqual(records[1]["transcript_match_status_v5"], "强匹配-文件名")


if __name__ == "__main__":
    unittest.main()
