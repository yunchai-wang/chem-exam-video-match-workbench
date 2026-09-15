"""Shared domain constants and state validation."""

from __future__ import annotations

from copy import deepcopy
from typing import Any


CURRENT_SCHEMA_VERSION = 9


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
        version = 3
    if version == 3:
        state.setdefault("diagnostic_runs", [])
        state.setdefault("gold_sample_sets", [])
        state["schema_version"] = 4
        version = 4
    if version == 4:
        state.setdefault("video_imports", [])
        state.setdefault("video_assets", [])
        state.setdefault("coverage_runs", [])
        state["schema_version"] = 5
        version = 5
    if version == 5:
        state.setdefault("calibration_reviews", [])
        state["schema_version"] = 6
        version = 6
    if version == 6:
        state.setdefault("selection_runs", [])
        state.setdefault("selection_reviews", [])
        state["schema_version"] = 7
        version = 7
    if version == 7:
        state.setdefault("question_sets", [])
        state.setdefault("downstream_tasks", [])
        state["schema_version"] = 8
        version = 8
    if version == 8:
        from .tagging import label_library_snapshot, profile_for_asset
        state.setdefault("label_library_snapshots", [label_library_snapshot()])
        assets_by_id = {item.get("id"): item for item in state.get("question_assets", [])}
        for asset in assets_by_id.values():
            asset.setdefault("tag_profile", profile_for_asset(asset))
        diagnosis_by_asset: dict[str, dict[str, Any]] = {}
        for run in state.get("diagnostic_runs", []):
            run.setdefault("label_library_snapshot", label_library_snapshot())
            for result in run.get("results", []):
                asset = assets_by_id.get(result.get("asset_id"), {})
                result.setdefault("tag_profile", profile_for_asset(asset))
                diagnosis_by_asset[result.get("asset_id")] = result
        for run in state.get("selection_runs", []):
            for result in run.get("results", []):
                diagnosis = diagnosis_by_asset.get(result.get("asset_id"), {})
                profile = diagnosis.get("tag_profile") or profile_for_asset(assets_by_id.get(result.get("asset_id"), {}))
                result.setdefault("tag_profile", profile)
                result.setdefault("label_library_snapshot_id", profile.get("library_snapshot_id"))
        for question_set in state.get("question_sets", []):
            for item in question_set.get("items", []):
                item.setdefault("tag_profile", profile_for_asset(assets_by_id.get(item.get("asset_id"), {})))
        state["schema_version"] = 9
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
        "diagnostic_runs", "gold_sample_sets",
        "video_imports", "video_assets", "coverage_runs",
        "calibration_reviews",
        "selection_runs", "selection_reviews",
        "question_sets", "downstream_tasks",
        "label_library_snapshots",
    ):
        if not isinstance(state.get(key), list):
            raise ValidationError(f"{key} must be a list")
