"""Project-scoped tag onboarding and parsing-quality contracts."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from typing import Any

from .domain import ValidationError


ONBOARDING_MODES = {"rich_labels", "partial_labels", "no_labels"}
MODE_LABELS = {
    "rich_labels": "已有完整标签与规则",
    "partial_labels": "只有部分标签或规则",
    "no_labels": "暂无标签与规则",
}
CANONICAL_DIMENSIONS = {
    "question_type": "整题题型",
    "knowledge": "知识点与作用域",
    "condition": "条件",
    "question": "小问任务",
    "solution": "解法",
    "context": "情景",
    "thinking_method": "思想方法",
    "option_concept": "概念选项归一标签",
    "experiment_name": "实验名称",
    "calculation_chapter": "计算题所属章节",
}
PARSE_QUALITY_POLICY = {
    "version": "parse-quality-v1",
    "question_boundary": "识别题号起止后必须核查缺号、重号和跨页断裂",
    "answer_section_boundary": "仅在独立标题行识别到答案与解析类标题后停止采集，题干中的同名短语不触发",
    "image_binding": "原题图表按原位置与题目绑定；缺图、远端未物化和疑似误裁均进入异常复核",
    "deduplication": "同时检查来源题号和内容指纹；重复项保留溯源，不重复计频",
    "unmatched_labels": "知识库外标签进入待映射队列，不留空吞掉新题型",
}


def build_tag_configuration(request: dict[str, Any]) -> dict[str, Any]:
    name = str(request.get("name") or "").strip()
    subject = str(request.get("subject") or "").strip()
    mode = str(request.get("onboarding_mode") or "")
    if not name:
        raise ValidationError("tag configuration name is required")
    if not subject:
        raise ValidationError("tag configuration subject is required")
    if mode not in ONBOARDING_MODES:
        raise ValidationError("invalid tag onboarding mode")

    selected = _unique_strings(request.get("selected_dimensions"))
    unknown_dimensions = set(selected) - set(CANONICAL_DIMENSIONS)
    if unknown_dimensions:
        raise ValidationError(f"unknown tag dimensions: {', '.join(sorted(unknown_dimensions))}")
    if not selected:
        raise ValidationError("at least one tag dimension is required")

    mapping = request.get("source_field_mapping") or {}
    if not isinstance(mapping, dict):
        raise ValidationError("source_field_mapping must be an object")
    mapping = {str(key).strip(): str(value).strip() for key, value in mapping.items() if str(key).strip() and str(value).strip()}
    invalid_targets = set(mapping.values()) - set(CANONICAL_DIMENSIONS)
    if invalid_targets:
        raise ValidationError(f"mapping targets unknown dimensions: {', '.join(sorted(invalid_targets))}")
    if mode == "rich_labels" and not mapping:
        raise ValidationError("rich_labels mode requires at least one explicit source field mapping")

    extensions = _unique_strings(request.get("custom_dimensions"))
    ai_fill = mode != "rich_labels" or bool(request.get("ai_fill_missing"))
    warnings = []
    if mode == "partial_labels" and not mapping:
        warnings.append("尚未映射既有标签字段；AI 可先运行，原字段仍完整保留")
    if mode == "no_labels":
        warnings.append("使用学科通用初版；建议从异常项或少量样本开始校准")

    identity = {
        "name": name,
        "subject": subject,
        "mode": mode,
        "selected": selected,
        "mapping": mapping,
        "extensions": extensions,
    }
    digest = hashlib.sha256(json.dumps(identity, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()[:14]
    status = "已映射·可运行" if mode == "rich_labels" else ("部分继承·可运行" if mode == "partial_labels" else "AI初版·可运行")
    return {
        "id": f"tag-config-{digest}",
        "name": name,
        "subject": subject,
        "onboarding_mode": mode,
        "onboarding_mode_label": MODE_LABELS[mode],
        "status": status,
        "blocks_pipeline": False,
        "selected_dimensions": selected,
        "dimension_labels": {key: CANONICAL_DIMENSIONS[key] for key in selected},
        "source_field_mapping": mapping,
        "project_extensions": extensions,
        "ai_fill_missing": ai_fill,
        "three_layer_model": ["来源原标签", "平台标准标签", "当前项目扩展"],
        "unmatched_label_policy": "进入待映射队列，不静默丢弃",
        "analysis_units": {
            "whole_question": "整题唯一题型",
            "subquestion": "每小问 1～3 个任务标签",
            "option": "概念选择题可拆选项、去除 A/B/C/D 序号后归一统计，并保留原题回链",
            "specialized": "实验题可统计实验名称；计算题可按章聚合",
        },
        "evidence_sources": [{
            "document_token": "B9d7dLZ1hoHnKHxM1xkcNQyhnyg",
            "wiki_token": "UYQcwYdaNibxoTkoVH1ci9e5nfd",
            "revision": 1255,
            "title": "AI工具分析考点考频汇总",
            "absorbed": ["题号边界与截取后核查", "答案解析区停止采集", "概念选项归一统计与原题回溯", "实验名称和计算题章节聚合"],
        }],
        "parse_quality_policy": dict(PARSE_QUALITY_POLICY),
        "warnings": warnings,
        "version": "project-tag-config-v1",
        "created_at": datetime.now().isoformat(timespec="seconds"),
    }


def _unique_strings(value: Any) -> list[str]:
    if value in (None, "", []):
        return []
    values = value if isinstance(value, list) else [value]
    return list(dict.fromkeys(str(item).strip() for item in values if str(item).strip()))
