"""Shared domain constants and state validation."""

from __future__ import annotations

from copy import deepcopy
from typing import Any


CURRENT_SCHEMA_VERSION = 3


STAGES = [
    "standardization",
    "diagnosis",
    "selection",
    "mother_question",
    "lesson_plan",
    "transcript",
    "storyboard",
]

STAGE_LABELS = {
    "standardization": "资料标准化",
    "diagnosis": "题型、趋势与覆盖诊断",
    "selection": "好题候选与生产优先级",
    "mother_question": "母题整合",
    "lesson_plan": "教案",
    "transcript": "逐字稿",
    "storyboard": "分镜与成品",
}

INTERVENTION_STRATEGIES = {"auto", "exceptions", "confirm", "blocked"}
STRATEGY_LABELS = {
    "auto": "自动推进",
    "exceptions": "只看异常",
    "confirm": "每次确认",
    "blocked": "禁止自动执行",
}

PRIORITIES = {"P1", "P2", "P3", "暂不生产"}
CONTENT_HEALTH_STATES = {"无明显问题", "疑似问题", "已确认需纠错", "已修复待验证"}
COVERAGE_STATES = {
    "充分覆盖",
    "部分覆盖",
    "组合支撑但缺综合迁移",
    "未覆盖",
    "无法判断",
}


class ValidationError(ValueError):
    """Raised when persisted or incoming state violates a domain boundary."""


def migrate_state(value: dict[str, Any]) -> dict[str, Any]:
    """Return a current-schema copy without mutating the persisted source."""
    state = deepcopy(value)
    version = int(state.get("schema_version", 1))
    if version > CURRENT_SCHEMA_VERSION:
        raise ValidationError(f"state schema {version} is newer than supported {CURRENT_SCHEMA_VERSION}")
    if version == 1:
        state.setdefault("metadata", {"state_revision": 0, "updated_at": None})
        state.setdefault("source_snapshots", [])
        state.setdefault("jobs", [])
        state.setdefault("prediction_freezes", [])
        state.setdefault("backtest_results", [])
        state["schema_version"] = 2
        version = 2
    if version == 2:
        state.setdefault("standardization_runs", [])
        state.setdefault("documents", [])
        state.setdefault("question_assets", [])
        state.setdefault("field_mappings", [])
        state["schema_version"] = 3
    return state


def validate_state(state: dict[str, Any]) -> None:
    if state.get("schema_version") != CURRENT_SCHEMA_VERSION:
        raise ValidationError(f"state must use schema {CURRENT_SCHEMA_VERSION}")
    metadata = state.get("metadata")
    if not isinstance(metadata, dict) or not isinstance(metadata.get("state_revision"), int):
        raise ValidationError("metadata.state_revision must be an integer")
    project = state.get("project")
    if not isinstance(project, dict):
        raise ValidationError("project must be an object")

    strategies = project.get("intervention_strategies", {})
    for stage in STAGES:
        strategy = strategies.get(stage)
        if strategy not in INTERVENTION_STRATEGIES:
            raise ValidationError(f"invalid intervention strategy for {stage}: {strategy}")

    for question in state.get("questions", []):
        priority = question.get("production_priority")
        if priority not in PRIORITIES:
            raise ValidationError(f"invalid production priority: {priority}")
        health = question.get("content_health", {}).get("status")
        if health not in CONTENT_HEALTH_STATES:
            raise ValidationError(f"invalid content health: {health}")
        coverage = question.get("coverage", {}).get("status")
        if coverage not in COVERAGE_STATES:
            raise ValidationError(f"invalid coverage status: {coverage}")

    for publication in state.get("publications", []):
        if publication.get("status") not in {"待人工批准", "已批准", "已拒绝"}:
            raise ValidationError("public rule publication must have a governed status")

    for key in (
        "source_snapshots", "jobs", "prediction_freezes", "backtest_results",
        "standardization_runs", "documents", "question_assets", "field_mappings",
    ):
        if not isinstance(state.get(key), list):
            raise ValidationError(f"{key} must be a list")
