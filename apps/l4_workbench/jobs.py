"""Idempotent, resumable stage-job records for the local workbench."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from typing import Any, Callable
from uuid import uuid4


def now() -> str:
    return datetime.now().isoformat(timespec="seconds")


class StageExecutionError(RuntimeError):
    def __init__(self, job: dict[str, Any]) -> None:
        super().__init__(job.get("error") or "stage execution failed")
        self.job = job


class StageJobRunner:
    def __init__(self, executors: dict[str, Callable[[dict[str, Any], dict[str, Any]], dict[str, Any]]] | None = None) -> None:
        self.executors = executors or {}

    def execute(self, state: dict[str, Any], run: dict[str, Any], stage: str) -> tuple[dict[str, Any], bool]:
        key = self._idempotency_key(run, stage)
        for existing in state["jobs"]:
            if existing["idempotency_key"] == key and existing["status"] == "completed":
                return existing, True

        job = next((item for item in state["jobs"] if item["idempotency_key"] == key), None)
        if job is None:
            job = {
                "id": f"job-{uuid4().hex[:12]}",
                "run_id": run["id"],
                "stage": stage,
                "status": "pending",
                "execution_mode": "production" if stage in self.executors else "dry_run",
                "idempotency_key": key,
                "source_snapshot_ids": run.get("source_snapshot_ids", []),
                "rule_version": run["rule_version"],
                "attempts": 0,
                "created_at": now(),
                "started_at": None,
                "completed_at": None,
                "output": None,
                "error": None,
            }
            state["jobs"].append(job)

        job["attempts"] += 1
        job["status"] = "running"
        job["started_at"] = now()
        job["error"] = None
        try:
            executor = self.executors.get(stage, self._dry_run_executor)
            job["output"] = executor(state, run)
            job["status"] = "completed"
            job["completed_at"] = now()
            return job, False
        except Exception as error:
            job["status"] = "failed"
            job["error"] = f"{type(error).__name__}: {error}"
            job["completed_at"] = now()
            raise StageExecutionError(job) from error

    @staticmethod
    def _idempotency_key(run: dict[str, Any], stage: str) -> str:
        payload = {
            "run_id": run["id"],
            "stage": stage,
            "rule_version": run["rule_version"],
            "source_snapshot_ids": run.get("source_snapshot_ids", []),
            "selected_question_ids": sorted(run.get("selected_question_ids", [])),
            "feedback_id": run.get("feedback_id"),
        }
        return hashlib.sha256(json.dumps(payload, sort_keys=True).encode("utf-8")).hexdigest()

    @staticmethod
    def _dry_run_executor(state: dict[str, Any], run: dict[str, Any]) -> dict[str, Any]:
        return {
            "production_ready": False,
            "result_type": "contract_preview",
            "message": "执行契约已验证；真实 AI/Skill 执行器尚未接入，不能标记为正式生产结果",
            "question_count": len(run.get("selected_question_ids", [])),
            "state_revision": state["metadata"]["state_revision"],
        }
