"""Versioned tag contracts shared by question, video and downstream assets."""

from __future__ import annotations

import json
import re
from copy import deepcopy
from typing import Any


TAG_SPLIT = re.compile(r"[、,，;；|]+")
QUESTION_TYPES = (
    "基础题", "基本实验题", "科学探究题", "推断题",
    "计算题", "科普阅读题", "工艺流程题", "综合应用题",
)
TAG_DIMENSIONS = ("knowledge", "condition", "question", "solution", "context", "thinking_method")
TAGGING_CONTRACT_VERSION = "2026-09-15.v2"

# This is a reproducible reference snapshot, not a copy of every live label row.
# The live Base remains the source of truth; every run records this version so a
# later taxonomy edit cannot silently change an earlier diagnosis.
DEFAULT_LABEL_LIBRARY_SNAPSHOT: dict[str, Any] = {
    "id": "junior-chem-label-contract-2026-09-15-v2",
    "taxonomy_snapshot_id": "junior-chem-label-library-2026-01-28",
    "parent_snapshot_id": "junior-chem-label-library-2026-01-28",
    "subject": "初中化学",
    "status": "reference_snapshot",
    "source": {
        "base_token": "C0bJbSDAdaa5L5smQigc7l70nFd",
        "prompt_document_token": "KkbKdweNioa27kxpcSfcFQ7lnvh",
        "prompt_document_revision": 5454,
        "prompt_document_title": "初中化学AI打标prompt",
        "evidence_documents": [
            {
                "document_token": "RIFWdvKvgo6H3QxaXR0cnm9bnlh",
                "revision": 455,
                "title": "【初化相似题推荐】第2批整题推整题·洞察报告",
                "use": "相似题推荐粒度的分题型实测参考",
            },
            {
                "document_token": "AbNTdb7mbo5qxZxqM1lcmr6AnMd",
                "revision": 13,
                "title": "初中化学相似题AI推题测评数据分析报告 by codex",
                "use": "整题相似与局部小问相似的边界参考",
            },
        ],
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
        "classification_model": "whole_question_single_type_plus_unit_task_multilabel",
        "unit_uses_eight_question_types": False,
        "knowledge_scopes": ["all", "core", "prerequisite", "distractor", "mentioned"],
        "mentioned_is_teaching_target": False,
        "distractor_is_teaching_target": False,
        "unknown_labels_may_be_created": False,
        "similarity_routing_evidence": {
            "scope": "similar_question_recommendation_only",
            "is_hard_gate": False,
            "whole_preferred": ["基本实验题", "科普阅读题", "科学探究题"],
            "unit_preferred": ["基础题", "工艺流程题", "计算题"],
            "adaptive": ["推断题", "综合应用题"],
        },
        "asset_knowledge_contracts": {
            "exercise": {
                "all": "题干、选项、图表、答案与解析中实质涉及的知识并集",
                "core": "完成目标设问并获得分数不可缺少的知识",
                "prerequisite": "解题中调用但不是本题主要考查目标的前置或工具知识",
                "distractor": "错误选项或错误路径涉及的知识",
                "mentioned": "背景中出现但不参与作答的知识",
            },
            "problem_lesson_video": {
                "all": "解题链中被实质调用、解释或训练的知识并集，不含画面偶现词",
                "core": "课程目标和关键解法链实际教授的知识与方法",
                "prerequisite": "默认学生已会或只作快速回顾的知识",
                "distractor": "课程明确分析的错误路径所涉及知识",
                "mentioned": "只在题面、开场或旁支中出现的知识",
            },
            "concept_lesson_video": {
                "all": "被实际讲解的概念知识与必要前置知识并集，不追求题面式全量收集",
                "core": "由学习目标确定且在课程中形成理解、辨析或迁移的知识",
                "prerequisite": "用于搭桥但不承担本课认知终点的旧知识",
                "distractor": "课程专门纠正的错误概念或误区所涉及知识",
                "mentioned": "只用于情境引入、举例或过渡的知识",
            },
        },
    },
    "contract_version": TAGGING_CONTRACT_VERSION,
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
    core_knowledge = split_tag_values(fields.get("core_knowledge_tags"))
    prerequisite_knowledge = _union(
        fields.get("prerequisite_knowledge_tags"),
        fields.get("supporting_knowledge_tags"),
        fields.get("tool_knowledge_tags"),
    )
    distractor_knowledge = split_tag_values(fields.get("distractor_knowledge_tags"))
    mentioned_knowledge = split_tag_values(fields.get("mentioned_knowledge_tags"))
    all_knowledge = _union(
        fields.get("knowledge_tags"), core_knowledge, prerequisite_knowledge,
        distractor_knowledge, mentioned_knowledge,
    )
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
        "contract_version": TAGGING_CONTRACT_VERSION,
        "classification_model": "整题唯一题型＋逐小问任务多标签",
        "question_type": question_type_candidates[0] if question_type_status == "有效" else None,
        "question_type_candidates": question_type_candidates,
        "question_type_status": question_type_status,
        "knowledge": {
            "all": all_knowledge,
            "core": core_knowledge,
            "prerequisite": prerequisite_knowledge,
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
    return normalize_tag_profile(existing) if isinstance(existing, dict) else build_tag_profile(canonical_source_fields(asset))


def normalize_tag_profile(profile: dict[str, Any]) -> dict[str, Any]:
    """Add the current role model without promoting historical tags to core."""
    result = deepcopy(profile)
    knowledge = result.setdefault("knowledge", {})
    core = split_tag_values(knowledge.get("core"))
    prerequisite = split_tag_values(knowledge.get("prerequisite"))
    distractor = split_tag_values(knowledge.get("distractor"))
    mentioned = split_tag_values(knowledge.get("mentioned"))
    knowledge["all"] = _union(knowledge.get("all"), core, prerequisite, distractor, mentioned)
    knowledge["core"] = core
    knowledge["prerequisite"] = prerequisite
    knowledge["distractor"] = distractor
    knowledge["mentioned"] = mentioned
    knowledge.setdefault("core_status", "已提供" if core else "待识别")
    result.setdefault("contract_version", TAGGING_CONTRACT_VERSION)
    result.setdefault("classification_model", "整题唯一题型＋逐小问任务多标签")
    return result


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
                "contract_version": TAGGING_CONTRACT_VERSION,
                "classification_model": "整题唯一题型＋逐小问任务多标签",
                "knowledge": {"all": [], "core": [], "prerequisite": [], "distractor": [], "mentioned": [], "core_status": "待识别"},
                "condition": [], "question": [], "solution": [], "context": [], "thinking_method": [],
            }
        result.append(item)
    return result


def _union(*values: Any) -> list[str]:
    result: list[str] = []
    for value in values:
        result.extend(split_tag_values(value))
    return list(dict.fromkeys(result))
