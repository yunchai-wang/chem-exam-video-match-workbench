from __future__ import annotations

import base64
import tempfile
import unittest
import zipfile
from pathlib import Path

from apps.l4_workbench.service import WorkbenchService
from apps.l4_workbench.store import JsonStore


ROOT = Path(__file__).resolve().parents[1]
SEED = ROOT / "sample_data" / "l4_workbench" / "seed.json"
PNG_1X1 = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAusB9Y9ZQmcAAAAASUVORK5CYII=")


def make_docx(path: Path) -> None:
    document = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"
 xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"
 xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main">
 <w:body>
  <w:p><w:r><w:t>1．观察装置图，回答（1）气体名称；</w:t></w:r></w:p>
  <w:p><w:r><w:drawing><a:blip r:embed="rId1"/></w:drawing></w:r></w:p>
  <w:p><w:r><w:t>（2）说明判断依据。</w:t></w:r></w:p>
  <w:tbl><w:tr><w:tc><w:p><w:r><w:t>现象</w:t></w:r></w:p></w:tc><w:tc><w:p><w:r><w:t>结论</w:t></w:r></w:p></w:tc></w:tr></w:tbl>
 </w:body>
</w:document>"""
    relationships = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
 <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/image" Target="media/image1.png"/>
</Relationships>"""
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("word/document.xml", document)
        archive.writestr("word/_rels/document.xml.rels", relationships)
        archive.writestr("word/media/image1.png", PNG_1X1)


class StandardizationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.service = WorkbenchService(JsonStore(self.root / "state.json", SEED))

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_docx_preserves_text_image_table_and_units(self) -> None:
        source = self.root / "试卷.docx"
        make_docx(source)
        snapshot = self.service.create_source_snapshot({"source_type": "local_files", "paths": [str(source)]})
        result = self.service.standardize_snapshot(snapshot["id"])
        self.assertEqual(result["run"]["question_asset_count"], 1)
        asset = result["question_assets"][0]
        self.assertEqual({item["type"] for item in asset["content_blocks"]}, {"text", "image", "table"})
        self.assertEqual(asset["image_integrity"], "preserved")
        self.assertEqual([unit["label"] for unit in asset["units"]], ["1", "2"])
        image = next(item for item in asset["content_blocks"] if item["type"] == "image")
        self.assertTrue((self.root / image["path"]).is_file())

    def test_standardization_is_idempotent_and_assigns_duplicate_group(self) -> None:
        first = self.root / "a.txt"
        second = self.root / "b.txt"
        first.write_text("1. 相同的题目内容", encoding="utf-8")
        second.write_text("1. 相同的题目内容", encoding="utf-8")
        snapshot = self.service.create_source_snapshot({"source_type": "local_files", "paths": [str(first), str(second)]})
        first_result = self.service.standardize_snapshot(snapshot["id"])
        second_result = self.service.standardize_snapshot(snapshot["id"])
        state = self.service.get_state()
        self.assertEqual(first_result["run"]["id"], second_result["run"]["id"])
        self.assertEqual(len(state["standardization_runs"]), 1)
        self.assertEqual(len(state["question_assets"]), 2)
        self.assertTrue(all(item["duplicate_count"] == 2 for item in state["question_assets"]))

    def test_declared_but_missing_image_is_a_blocker(self) -> None:
        source = self.root / "missing.txt"
        source.write_text("1. 根据[图片：实验装置]回答问题", encoding="utf-8")
        snapshot = self.service.create_source_snapshot({"source_type": "local_files", "paths": [str(source)]})
        result = self.service.standardize_snapshot(snapshot["id"])
        self.assertEqual(result["run"]["status"], "completed_with_blockers")
        self.assertIn("declared_image_missing", result["question_assets"][0]["issue_codes"])


if __name__ == "__main__":
    unittest.main()
