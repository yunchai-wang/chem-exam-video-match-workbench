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

    def test_tag_configuration_can_be_created_through_api(self) -> None:
        status, config = self.request("/api/tag-configurations", "POST", {
            "name": "已有少量标签",
            "subject": "初中化学",
            "onboarding_mode": "partial_labels",
            "selected_dimensions": ["knowledge", "question_type", "option_concept"],
            "source_field_mapping": {"我的知识点": "knowledge"},
            "custom_dimensions": ["校本专题"],
        })
        self.assertEqual(status, 201)
        self.assertEqual(config["project_extensions"], ["校本专题"])
        _, state = self.request("/api/state")
        self.assertEqual(state["project"]["active_tag_configuration_id"], config["id"])

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
        video_manifest = Path(self.temp.name) / "videos.json"
        video_manifest.write_text(json.dumps([{
            "video_id": "V1", "video_name": "图表分析课", "signatures": "控制变量实验",
            "task_tags": "设计方案", "transcript_match_status_v5": "强匹配-文件名",
            "screenshot_tokens": "X:token",
        }], ensure_ascii=False), encoding="utf-8")
        status, video_import = self.request("/api/video-manifests/imports", "POST", {
            "path": str(video_manifest), "source_label": "视频证据库",
        })
        self.assertEqual(status, 201)
        self.assertEqual(video_import["video_asset_count"], 1)
        status, coverage = self.request("/api/coverage-diagnostics", "POST", {
            "diagnostic_run_id": diagnosis["id"], "gold_sample_id": sample["id"],
            "video_import_id": video_import["id"],
        })
        self.assertEqual(status, 201)
        self.assertEqual(coverage["result_count"], 1)
        _, state = self.request("/api/state")
        self.assertEqual(state["summary"]["selection_run_count"], 1)
        selection = state["selection_runs"][-1]
        self.assertEqual(selection["result_count"], 1)
        candidate = selection["results"][0]
        status, selection_review = self.request("/api/selection-reviews", "POST", {
            "selection_run_id": selection["id"], "candidate_id": candidate["id"],
        })
        self.assertEqual(status, 200)
        self.assertEqual(selection_review["status"], "accepted")
        status, selection_batch = self.request("/api/selection-reviews/batch-pass", "POST", {
            "selection_run_id": selection["id"],
        })
        self.assertEqual(status, 200)
        self.assertEqual(selection_batch["passed_count"], 1)
        status, downstream = self.request("/api/downstream-tasks", "POST", {
            "selection_run_id": selection["id"], "task_type": "习题册",
            "name": "图表分析习题册", "candidate_ids": [candidate["id"]],
            "output_formats": ["Word", "飞书云文档"],
        })
        self.assertEqual(status, 201)
        self.assertEqual(downstream["question_set"]["item_count"], 1)
        self.assertEqual(downstream["task"]["execution_mode"], "contract_only")
        calibration_context = {
            "diagnostic_run_id": diagnosis["id"], "gold_sample_id": sample["id"],
            "coverage_run_id": coverage["id"],
        }
        status, review = self.request("/api/calibrations", "POST", {
            **calibration_context, "asset_id": diagnosis["results"][0]["asset_id"],
        })
        self.assertEqual(status, 200)
        self.assertEqual(review["status"], "accepted")
        self.assertFalse(review["ai_flow_blocked"])
        status, batch = self.request("/api/calibrations/batch-pass", "POST", calibration_context)
        self.assertEqual(status, 200)
        self.assertEqual(batch["passed_count"], 1)
        _, state = self.request("/api/state")
        self.assertEqual(state["summary"]["calibration_review_count"], 1)
        self.assertEqual(state["summary"]["selection_review_count"], 1)
        self.assertEqual(state["summary"]["selection_run_count"], 2)
        self.assertEqual(state["summary"]["question_set_count"], 1)
        self.assertEqual(state["summary"]["downstream_task_count"], 1)


if __name__ == "__main__":
    unittest.main()
