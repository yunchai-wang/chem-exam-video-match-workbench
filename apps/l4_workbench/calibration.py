"""Persist optional teacher calibration without blocking the AI workflow."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from typing import Any

from .domain import ValidationError


FREQUENCY_VALUES = {"高频", "中频", "低频", "不可判断"}
QUALITY_VALUES = {"AI候选好题", "备选", "暂不推荐", "异常复核", "好题", "非好题", "待复核"}
SCIENCE_VALUES = {"待人工核验", "待核验", "通过", "有问题"}
COVERAGE_VALUES = {
    "部分覆盖候选", "证据不足", "未发现可核验证据", "无法判断",
    "充分覆盖", "部分覆盖", "组合支撑但缺综合迁移", "未覆盖",
}


def now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def build_calibration_review(
    diagnostic_run: dict[str, Any],
    gold_sample: dict[str, Any],
    coverage_run: dict[str, Any],
    asset: dict[str, Any],
    request: dict[str, Any],
    *,
    mode: str = "single",
) -> dict[str, Any]:
    asset_id = asset["id"]
    diagnosis = _result(diagnostic_run["results"], asset_id, "diagnostic result")
    coverage = _result(coverage_run["results"], asset_id, "coverage result")
    if not any(item["asset_id"] == asset_id for item in gold_sample["items"]):
        raise ValidationError("asset is not part of this gold sample")

    ai_values = {
        "structural_keys": diagnosis["structural_keys"],
        "frequency": diagnosis["frequency"]["level"],
        "quality": diagnosis["quality"]["recommendation"],
        "science": diagnosis["quality"]["dimensions"]["科学性"]["status"],
        "coverage": coverage["status"],
    }
    effective = {
        "structural_keys": _structures(request.get("structural_keys", ai_values["structural_keys"])),
        "frequency": str(request.get("frequency", ai_values["frequency"])),
        "quality": str(request.get("quality", ai_values["quality"])),
        "science": str(request.get("science", ai_values["science"])),
        "coverage": str(request.get("coverage", ai_values["coverage"])),
    }
    _validate_values(effective)
    corrected_fields = [key for key in ai_values if effective[key] != ai_values[key]]
    reason = str(request.get("reason") or "").strip()
    if corrected_fields and not reason:
        raise ValidationError("corrected calibration requires a reason")
    if not reason:
        reason = "教师接受 AI 当前判断及其证据边界。" if mode == "single" else "批量通过：接受非异常样本的 AI 当前判断及证据边界。"

    family_map = {
        "structural_keys": "structure_grouping",
        "frequency": "frequency_rule",
        "quality": "quality_rule",
        "science": "science_check_rule",
        "coverage": "coverage_rule",
    }
    rule_families = [family_map[key] for key in corrected_fields]
    identity = json.dumps({"sample": gold_sample["id"], "asset": asset_id}, ensure_ascii=False, sort_keys=True)
    return {
        "id": f"calibration-{hashlib.sha256(identity.encode('utf-8')).hexdigest()[:14]}",
        "gold_sample_id": gold_sample["id"], "diagnostic_run_id": diagnostic_run["id"],
        "coverage_run_id": coverage_run["id"], "asset_id": asset_id,
        "source_name": diagnosis["source_name"], "question_no": diagnosis["question_no"],
        "status": "corrected" if corrected_fields else "accepted",
        "review_mode": mode, "ai_values": ai_values, "effective_values": effective,
        "corrected_fields": corrected_fields, "reason": reason,
        "reason_source": "teacher" if corrected_fields or request.get("reason") else "system",
        "rule_families": rule_families,
        "rule_proposals": [
            {"family": family, "status": "隔离实验候选", "evidence": reason}
            for family in rule_families
        ],
        "issue_codes": asset.get("issue_codes", []),
        "requires_followup": bool(asset.get("issue_codes")) or effective["science"] in {"待人工核验", "待核验"},
        "ai_flow_blocked": False,
        "updated_at": now(),
    }


def _result(results: list[dict[str, Any]], asset_id: str, label: str) -> dict[str, Any]:
    result = next((item for item in results if item["asset_id"] == asset_id), None)
    if result is None:
        raise ValidationError(f"{label} not found for asset")
    return result


def _structures(value: Any) -> list[str]:
    if not isinstance(value, list):
        raise ValidationError("structural_keys must be a list")
    return sorted({str(item).strip() for item in value if str(item).strip()})


def _validate_values(values: dict[str, Any]) -> None:
    allowed = {
        "frequency": FREQUENCY_VALUES,
        "quality": QUALITY_VALUES,
        "science": SCIENCE_VALUES,
        "coverage": COVERAGE_VALUES,
    }
    for key, options in allowed.items():
        if values[key] not in options:
            raise ValidationError(f"invalid calibration {key}: {values[key]}")
