from __future__ import annotations

import json
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path

from apps.l4_workbench.service import WorkbenchService
from apps.l4_workbench.store import JsonStore
from apps.l4_workbench.web import make_server


ROOT = Path(__file__).resolve().parents[1]
SEED = ROOT / "sample_data" / "l4_workbench" / "seed.json"


class ApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        service = WorkbenchService(JsonStore(Path(self.temp.name) / "state.json", SEED))
        self.server = make_server(service, "127.0.0.1", 0)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base = f"http://127.0.0.1:{self.server.server_port}"

    def tearDown(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)
        self.temp.cleanup()

    def request(self, path: str, method: str = "GET", payload=None):
        data = json.dumps(payload).encode("utf-8") if payload is not None else None
        request = urllib.request.Request(self.base + path, data=data, method=method)
        if data is not None:
            request.add_header("Content-Type", "application/json")
        with urllib.request.urlopen(request) as response:
            return response.status, json.loads(response.read().decode("utf-8"))

    def test_state_and_run_path(self) -> None:
        status, state = self.request("/api/state")
        self.assertEqual(status, 200)
        self.assertIn("summary", state)
        status, run = self.request("/api/runs", "POST", {})
        self.assertEqual(status, 201)
        self.assertIn(run["status"], {"waiting", "completed"})

    def test_invalid_project_field_is_400(self) -> None:
        request = urllib.request.Request(
            self.base + "/api/project",
            data=json.dumps({"secret_mode": True}).encode("utf-8"),
            method="PATCH",
            headers={"Content-Type": "application/json"},
        )
        with self.assertRaises(urllib.error.HTTPError) as context:
            urllib.request.urlopen(request)
        self.assertEqual(context.exception.code, 400)
        context.exception.close()

    def test_oversized_json_body_is_rejected_before_read(self) -> None:
        request = urllib.request.Request(
            self.base + "/api/project",
            data=b"{}",
            method="PATCH",
            headers={"Content-Type": "application/json", "Content-Length": str(2 * 1024 * 1024 + 1)},
        )
        with self.assertRaises(urllib.error.HTTPError) as context:
            urllib.request.urlopen(request)
        self.assertEqual(context.exception.code, 400)
        context.exception.close()

    def test_publication_request_stays_manual(self) -> None:
        _, state = self.request("/api/state")
        rule_id = state["rules"][-1]["id"]
        status, publication = self.request(f"/api/rules/{rule_id}/request-publication", "POST", {})
        self.assertEqual(status, 201)
        self.assertEqual(publication["status"], "待人工批准")

    def test_remote_snapshot_and_backtest_contract(self) -> None:
        status, snapshot = self.request("/api/source-snapshots", "POST", {
            "source_type": "feishu_base",
            "source_label": "个人闭环 Base",
            "url": "https://example.feishu.cn/base/demo",
            "data_cutoff": "2026-09-10",
        })
        self.assertEqual(status, 201)
        self.assertEqual(snapshot["status"], "pending_adapter")

        status, freeze = self.request("/api/backtests/freezes", "POST", {
            "training_years": [2023, 2024],
            "validation_years": [2025],
            "data_cutoff": "2024-12-31",
            "rule_version": "v1",
            "sample_scope": {"subject": "初中化学"},
            "predictions": [{"entity_id": "a", "predicted_positive": True}],
        })
        self.assertEqual(status, 201)
        status, result = self.request(f"/api/backtests/{freeze['id']}/evaluate", "POST", {
            "observation_year": 2025,
            "observations": [{"entity_id": "a", "actual_positive": True}],
        })
        self.assertEqual(status, 201)
        self.assertEqual(result["precision"], 1.0)

    def test_local_snapshot_can_be_standardized_through_api(self) -> None:
        source = Path(self.temp.name) / "questions.txt"
        source.write_text("1. 第一题\n\n2. 第二题", encoding="utf-8")
        status, snapshot = self.request("/api/source-snapshots", "POST", {
            "source_type": "local_files", "source_label": "本地题目", "paths": [str(source)],
        })
        self.assertEqual(status, 201)
        status, result = self.request(f"/api/source-snapshots/{snapshot['id']}/standardize", "POST", {})
        self.assertEqual(status, 201)
        self.assertEqual(result["run"]["question_asset_count"], 2)
        _, state = self.request("/api/state")
        self.assertEqual(state["summary"]["standardized_asset_count"], 2)

    def test_standardized_image_can_be_served_but_traversal_is_rejected(self) -> None:
        source = Path(self.temp.name) / "question.png"
        source.write_bytes(b"\x89PNG\r\n\x1a\nmock")
        _, snapshot = self.request("/api/source-snapshots", "POST", {
            "source_type": "local_files", "source_label": "题图", "paths": [str(source)],
        })
        _, result = self.request(f"/api/source-snapshots/{snapshot['id']}/standardize", "POST", {})
        image_path = result["question_assets"][0]["content_blocks"][0]["path"]
        with urllib.request.urlopen(f"{self.base}/api/assets/{image_path}") as response:
            self.assertEqual(response.status, 200)
            self.assertEqual(response.read(), source.read_bytes())

        request = urllib.request.Request(self.base + "/api/assets/%2e%2e/secret.txt")
        with self.assertRaises(urllib.error.HTTPError) as context:
            urllib.request.urlopen(request)
        self.assertEqual(context.exception.code, 404)
        context.exception.close()

    def test_local_manifest_preview_and_import_api(self) -> None:
        image = Path(self.temp.name) / "q1.png"
        image.write_bytes(b"\x89PNG\r\n\x1a\nquestion")
        manifest = Path(self.temp.name) / "records.json"
        manifest.write_text(json.dumps([{
            "question_id": "paper-Q1", "paper": "试卷", "qnum": 1,
            "stem": "1. 根据图回答", "pdf_crop_path": str(image),
        }], ensure_ascii=False), encoding="utf-8")
        status, preview = self.request("/api/manifests/previews", "POST", {"path": str(manifest), "limit": 5})
        self.assertEqual(status, 200)
        self.assertEqual(preview["image_report"]["coverage_rate"], 1.0)
        status, result = self.request("/api/manifests/imports", "POST", {
            "path": str(manifest), "source_label": "结构化题库", "mapping": preview["suggested_mapping"],
        })
        self.assertEqual(status, 201)
        self.assertEqual(result["run"]["question_asset_count"], 1)
        self.assertEqual(result["question_assets"][0]["image_integrity"], "preserved")
        status, diagnosis = self.request("/api/diagnostics", "POST", {
            "source_snapshot_id": result["snapshot"]["id"],
        })
        self.assertEqual(status, 201)
        self.assertEqual(diagnosis["summary"]["asset_count"], 1)
        self.assertEqual(diagnosis["results"][0]["trend"]["status"], "证据不足")
        status, sample = self.request("/api/gold-samples", "POST", {
            "diagnostic_run_id": diagnosis["id"], "size": 10,
        })
        self.assertEqual(status, 201)
        self.assertEqual(sample["actual_size"], 1)


if __name__ == "__main__":
    unittest.main()
