"""HTTP API and static-file server for the L4 workbench."""

from __future__ import annotations

import json
import mimetypes
import re
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import quote, unquote, urlparse

from .domain import ValidationError
from .service import WorkbenchService


STATIC_DIR = Path(__file__).with_name("static")
CONTENT_TYPES = {".html": "text/html; charset=utf-8", ".css": "text/css; charset=utf-8", ".js": "text/javascript; charset=utf-8"}
MAX_JSON_BODY_BYTES = 2 * 1024 * 1024


class WorkbenchHandler(BaseHTTPRequestHandler):
    service: WorkbenchService

    def log_message(self, format: str, *args: Any) -> None:
        return

    def do_GET(self) -> None:
        path = urlparse(self.path).path
        if path == "/api/state":
            self._json(200, self.service.get_state())
            return
        if path.startswith("/api/assets/"):
            try:
                target = self.service.asset_path(unquote(path.removeprefix("/api/assets/")))
            except ValidationError as error:
                self._json(404, {"error": str(error)})
                return
            self.send_response(200)
            self.send_header("Content-Type", mimetypes.guess_type(target.name)[0] or "application/octet-stream")
            self.send_header("Content-Length", str(target.stat().st_size))
            self.send_header("Cache-Control", "private, max-age=300")
            self.end_headers()
            self.wfile.write(target.read_bytes())
            return
        match = re.fullmatch(r"/api/exports/(selections|question-sets|mother-questions)/([^/]+)\.docx", path)
        if match:
            exporters = {
                "selections": self.service.export_selection_docx,
                "question-sets": self.service.export_question_set_docx,
                "mother-questions": self.service.export_mother_question_docx,
            }
            try:
                payload, filename, report = exporters[match.group(1)](unquote(match.group(2)))
            except ValidationError as error:
                self._json(404, {"error": str(error)})
                return
            self.send_response(200)
            self.send_header("Content-Type", "application/vnd.openxmlformats-officedocument.wordprocessingml.document")
            self.send_header("Content-Length", str(len(payload)))
            self.send_header("Content-Disposition", f"attachment; filename*=UTF-8''{quote(filename)}")
            self.send_header("X-Figure-Count", str(report["figure_count"]))
            self.send_header("X-Missing-Figure-Count", str(report["missing_figure_count"]))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(payload)
            return
        static_path = {"/": "index.html", "/index.html": "index.html", "/styles.css": "styles.css", "/app.js": "app.js"}.get(path)
        if static_path:
            target = STATIC_DIR / static_path
            self.send_response(200)
            self.send_header("Content-Type", CONTENT_TYPES[target.suffix])
            self.send_header("Content-Length", str(target.stat().st_size))
            self.end_headers()
            self.wfile.write(target.read_bytes())
            return
        self._json(404, {"error": "not found"})

    def do_PATCH(self) -> None:
        path = urlparse(self.path).path
        try:
            payload = self._payload()
            if path == "/api/project":
                self._json(200, self.service.update_project(payload))
                return
            match = re.fullmatch(r"/api/questions/([^/]+)", path)
            if match:
                self._json(200, self.service.update_question(match.group(1), payload))
                return
            self._json(404, {"error": "not found"})
        except (ValidationError, ValueError, json.JSONDecodeError) as error:
            self._json(400, {"error": str(error)})

    def do_POST(self) -> None:
        path = urlparse(self.path).path
        try:
            payload = self._payload()
            if path == "/api/runs":
                self._json(201, self.service.start_run(payload))
                return
            if path == "/api/source-snapshots":
                self._json(201, self.service.create_source_snapshot(payload))
                return
            if path == "/api/tag-configurations":
                self._json(201, self.service.create_tag_configuration(payload))
                return
            if path == "/api/label-library/sync":
                self._json(201, self.service.sync_label_library(payload))
                return
            if path == "/api/label-library/mappings":
                self._json(200, self.service.map_unmatched_label(payload))
                return
            if path == "/api/video-evidence-index/sync":
                self._json(201, self.service.sync_video_evidence_index(payload))
                return
            if path == "/api/coverage-candidates/exclude":
                self._json(200, self.service.exclude_coverage_candidate(payload))
                return
            if path == "/api/base/previews":
                self._json(200, self.service.preview_base(payload))
                return
            if path == "/api/base/imports":
                self._json(201, self.service.import_base(payload))
                return
            if path == "/api/manifests/previews":
                self._json(200, self.service.preview_manifest(payload))
                return
            if path == "/api/manifests/imports":
                self._json(201, self.service.import_manifest(payload))
                return
            if path == "/api/diagnostics":
                self._json(201, self.service.diagnose_snapshot(payload))
                return
            if path == "/api/gold-samples":
                self._json(201, self.service.create_gold_sample(payload))
                return
            if path == "/api/video-manifests/imports":
                self._json(201, self.service.import_video_manifest(payload))
                return
            if path == "/api/coverage-diagnostics":
                self._json(201, self.service.diagnose_coverage(payload))
                return
            if path == "/api/calibrations":
                self._json(200, self.service.save_calibration(payload))
                return
            if path == "/api/calibrations/batch-pass":
                self._json(200, self.service.batch_pass_calibrations(payload))
                return
            if path == "/api/selections":
                self._json(201, self.service.create_selection_run(payload))
                return
            if path == "/api/selection-reviews":
                self._json(200, self.service.save_selection_review(payload))
                return
            if path == "/api/selection-reviews/batch-pass":
                self._json(200, self.service.batch_pass_selections(payload))
                return
            if path == "/api/downstream-tasks":
                self._json(201, self.service.create_downstream_task(payload))
                return
            if path == "/api/mother-questions":
                self._json(201, self.service.create_mother_question_run(payload))
                return
            if path == "/api/ai-tag-fills":
                self._json(201, self.service.create_ai_tag_fill_run(payload))
                return
            if path == "/api/mother-question-reviews":
                self._json(200, self.service.save_mother_question_review(payload))
                return
            if path == "/api/mother-question-reviews/batch-confirm":
                self._json(200, self.service.batch_confirm_mother_question_groups(payload))
                return
            if path == "/api/backtests/freezes":
                self._json(201, self.service.freeze_predictions(payload))
                return
            if path == "/api/reviews/batch-pass":
                self._json(200, self.service.approve_review(str(payload.get("review_id", ""))))
                return
            match = re.fullmatch(r"/api/artifacts/([^/]+)/feedback", path)
            if match:
                self._json(201, self.service.add_artifact_feedback(match.group(1), str(payload.get("text", ""))))
                return
            match = re.fullmatch(r"/api/artifacts/([^/]+)/outputs", path)
            if match:
                self._json(201, self.service.register_artifact_outputs(match.group(1), payload))
                return
            match = re.fullmatch(r"/api/artifacts/([^/]+)/confirm", path)
            if match:
                self._json(200, self.service.confirm_artifact(match.group(1), payload))
                return
            match = re.fullmatch(r"/api/source-snapshots/([^/]+)/standardize", path)
            if match:
                self._json(201, self.service.standardize_snapshot(match.group(1)))
                return
            match = re.fullmatch(r"/api/rules/([^/]+)/request-publication", path)
            if match:
                self._json(201, self.service.request_publication(match.group(1)))
                return
            match = re.fullmatch(r"/api/backtests/([^/]+)/evaluate", path)
            if match:
                self._json(201, self.service.evaluate_predictions(match.group(1), payload))
                return
            self._json(404, {"error": "not found"})
        except (ValidationError, ValueError, json.JSONDecodeError) as error:
            self._json(400, {"error": str(error)})

    def _payload(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length", "0"))
        if length < 0 or length > MAX_JSON_BODY_BYTES:
            raise ValidationError("JSON body exceeds the 2 MiB limit")
        if length == 0:
            return {}
        value = json.loads(self.rfile.read(length).decode("utf-8"))
        if not isinstance(value, dict):
            raise ValidationError("JSON body must be an object")
        return value

    def _json(self, status: int, value: Any) -> None:
        body = json.dumps(value, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)


def make_server(service: WorkbenchService, host: str, port: int) -> ThreadingHTTPServer:
    handler = type("ConfiguredWorkbenchHandler", (WorkbenchHandler,), {"service": service})
    return ThreadingHTTPServer((host, port), handler)
