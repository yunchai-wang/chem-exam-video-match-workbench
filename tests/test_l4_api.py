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

    def test_publication_request_stays_manual(self) -> None:
        _, state = self.request("/api/state")
        rule_id = state["rules"][-1]["id"]
        status, publication = self.request(f"/api/rules/{rule_id}/request-publication", "POST", {})
        self.assertEqual(status, 201)
        self.assertEqual(publication["status"], "待人工批准")


if __name__ == "__main__":
    unittest.main()
