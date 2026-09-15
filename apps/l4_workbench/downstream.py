"""Freeze reviewed question assets into explicit downstream task contracts."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from typing import Any

from .domain import ValidationError


TASK_TYPES = {"视频生产", "习题册", "作业", "学案", "专题资料", "备考题池", "自定义"}


def now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def create_downstream_task(
    selection_run: dict[str, Any],
    selection_reviews: list[dict[str, Any]],
    assets: list[dict[str, Any]],
    project: dict[str, Any],
    request: dict[str, Any],
) -> dict[str, dict[str, Any]]:
    task_type = str(request.get("task_type") or "")
    if task_type not in TASK_TYPES:
        raise ValidationError(f"invalid downstream task type: {task_type}")
    output_goal = str(request.get("output_goal") or "").strip()
    if task_type == "自定义" and not output_goal:
        raise ValidationError("custom downstream task requires output_goal")

    candidates = {item["id"]: item for item in selection_run["results"]}
    candidate_ids = request.get("candidate_ids")
    if not isinstance(candidate_ids, list) or not candidate_ids:
        raise ValidationError("at least one candidate is required")
    candidate_ids = list(dict.fromkeys(str(item) for item in candidate_ids))
    unknown = set(candidate_ids) - set(candidates)
    if unknown:
        raise ValidationError("downstream task contains candidates outside the selection run")

    reviews = {
        item["candidate_id"]: item for item in selection_reviews
        if item.get("selection_run_id") == selection_run["id"]
    }
    asset_index = {item["id"]: item for item in assets}
    items = []
    for candidate_id in candidate_ids:
        candidate = candidates[candidate_id]
        review = reviews.get(candidate_id, {})
        selected_units = review["selected_unit_ids"] if "selected_unit_ids" in review else [item["id"] for item in candidate["units"]]
        if not selected_units:
            raise ValidationError("downstream task contains a candidate without selected question units")
        asset = asset_index.get(candidate["asset_id"], {})
        unit_profiles = {
            item["id"]: item.get("tag_profile", {})
            for item in candidate.get("units", []) if item["id"] in selected_units
        }
        items.append({
            "candidate_id": candidate_id,
            "asset_id": candidate["asset_id"],
            "selected_unit_ids": selected_units,
            "selected_unit_tag_profiles": unit_profiles,
            "role_labels": review.get("role_labels", candidate.get("ai_role_labels", [])),
            "usage_scenarios": review.get("usage_scenarios", candidate.get("ai_usage_scenarios", [])),
            "source_name": candidate.get("source_name"),
            "question_no": candidate.get("question_no"),
            "content_blocks": asset.get("content_blocks", []),
            "tag_profile": asset.get("tag_profile", {}),
            "image_integrity": asset.get("image_integrity", "unknown"),
        })

    frozen_at = now()
    set_payload = {
        "selection_run_id": selection_run["id"], "candidate_ids": candidate_ids,
        "items": items, "source_snapshot_id": selection_run.get("source_snapshot_id"),
    }
    checksum = hashlib.sha256(json.dumps(set_payload, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()
    question_set = {
        "id": f"question-set-{checksum[:14]}", "name": str(request.get("name") or f"{task_type}题集"),
        "version": "v1", **set_payload, "item_count": len(items),
        "immutable_checksum": checksum, "frozen_at": frozen_at,
    }

    needs_video = task_type == "视频生产"
    task_payload = {
        "question_set_id": question_set["id"], "task_type": task_type,
        "target_students": str(request.get("target_students") or project.get("target_students") or ""),
        "target_region": str(request.get("target_region") or project.get("target_region") or ""),
        "exam_type": str(request.get("exam_type") or project.get("target_exam_type") or ""),
        "content_scope": str(request.get("content_scope") or project.get("content_scope") or ""),
        "output_goal": output_goal or f"生成可编辑的{task_type}生产任务",
        "output_formats": _list_value(request.get("output_formats") or ["Word"]),
        "review_strategy": str(request.get("review_strategy") or "按当前项目设置"),
    }
    task_checksum = hashlib.sha256(json.dumps(task_payload, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()
    task = {
        "id": f"downstream-task-{task_checksum[:14]}", "name": question_set["name"], **task_payload,
        "status": "任务契约已冻结，生产执行器待接入", "execution_mode": "contract_only",
        "dependency_plan": {
            "required": ["标准题目资产", "题目角色与场景", "难度梯度", "答案与原图完整性"],
            "conditional": ["母题整合"] if task_type in {"视频生产", "习题册", "学案", "专题资料"} else [],
            "skipped": [] if needs_video else ["视频覆盖诊断", "课程生产优先级"],
        },
        "created_at": frozen_at,
    }
    return {"question_set": question_set, "task": task}


def _list_value(value: Any) -> list[str]:
    if not isinstance(value, list):
        raise ValidationError("output_formats must be a list")
    return list(dict.fromkeys(str(item) for item in value if str(item).strip()))
