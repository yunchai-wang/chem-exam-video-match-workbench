from __future__ import annotations

import json
import unittest
from pathlib import Path

from apps.l4_workbench.domain import migrate_state
from apps.l4_workbench.jobs import StageExecutionError, StageJobRunner


ROOT = Path(__file__).resolve().parents[1]
SEED = ROOT / "sample_data" / "l4_workbench" / "seed.json"


class JobTests(unittest.TestCase):
    def state_and_run(self):
        state = migrate_state(json.loads(SEED.read_text(encoding="utf-8")))
        run = {"id": "run-1", "rule_version": "v1", "source_snapshot_ids": ["s1"], "selected_question_ids": ["q1"]}
        return state, run

    def test_same_idempotency_key_reuses_completed_job(self) -> None:
        calls = []
        runner = StageJobRunner({"diagnosis": lambda state, run: calls.append("called") or {"ok": True}})
        state, run = self.state_and_run()
        first, reused_first = runner.execute(state, run, "diagnosis")
        second, reused_second = runner.execute(state, run, "diagnosis")
        self.assertFalse(reused_first)
        self.assertTrue(reused_second)
        self.assertEqual(first["id"], second["id"])
        self.assertEqual(calls, ["called"])

    def test_failure_is_persisted_and_can_retry(self) -> None:
        attempts = []

        def executor(state, run):
            attempts.append(1)
            if len(attempts) == 1:
                raise RuntimeError("temporary")
            return {"ok": True}

        runner = StageJobRunner({"standardization": executor})
        state, run = self.state_and_run()
        with self.assertRaises(StageExecutionError):
            runner.execute(state, run, "standardization")
        self.assertEqual(state["jobs"][0]["status"], "failed")
        job, reused = runner.execute(state, run, "standardization")
        self.assertFalse(reused)
        self.assertEqual(job["status"], "completed")
        self.assertEqual(job["attempts"], 2)

    def test_default_executor_is_explicitly_dry_run(self) -> None:
        state, run = self.state_and_run()
        job, _ = StageJobRunner().execute(state, run, "lesson_plan")
        self.assertEqual(job["execution_mode"], "dry_run")
        self.assertFalse(job["output"]["production_ready"])


if __name__ == "__main__":
    unittest.main()
