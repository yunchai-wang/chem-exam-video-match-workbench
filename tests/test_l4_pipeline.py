from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from apps.l4_workbench.domain import ValidationError
from apps.l4_workbench.pipeline import SourceSnapshotManager, sha256_file
from apps.l4_workbench.service import WorkbenchService
from apps.l4_workbench.store import JsonStore


ROOT = Path(__file__).resolve().parents[1]
SEED = ROOT / "sample_data" / "l4_workbench" / "seed.json"


class PipelineTests(unittest.TestCase):
    def test_local_files_are_copied_and_hashed(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            source = root / "试卷.txt"
            source.write_text("原始题目内容", encoding="utf-8")
            manager = SourceSnapshotManager(root / "snapshots")
            snapshot = manager.capture({"source_type": "local_files", "paths": [str(source)], "source_label": "试卷"})
            copied = root / snapshot["files"][0]["relative_path"]
            self.assertTrue(copied.exists())
            self.assertEqual(snapshot["files"][0]["sha256"], sha256_file(source))
            self.assertEqual(snapshot["status"], "frozen")

    def test_folder_ignores_unsupported_and_symlink_files(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            source = root / "source"
            source.mkdir()
            (source / "题目.md").write_text("题目", encoding="utf-8")
            (source / "程序.py").write_text("print('no')", encoding="utf-8")
            (source / "link.md").symlink_to(source / "题目.md")
            snapshot = SourceSnapshotManager(root / "snapshots").capture({"source_type": "local_folder", "path": str(source)})
            self.assertEqual(snapshot["file_count"], 1)
            self.assertEqual(snapshot["files"][0]["name"], "题目.md")

    def test_failed_capture_removes_partial_snapshot(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            source = root / "large.pdf"
            source.write_bytes(b"12345")
            manager = SourceSnapshotManager(root / "snapshots", max_file_bytes=4)
            with self.assertRaises(ValidationError):
                manager.capture({"source_type": "local_files", "paths": [str(source)]})
            self.assertEqual(list((root / "snapshots").glob("snapshot-*")), [])

    def test_remote_snapshot_does_not_fetch_or_accept_credentials(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            manager = SourceSnapshotManager(Path(folder) / "snapshots")
            snapshot = manager.capture({"source_type": "feishu_base", "url": "https://example.feishu.cn/base/demo"})
            self.assertEqual(snapshot["status"], "pending_adapter")
            self.assertEqual(snapshot["file_count"], 0)
            with self.assertRaises(ValidationError):
                manager.capture({"source_type": "feishu_base", "url": "https://example.test", "token": "secret"})

    def test_service_registers_snapshot_in_project_state(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            source = root / "题目.json"
            source.write_text("{}", encoding="utf-8")
            service = WorkbenchService(JsonStore(root / "state.json", SEED))
            snapshot = service.create_source_snapshot({"source_type": "local_files", "paths": [str(source)]})
            self.assertEqual(service.get_state()["source_snapshots"][0]["id"], snapshot["id"])


if __name__ == "__main__":
    unittest.main()
