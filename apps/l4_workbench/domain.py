"""Shared domain constants and state validation."""

from __future__ import annotations

from typing import Any


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


def validate_state(state: dict[str, Any]) -> None:
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
