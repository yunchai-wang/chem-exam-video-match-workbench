from __future__ import annotations

import struct
import tempfile
import unittest
import zipfile
import zlib
from pathlib import Path

from apps.l4_workbench.docx_writer import DocxBuilder
from apps.l4_workbench.exports import build_mother_question_docx, build_question_set_docx, build_selection_docx
from apps.l4_workbench.mother_question import build_mother_question_run


def tiny_png(width: int = 4, height: int = 3) -> bytes:
    def chunk(kind: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)
    raw = b"".join(b"\x00" + b"\xff\x00\x00" * width for _ in range(height))
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)) + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b"")


def read_docx(payload: bytes) -> tuple[str, list[str]]:
    with zipfile.ZipFile(__import__("io").BytesIO(payload)) as archive:
        return archive.read("word/document.xml").decode("utf-8"), archive.namelist()


class DocxWriterTests(unittest.TestCase):
    def test_builds_valid_package_with_native_aspect_ratio_image(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            image = Path(temp) / "q.png"
            image.write_bytes(tiny_png(400, 100))
            doc = DocxBuilder()
            doc.heading("标题", 1)
            doc.paragraph("题干 <A & B>")
            doc.table([["a", "b"], ["1", "2"]])
            self.assertTrue(doc.image(image, caption="原题图"))
            xml, names = read_docx(doc.to_bytes())
        self.assertIn("word/media/image1.png", names)
        self.assertIn("题干 &lt;A &amp; B&gt;", xml)
        cx = int(xml.split('cx="')[1].split('"')[0])
        cy = int(xml.split('cy="')[1].split('"')[0])
        self.assertAlmostEqual(cx / cy, 4.0, places=2)

    def test_missing_image_is_reported_not_faked(self) -> None:
        doc = DocxBuilder()
        self.assertFalse(doc.image(Path("/nonexistent/q.png")))
        self.assertEqual(doc.missing_images, ["/nonexistent/q.png"])
        xml, names = read_docx(doc.to_bytes())
        self.assertIn("原题图缺失", xml)
        self.assertFalse(any(name.startswith("word/media/") for name in names))


class ExportBuilderTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        (self.root / "snap").mkdir()
        (self.root / "snap" / "a.png").write_bytes(tiny_png())
        (self.root / "snap" / "b.png").write_bytes(tiny_png(2, 2))
        self.assets = [
            {"id": "a", "source_name": "2026 南京", "question_no": "16", "image_integrity": "preserved", "issue_codes": [],
             "content_blocks": [{"type": "text", "text": "题干A"}, {"type": "image", "path": "snap/a.png", "source_locator": "p1"}, {"type": "table", "rows": [["x", "y"], ["1", "2"]]}],
             "tag_profile": {"question_type": "科学探究题", "knowledge": {"core": ["控制变量法"]}, "question": ["设计方案"], "solution": []}},
            {"id": "b", "source_name": "2026 苏州", "question_no": "20", "image_integrity": "missing", "issue_codes": [],
             "content_blocks": [{"type": "text", "text": "题干B"}, {"type": "image", "path": "snap/missing.png"}], "tag_profile": {}},
        ]

    def tearDown(self) -> None:
        self.temp.cleanup()

    def candidate(self, cid: str) -> dict:
        return {
            "id": f"candidate-{cid}", "asset_id": cid, "source_name": f"2026 {cid}", "question_no": "1", "title": cid,
            "units": [{"id": f"{cid}-whole", "label": "整题", "kind": "whole_question"}], "ai_next_route": "进入课程生产",
            "ai_role_labels": ["核心例题"], "ai_usage_scenarios": ["习题册"], "structural_keys": ["控制变量实验"],
            "tag_profile": {"question_type": "科学探究题", "question": ["设计方案"], "context": [], "knowledge": {"core": ["控制变量法"]}},
            "frequency": {"level": "高频", "numerator": 5, "denominator": 10, "reason": "5/10"},
            "quality": {"recommendation": "AI候选好题", "reason": "3/4", "score": 3},
            "difficulty": {"level": "中等"}, "coverage": {"status": "证据不足", "reason": "无强证据"},
            "production_priority": {"recommendation": "P2", "status": "教研预测", "reason": "两项成立"},
        }

    def test_selection_export_embeds_every_figure_and_reports_missing_ones(self) -> None:
        selection = {
            "id": "selection-1", "rule_version": "production-selection-v0.3", "created_at": "t",
            "summary": {"evaluated_count": 2, "high_frequency_count": 2, "good_question_count": 2, "high_frequency_and_good_count": 2,
                        "p1_count": 0, "p2_count": 2, "p3_count": 0, "not_produce_count": 0},
            "evidence_limits": ["无学生数据"], "results": [self.candidate("a"), self.candidate("b")],
        }
        payload, report = build_selection_docx(selection, [], self.assets, self.root)
        xml, names = read_docx(payload)
        self.assertEqual(report["figure_count"], 2)
        self.assertEqual(report["missing_figure_count"], 1)
        self.assertEqual(sum(name.startswith("word/media/") for name in names), 1)
        self.assertIn("题干A", xml)
        self.assertIn("原题图缺失", xml)
        self.assertIn("图片完整性：missing", xml)
        # candidates without the newer dimensions still export with safe placeholders
        self.assertIn("记忆依赖度", xml)
        self.assertIn("待核验", xml)

    def test_selection_export_shows_memory_innovation_and_quantified_difficulty(self) -> None:
        candidate = self.candidate("a")
        candidate["difficulty"] = {"level": "较难", "score": 85, "band": "较难", "confidence": "高", "reason": "依据既有难度值 88 归一到 85"}
        candidate["memory_dependence"] = {"level": "推理/信息提取型", "confidence": "高", "evidence": "命中 4 项推理信号"}
        candidate["innovation"] = {"has_innovation": True, "tags": ["价类图", "真实工业情境"]}
        selection = {
            "id": "selection-2", "rule_version": "production-selection-v0.4", "created_at": "t",
            "summary": {"evaluated_count": 1, "high_frequency_count": 1, "good_question_count": 1, "high_frequency_and_good_count": 1,
                        "p1_count": 0, "p2_count": 1, "p3_count": 0, "not_produce_count": 0},
            "evidence_limits": [], "results": [candidate],
        }
        payload, _ = build_selection_docx(selection, [], self.assets, self.root)
        xml, _ = read_docx(payload)
        self.assertIn("推理/信息提取型", xml)
        self.assertIn("价类图、真实工业情境", xml)
        self.assertIn("85 · 较难 · 高置信", xml)
        self.assertIn("记忆依赖度证据：命中 4 项推理信号", xml)
        self.assertIn("难度量化依据：依据既有难度值 88 归一到 85", xml)

    def test_question_set_export_keeps_tables_and_tags(self) -> None:
        question_set = {
            "id": "question-set-1", "name": "实验探究习题册", "version": "v1", "frozen_at": "t", "immutable_checksum": "abc" * 10,
            "items": [{"asset_id": "a", "source_name": "2026 南京", "question_no": "16", "role_labels": ["核心例题"], "usage_scenarios": ["习题册"],
                       "selected_unit_ids": ["a-whole"], "tag_profile": self.assets[0]["tag_profile"]}],
        }
        payload, report = build_question_set_docx(question_set, {"task_type": "习题册", "status": "契约", "target_students": "中等", "content_scope": "实验"}, self.assets, self.root)
        xml, _ = read_docx(payload)
        self.assertEqual(report["missing_figure_count"], 0)
        self.assertIn("<w:tbl>", xml)
        self.assertIn("控制变量法", xml)
        self.assertIn("实验探究习题册", xml)

    def test_mother_question_export_has_review_area_and_all_originals(self) -> None:
        run = build_mother_question_run(
            {"id": "selection-1", "diagnostic_run_id": "d", "source_snapshot_id": "s", "results": [self.candidate("a"), self.candidate("b")]},
            [], self.assets,
        )
        payload, report = build_mother_question_docx(run, [], self.assets, self.root)
        xml, _ = read_docx(payload)
        self.assertIn("经典母题整合审核稿", xml)
        self.assertIn("教师审核区", xml)
        self.assertIn("待 Skill 补入", xml)
        self.assertEqual(xml.count("原题："), 2)
        self.assertEqual(report["figure_count"], 2)


if __name__ == "__main__":
    unittest.main()
