"""Build a traceable production candidate pool from real diagnostic evidence."""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime
from typing import Any

from .domain import ValidationError


RULE_VERSION = "production-selection-v0.3"
DECISIONS = {"按 AI 建议推进", "进入课程生产", "仅保留好题池", "进入母题改造", "暂不使用"}
ROLE_LABELS = {"母题候选", "核心例题", "同构练习", "变式练习", "迁移练习", "检测题", "基础巩固题"}
USAGE_SCENARIOS = {"视频生产", "习题册", "作业", "学案", "专题资料", "备考题池"}
SUBQUESTION = re.compile(r"[（(]\s*(\d{1,2})\s*[）)]")
OPTION = re.compile(r"(?<![A-Za-zＡ-Ｚａ-ｚ])([A-DＡ-Ｄ])[．.、]")


def now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def build_selection_run(
    diagnostic_run: dict[str, Any],
    gold_sample: dict[str, Any],
    coverage_run: dict[str, Any],
    assets: list[dict[str, Any]],
    calibration_reviews: list[dict[str, Any]],
) -> dict[str, Any]:
    """Create one explainable candidate row per sampled real question."""
    if gold_sample["diagnostic_run_id"] != diagnostic_run["id"]:
        raise ValidationError("gold sample and diagnostic run do not match")
    if coverage_run["diagnostic_run_id"] != diagnostic_run["id"] or coverage_run["gold_sample_id"] != gold_sample["id"]:
        raise ValidationError("coverage run does not match the diagnostic context")

    asset_index = {item["id"]: item for item in assets}
    diagnosis_index = {item["asset_id"]: item for item in diagnostic_run["results"]}
    coverage_index = {item["asset_id"]: item for item in coverage_run["results"]}
    review_index = {
        item["asset_id"]: item for item in calibration_reviews
        if item.get("diagnostic_run_id") == diagnostic_run["id"]
        and item.get("gold_sample_id") == gold_sample["id"]
        and item.get("coverage_run_id") == coverage_run["id"]
    }

    results = []
    for sample_item in gold_sample["items"]:
        asset_id = sample_item["asset_id"]
        try:
            asset = asset_index[asset_id]
            diagnosis = diagnosis_index[asset_id]
            coverage = coverage_index[asset_id]
        except KeyError as error:
            raise ValidationError(f"selection evidence missing for asset: {asset_id}") from error
        results.append(_candidate(asset, diagnosis, coverage, review_index.get(asset_id)))

    review_fingerprint = [
        {"asset_id": item["asset_id"], "updated_at": item.get("updated_at"), "effective": item.get("effective_values")}
        for item in sorted(review_index.values(), key=lambda item: item["asset_id"])
    ]
    checksum = hashlib.sha256(json.dumps({
        "diagnostic": diagnostic_run["id"], "sample": gold_sample["id"], "coverage": coverage_run["id"],
        "rule": RULE_VERSION, "reviews": review_fingerprint,
    }, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()
    summary = {
        "evaluated_count": len(results),
        "good_question_count": sum(item["quality"]["is_good_candidate"] for item in results),
        "high_frequency_count": sum(item["frequency"]["level"] == "高频" for item in results),
        "high_frequency_and_good_count": sum(
            item["frequency"]["level"] == "高频" and item["quality"]["is_good_candidate"] for item in results
        ),
        "p1_count": sum(item["production_priority"]["recommendation"] == "P1" for item in results),
        "p2_count": sum(item["production_priority"]["recommendation"] == "P2" for item in results),
        "p3_count": sum(item["production_priority"]["recommendation"] == "P3" for item in results),
        "not_produce_count": sum(item["production_priority"]["recommendation"] == "暂不生产" for item in results),
        "teacher_calibrated_count": sum(item["evidence_sources"]["calibration"] == "教师校准" for item in results),
        "student_evidence_count": 0,
    }
    return {
        "id": f"selection-{checksum[:14]}", "diagnostic_run_id": diagnostic_run["id"],
        "gold_sample_id": gold_sample["id"], "coverage_run_id": coverage_run["id"],
        "source_snapshot_id": diagnostic_run["source_snapshot_id"], "rule_version": RULE_VERSION,
        "status": "completed_with_predicted_student_value", "result_count": len(results),
        "evidence_limits": [
            "当前未接入学生观看、练习或错因数据；学生需求与学习增益只标为“教研预测”，不冒充实证。",
            "视频“部分覆盖候选”仍需核对小问、作答边界和原视频证据；视频缺口本身不会自动抬高生产优先级。",
            "题目单元由现有结构或可识别的小问/选项生成；无法可靠拆分时保留整题，绝不虚构小问。",
        ],
        "summary": summary, "results": results, "created_at": now(),
    }


def build_selection_review(selection_run: dict[str, Any], request: dict[str, Any], *, mode: str = "single") -> dict[str, Any]:
    candidate_id = str(request.get("candidate_id") or "")
    candidate = next((item for item in selection_run["results"] if item["id"] == candidate_id), None)
    if candidate is None:
        raise ValidationError("candidate is not part of this selection run")
    decision = str(request.get("decision") or "按 AI 建议推进")
    if decision not in DECISIONS:
        raise ValidationError(f"invalid selection decision: {decision}")
    allowed_units = {item["id"] for item in candidate["units"]}
    selected_units = request.get("selected_unit_ids")
    if selected_units is None:
        selected_units = [item["id"] for item in candidate["units"]]
    if not isinstance(selected_units, list) or not set(selected_units) <= allowed_units:
        raise ValidationError("selected units must belong to the candidate")
    reason = str(request.get("reason") or "").strip()
    effective_decision = candidate["ai_next_route"] if decision == "按 AI 建议推进" else decision
    role_labels = _validated_multi_value(
        request.get("role_labels", candidate["ai_role_labels"]), ROLE_LABELS, "role labels",
    )
    usage_scenarios = _validated_multi_value(
        request.get("usage_scenarios", candidate["ai_usage_scenarios"]), USAGE_SCENARIOS, "usage scenarios",
    )
    if effective_decision == "进入课程生产" and not selected_units:
        raise ValidationError("course production requires at least one selected question unit")
    production_corrected = effective_decision != candidate["ai_next_route"] or set(selected_units) != allowed_units
    reuse_adjusted = set(role_labels) != set(candidate["ai_role_labels"]) or set(usage_scenarios) != set(candidate["ai_usage_scenarios"])
    corrected = production_corrected or reuse_adjusted
    if production_corrected and not reason:
        raise ValidationError("changing the AI route or production units requires a reason")
    if not reason:
        reason = (
            "教师调整了题目角色或可用场景；该信号只作为任务偏好，不自动改写筛选规则。"
            if reuse_adjusted else
            ("教师接受 AI 当前生产去向及全部可识别题目单元。" if mode == "single" else "批量通过：接受非异常题目的 AI 当前生产去向。")
        )
    identity = json.dumps({"selection": selection_run["id"], "candidate": candidate_id}, ensure_ascii=False, sort_keys=True)
    return {
        "id": f"selection-review-{hashlib.sha256(identity.encode('utf-8')).hexdigest()[:14]}",
        "selection_run_id": selection_run["id"], "candidate_id": candidate_id,
        "asset_id": candidate["asset_id"], "status": "corrected" if corrected else "accepted",
        "review_mode": mode, "ai_route": candidate["ai_next_route"], "decision": effective_decision,
        "selected_unit_ids": selected_units, "role_labels": role_labels, "usage_scenarios": usage_scenarios,
        "reuse_adjusted": reuse_adjusted, "reason": reason,
        "rule_proposals": [{
            "family": "selection_priority_rule", "status": "隔离实验候选", "evidence": reason,
        }] if production_corrected else [],
        "preference_signals": [{
            "family": "question_reuse_preference", "scope": "当前生产项目", "evidence": reason,
        }] if reuse_adjusted else [],
        "ai_flow_blocked": False, "updated_at": now(),
    }


def _candidate(asset: dict[str, Any], diagnosis: dict[str, Any], coverage: dict[str, Any], review: dict[str, Any] | None) -> dict[str, Any]:
    effective = review.get("effective_values", {}) if review else {}
    corrected_fields = set(review.get("corrected_fields", [])) if review else set()
    calibration_reason = str(review.get("reason") or "") if review else ""
    frequency_level = effective.get("frequency", diagnosis["frequency"]["level"])
    quality_label = effective.get("quality", diagnosis["quality"]["recommendation"])
    science = effective.get("science", diagnosis["quality"]["dimensions"]["科学性"]["status"])
    coverage_status = effective.get("coverage", coverage["status"])
    structural_keys = effective.get("structural_keys", diagnosis["structural_keys"])
    difficulty = _difficulty_label(diagnosis.get("difficulty"))
    is_good = quality_label in {"AI候选好题", "好题"}
    migration = "高" if diagnosis["quality"]["dimensions"]["迁移价值"]["status"] == "支持" else "低"
    cross_region = "高" if frequency_level == "高频" else ("中" if frequency_level == "中频" else "低")
    learner_level, learner_reason = _predicted_learner_value(difficulty, quality_label, migration, diagnosis.get("task_tags", []))
    substitutability = _substitutability(coverage_status, coverage.get("candidates", []))
    cost = _production_cost(asset, difficulty)
    priority, priority_reason, intervention, route = _priority(
        asset=asset, quality_label=quality_label, is_good=is_good, science=science,
        frequency_level=frequency_level, coverage_status=coverage_status, difficulty=difficulty,
        migration=migration, learner_level=learner_level, structural_keys=structural_keys,
    )
    role_labels, usage_scenarios = _reuse_suggestions(
        is_good=is_good, frequency_level=frequency_level, difficulty=difficulty,
        migration=migration, structural_keys=structural_keys, route=route,
    )
    return {
        "id": f"candidate-{asset['id']}", "asset_id": asset["id"],
        "source_name": asset.get("source_name"), "question_no": asset.get("question_no"),
        "title": asset.get("title") or "题目资产", "units": _question_units(asset),
        "tag_profile": diagnosis.get("tag_profile") or asset.get("tag_profile", {}),
        "label_library_snapshot_id": diagnosis.get("tag_profile", {}).get("library_snapshot_id"),
        "frequency": {
            **diagnosis["frequency"], "level": frequency_level, "independent_dimension": True,
            "reason": _effective_reason("frequency", corrected_fields, calibration_reason, diagnosis["frequency"]["reason"]),
        },
        "quality": {
            "recommendation": quality_label, "is_good_candidate": is_good, "independent_dimension": True,
            "score": diagnosis["quality"]["score"], "score_denominator": diagnosis["quality"]["score_denominator"],
            "dimensions": diagnosis["quality"]["dimensions"],
            "reason": _effective_reason("quality", corrected_fields, calibration_reason, diagnosis["quality"]["reason"]),
        },
        "content_health": {
            "status": "有问题" if science == "有问题" or asset.get("issue_codes") else science,
            "issue_codes": asset.get("issue_codes", []),
        },
        "coverage": {
            "status": coverage_status,
            "reason": _effective_reason("coverage", corrected_fields, calibration_reason, coverage["reason"]),
            "candidates": coverage.get("candidates", []),
            "best_evidence_level": coverage.get("candidates", [{}])[0].get("evidence_level", "E0") if coverage.get("candidates") else "E0",
            "independent_dimension": True,
        },
        "difficulty": {"level": difficulty, "source": "题目已有难度字段"},
        "learner_value": {
            "level": learner_level, "evidence_status": "教研预测", "reason": learner_reason,
            "student_data_available": False,
        },
        "migration_value": migration, "cross_region_reuse": cross_region,
        "existing_asset_substitutability": substitutability,
        "production_cost": {"level": cost, "evidence_status": "工程启发式"},
        "production_priority": {
            "recommendation": priority, "status": "教研预测", "reason": priority_reason,
            "minimum_intervention": intervention, "coverage_gap_alone_can_raise_priority": False,
        },
        "ai_next_route": route,
        "ai_role_labels": role_labels,
        "ai_usage_scenarios": usage_scenarios,
        "evidence_sources": {
            "diagnostic_rule": diagnosis.get("quality", {}).get("reason"),
            "coverage_rule": coverage.get("reason"),
            "calibration": "教师校准" if review else "AI 原始判断",
            "calibration_reason": calibration_reason or None,
        },
        "exception": bool(asset.get("issue_codes")) or quality_label == "异常复核" or science == "有问题",
    }


def _priority(*, asset: dict[str, Any], quality_label: str, is_good: bool, science: str,
              frequency_level: str, coverage_status: str, difficulty: str, migration: str,
              learner_level: str, structural_keys: list[str]) -> tuple[str, str, str, str]:
    if asset.get("issue_codes") or quality_label == "异常复核" or science == "有问题":
        return "暂不生产", "内容完整性或科学性存在异常，先纠错再排序。", "纠错后再评估", "暂不使用"
    if quality_label in {"暂不推荐", "非好题"}:
        if structural_keys and migration == "高":
            return "暂不生产", "原题质量不足，但底层结构可用于重新设计母题；不能直接进入课程生产。", "母题改造后再评估", "进入母题改造"
        return "暂不生产", "当前题目质量不足；不能因高频或视频缺口而进入生产。", "保留来源证据", "暂不使用"
    if difficulty == "基础":
        return "P3", "题目偏基础；缺少学生实证时默认只保留题源，视频缺口不会让它自动升档。", "仅题库保留", "仅保留好题池" if is_good else "暂不使用"
    if coverage_status == "充分覆盖":
        return "P3", "题目可用，但既有视频已被教师确认充分覆盖，当前无需新生产。", "仅题库保留", "仅保留好题池"
    strong_demand = frequency_level == "高频" and migration == "高" and learner_level == "高"
    verified_gap = coverage_status in {"部分覆盖", "组合支撑但缺综合迁移", "未覆盖"}
    candidate_gap = coverage_status in {"部分覆盖候选", "证据不足", "未发现可核验证据", "无法判断"}
    if is_good and strong_demand and (verified_gap or candidate_gap):
        caveat = "覆盖仍为候选结论；" if candidate_gap else ""
        return "P1", f"{caveat}高频、迁移价值与预测学习增益同时较高，建议优先核验并生产。", "完整新课或综合真题串讲", "进入课程生产"
    if is_good and (frequency_level in {"高频", "中频"} or migration == "高") and (verified_gap or candidate_gap):
        return "P2", "题目质量与考试/迁移价值至少两项成立；先用最小充分方式补强，学生价值仍待实证。", "短讲/补丁或旧课改造", "进入课程生产"
    if is_good:
        return "P3", "题目质量成立，但当前项目缺少足够的优先生产证据。", "仅题库保留", "仅保留好题池"
    return "P3", "作为备选题源保留；需要教研补证据后再决定是否生产。", "仅题库保留", "仅保留好题池"


def _predicted_learner_value(difficulty: str, quality: str, migration: str, task_tags: list[str]) -> tuple[str, str]:
    if difficulty == "基础":
        return "低", "面向中等及以上学生，基础题默认预测增益较低；尚无观看、练习或错因数据。"
    if quality in {"AI候选好题", "好题"} and migration == "高" and len(task_tags) >= 2:
        return "高", "多任务且具有迁移价值，教研上预测可能形成学习增益；尚待学生数据验证。"
    return "中", "题目具备一定认知或迁移要求，但当前只有教研侧预测，尚无学生行为实证。"


def _difficulty_label(value: Any) -> str:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return "未知"
    if number <= 2:
        return "基础"
    if number <= 3.5:
        return "中等"
    return "较难"


def _substitutability(status: str, candidates: list[dict[str, Any]]) -> str:
    if status == "充分覆盖":
        return "高：优先复用既有视频"
    if status in {"部分覆盖", "组合支撑但缺综合迁移", "部分覆盖候选"} and candidates:
        return "中：可评估旧课改造或组合支撑"
    return "低或待核：暂无可替代的充分证据"


def _production_cost(asset: dict[str, Any], difficulty: str) -> str:
    has_visual = any(block.get("type") in {"image", "table"} for block in asset.get("content_blocks", []))
    if has_visual and difficulty == "较难":
        return "高"
    if has_visual or difficulty in {"中等", "较难"}:
        return "中"
    return "低"


def _question_units(asset: dict[str, Any]) -> list[dict[str, Any]]:
    existing = asset.get("units") or []
    if existing:
        values = []
        for item in existing:
            label = str(item.get("label") or item["id"])
            kind = item.get("kind", "subquestion")
            if kind == "subquestion" and label.isdigit():
                label = f"小问（{label}）"
            values.append({
                "id": str(item["id"]), "label": label, "kind": kind,
                "tag_profile": item.get("tag_profile", {}),
            })
        return values
    text = str(asset.get("raw_text") or "")
    subquestions = list(dict.fromkeys(match.group(1) for match in SUBQUESTION.finditer(text)))
    if subquestions:
        return [{"id": f"{asset['id']}-sub-{label}", "label": f"小问（{label}）", "kind": "subquestion"} for label in subquestions]
    options = list(dict.fromkeys(match.group(1).translate(str.maketrans("ＡＢＣＤ", "ABCD")) for match in OPTION.finditer(text)))
    if len(options) >= 2:
        return [{"id": f"{asset['id']}-option-{label}", "label": f"选项 {label}", "kind": "option"} for label in options]
    return [{"id": f"{asset['id']}-whole", "label": "整题", "kind": "whole_question"}]


def _effective_reason(field: str, corrected_fields: set[str], calibration_reason: str, ai_reason: str) -> str:
    if field not in corrected_fields:
        return ai_reason
    return f"教师校准：{calibration_reason}｜原 AI 证据：{ai_reason}"


def _validated_multi_value(value: Any, allowed: set[str], label: str) -> list[str]:
    if not isinstance(value, list):
        raise ValidationError(f"{label} must be a list")
    normalized = list(dict.fromkeys(str(item) for item in value))
    unknown = set(normalized) - allowed
    if unknown:
        raise ValidationError(f"invalid {label}: {', '.join(sorted(unknown))}")
    return normalized


def _reuse_suggestions(*, is_good: bool, frequency_level: str, difficulty: str,
                       migration: str, structural_keys: list[str], route: str) -> tuple[list[str], list[str]]:
    """Suggest reusable roles without treating video priority as universal value."""
    roles: list[str] = []
    scenarios: list[str] = []
    if is_good:
        roles.append("基础巩固题" if difficulty == "基础" else "核心例题")
        if difficulty == "基础":
            roles.append("同构练习")
        elif migration == "高":
            roles.append("迁移练习")
        if structural_keys and migration == "高":
            roles.insert(0, "母题候选")
        scenarios.extend(["习题册", "学案", "专题资料"])
        if frequency_level == "高频":
            scenarios.extend(["作业", "备考题池"])
    elif structural_keys and migration == "高":
        roles.append("母题候选")
    if route == "进入课程生产":
        scenarios.insert(0, "视频生产")
    return list(dict.fromkeys(roles)), list(dict.fromkeys(scenarios))
