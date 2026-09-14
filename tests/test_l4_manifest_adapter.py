from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from apps.l4_workbench.manifest_adapter import LocalManifestAdapter
from apps.l4_workbench.service import WorkbenchService
from apps.l4_workbench.store import JsonStore


ROOT = Path(__file__).resolve().parents[1]
SEED = ROOT / "sample_data" / "l4_workbench" / "seed.json"


class ManifestAdapterTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.image = self.root / "q01.png"
        self.image.write_bytes(b"\x89PNG\r\n\x1a\nlocal-question-image")
        self.manifest = self.root / "questions.json"
        self.manifest.write_text(json.dumps([
            {
                "question_id": "paper-a-Q01", "paper": "试卷A", "qnum": 1,
                "stem": "1. 根据图示回答问题", "pdf_crop_path": str(self.image),
                "difficulty": "中档", "knowledge_tags": ["水的净化"],
            },
            {
                "question_id": "paper-a-Q02", "paper": "试卷A", "qnum": 2,
                "stem": "2. 第二题", "pdf_crop_path": str(self.root / "missing.png"),
                "difficulty": "较易", "knowledge_tags": ["化学用语"],
            },
        ], ensure_ascii=False), encoding="utf-8")

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_preview_suggests_existing_fields_and_reports_image_coverage(self) -> None:
        preview = LocalManifestAdapter().preview({"path": str(self.manifest), "limit": 1})
        self.assertEqual(preview["record_count"], 2)
        self.assertTrue(preview["has_more"])
        self.assertEqual(preview["suggested_mapping"]["question_text"], "stem")
        self.assertEqual(preview["suggested_mapping"]["question_image"], "pdf_crop_path")
        self.assertEqual(preview["image_report"]["records_with_existing_local_image"], 1)
        self.assertEqual(preview["image_report"]["records_with_missing_local_image"], 1)

    def test_import_freezes_existing_images_and_keeps_missing_image_blocker(self) -> None:
        service = WorkbenchService(JsonStore(self.root / "state.json", SEED))
        result = service.import_manifest({"path": str(self.manifest), "source_label": "本地题库"})
        self.assertEqual(result["snapshot"]["file_count"], 2)
        self.assertEqual(len(result["question_assets"]), 2)
        first, second = result["question_assets"]
        self.assertEqual(first["image_integrity"], "preserved")
        self.assertTrue((self.root / next(block["path"] for block in first["content_blocks"] if block["type"] == "image")).is_file())
        self.assertEqual(first["normalized_fields"]["source_paper"], "试卷A")
        self.assertEqual(second["image_integrity"], "missing")
        self.assertIn("local_image_missing", second["issue_codes"])

    def test_extreme_png_aspect_ratio_is_reported_for_review(self) -> None:
        tall = self.root / "too-tall.png"
        tall.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 8 + (1100).to_bytes(4, "big") + (12000).to_bytes(4, "big"))
        manifest = self.root / "tall.json"
        manifest.write_text(json.dumps([{"question_id": "q-tall", "stem": "1. 题目", "pdf_crop_path": str(tall)}]), encoding="utf-8")
        preview = LocalManifestAdapter().preview({"path": str(manifest)})
        self.assertEqual(preview["image_report"]["suspicious_local_images"], 1)
        service = WorkbenchService(JsonStore(self.root / "tall-state.json", SEED))
        result = service.import_manifest({"path": str(manifest)})
        self.assertIn("image_extreme_aspect_ratio", result["question_assets"][0]["issue_codes"])


if __name__ == "__main__":
    unittest.main()
