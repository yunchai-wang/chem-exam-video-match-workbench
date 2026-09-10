"""HTTP API and static-file server for the L4 workbench."""

from __future__ import annotations

import json
import re
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from .domain import ValidationError
from .service import WorkbenchService


STATIC_DIR = Path(__file__).with_name("static")
CONTENT_TYPES = {".html": "text/html; charset=utf-8", ".css": "text/css; charset=utf-8", ".js": "text/javascript; charset=utf-8"}


class WorkbenchHandler(BaseHTTPRequestHandler):
    service: WorkbenchService

    def log_message(self, format: str, *args: Any) -> None:
        return

    def do_GET(self) -> None:
        path = urlparse(self.path).path
        if path == "/api/state":
            self._json(200, self.service.get_state())
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
                self._json(201, self.service.start_run())
                return
            if path == "/api/reviews/batch-pass":
                self._json(200, self.service.approve_review(str(payload.get("review_id", ""))))
                return
            match = re.fullmatch(r"/api/artifacts/([^/]+)/feedback", path)
            if match:
                self._json(201, self.service.add_artifact_feedback(match.group(1), str(payload.get("text", ""))))
                return
            match = re.fullmatch(r"/api/rules/([^/]+)/request-publication", path)
            if match:
                self._json(201, self.service.request_publication(match.group(1)))
                return
            self._json(404, {"error": "not found"})
        except (ValidationError, ValueError, json.JSONDecodeError) as error:
            self._json(400, {"error": str(error)})

    def _payload(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length", "0"))
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
