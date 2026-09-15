"""Versioned tag contracts shared by question, video and downstream assets."""

from __future__ import annotations

import re
import json
from copy import deepcopy
from typing import Any


TAG_SPLIT = re.compile(r"[、,，;；|]+")
QUESTION_TYPES = (
    "基础题", "基本实验题", "科学探究题", "推断题",
    "计算题", "科普阅读题", "工艺流程题", "综合应用题",
)
TAG_DIMENSIONS = ("knowledge", "condition", "question", "solution", "context", "thinking_method")

# This is a reproducible reference snapshot, not a copy of every live label row.
# The live Base remains the source of truth; every run records this version so a
# later taxonomy edit cannot silently change an earlier diagnosis.
DEFAULT_LABEL_LIBRARY_SNAPSHOT: dict[str, Any] = {
    "id": "junior-chem-label-library-2026-01-28",
    "subject": "初中化学",
    "status": "reference_snapshot",
    "source": {
        "base_token": "C0bJbSDAdaa5L5smQigc7l70nFd",
        "prompt_document_token": "KkbKdweNioa27kxpcSfcFQ7lnvh",
        "prompt_document_revision": 5454,
        "prompt_document_title": "初中化学AI打标prompt",
        "tables": {
            "knowledge": {"table_id": "tblYz0fgFxgFyRJs", "revision": 1585, "record_count": 209},
            "solution": {"table_id": "tbl2oTGlLZPHSChw", "revision": 1862, "record_count": 456},
            "condition": {"table_id": "tbl5j5xxQG06bUez", "revision": 1146, "record_count": 384},
            "question": {"table_id": "tbl4GzmGHFw9lG5N", "revision": 1432, "record_count": 362},
            "context": {"table_id": "tblEgRARdksTOV3t", "revision": 21, "record_count": 228},
            "thinking_method": {"table_id": "tbl6bSVtkd6YOT2j", "revision": 37, "record_count": 11},
        },
    },
    "prompt_versions": {"question_type": "1.5", "question": "2.6", "knowledge": "2.1"},
    "dimensions": list(TAG_DIMENSIONS),
    "question_types": list(QUESTION_TYPES),
    "policies": {
        "whole_question_type_cardinality": [1, 1],
        "unit_question_tag_cardinality": [1, 3],
        "all_knowledge_tag_cardinality": [1, 13],
        "knowledge_scopes": ["all", "core", "distractor", "mentioned"],
        "mentioned_is_teaching_target": False,
        "distractor_is_teaching_target": False,
        "unknown_labels_may_be_created": False,
    },
}


def label_library_snapshot() -> dict[str, Any]:
    return deepcopy(DEFAULT_LABEL_LIBRARY_SNAPSHOT)


def split_tag_values(value: Any) -> list[str]:
    if value in (None, "", [], {}):
        return []
    if isinstance(value, dict):
        values = list(value.values())
    elif isinstance(value, list):
        values = value
    else:
        values = TAG_SPLIT.split(str(value))
    flattened: list[str] = []
    for item in values:
        if isinstance(item, list):
            flattened.extend(str(value).strip() for value in item if str(value).strip())
        elif str(item).strip():
            flattened.append(str(item).strip())
    return list(dict.fromkeys(flattened))


def canonical_source_fields(asset: dict[str, Any]) -> dict[str, Any]:
    """Prefer mapped canonical fields while retaining raw manifest fields."""
    fields = dict(asset.get("source_fields") or {})
    fields.update({key: value for key, value in (asset.get("normalized_fields") or {}).items() if value not in (None, "", [])})
    return fields


def build_tag_profile(fields: dict[str, Any]) -> dict[str, Any]:
    """Keep pedagogically different tag scopes separate instead of flattening them."""
    all_knowledge = split_tag_values(fields.get("knowledge_tags"))
    core_knowledge = split_tag_values(fields.get("core_knowledge_tags"))
    distractor_knowledge = split_tag_values(fields.get("distractor_knowledge_tags"))
    mentioned_knowledge = split_tag_values(fields.get("mentioned_knowledge_tags"))
    question_tags = _union(fields.get("question_tags"), fields.get("task_tags"))
    solution_tags = _union(fields.get("solution_tags"), fields.get("method_models"))
    context_tags = _union(fields.get("context_tags"), fields.get("background_tags"))
    question_type_candidates = split_tag_values(fields.get("question_type") or fields.get("primary_type"))
    question_type_status = (
        "有效" if len(question_type_candidates) == 1 and question_type_candidates[0] in QUESTION_TYPES
        else ("待识别" if not question_type_candidates else "需复核")
    )
    return {
        "library_snapshot_id": DEFAULT_LABEL_LIBRARY_SNAPSHOT["id"],
        "question_type": question_type_candidates[0] if question_type_status == "有效" else None,
        "question_type_candidates": question_type_candidates,
        "question_type_status": question_type_status,
        "knowledge": {
            "all": all_knowledge,
            "core": core_knowledge,
            "distractor": distractor_knowledge,
            "mentioned": mentioned_knowledge,
            "core_status": "已提供" if core_knowledge else "待识别",
        },
        "condition": split_tag_values(fields.get("condition_tags")),
        "question": question_tags,
        "solution": solution_tags,
        "context": context_tags,
        "thinking_method": split_tag_values(fields.get("thinking_method_tags")),
    }


def profile_for_asset(asset: dict[str, Any]) -> dict[str, Any]:
    existing = asset.get("tag_profile")
    return deepcopy(existing) if isinstance(existing, dict) else build_tag_profile(canonical_source_fields(asset))


def attach_unit_tag_profiles(units: list[dict[str, Any]], fields: dict[str, Any]) -> list[dict[str, Any]]:
    """Attach explicit per-subquestion profiles; never copy whole-question tags blindly."""
    raw = fields.get("unit_tag_profiles")
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except json.JSONDecodeError:
            raw = None
    mapping = raw if isinstance(raw, dict) else {}
    result = []
    for unit in units:
        item = dict(unit)
        supplied = mapping.get(str(unit.get("id"))) or mapping.get(str(unit.get("label")))
        if isinstance(supplied, dict):
            profile = build_tag_profile(supplied)
            issue = not 1 <= len(profile["question"]) <= 3
            item["tag_profile"] = {
                **profile, "status": "需复核" if issue else "已提供",
                "validation_issues": ["每个小问的问题标签必须为 1～3 个"] if issue else [],
            }
        else:
            item["tag_profile"] = {
                "library_snapshot_id": DEFAULT_LABEL_LIBRARY_SNAPSHOT["id"],
                "status": "待逐小问识别",
                "knowledge": {"all": [], "core": [], "distractor": [], "mentioned": [], "core_status": "待识别"},
                "condition": [], "question": [], "solution": [], "context": [], "thinking_method": [],
            }
        result.append(item)
    return result


def _union(*values: Any) -> list[str]:
    result: list[str] = []
    for value in values:
        result.extend(split_tag_values(value))
    return list(dict.fromkeys(result))

