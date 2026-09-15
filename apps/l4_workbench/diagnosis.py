"""Explainable first-pass diagnosis for standardized question assets."""

from __future__ import annotations

import hashlib
import json
import re
from collections import Counter, defaultdict
from datetime import datetime
from typing import Any

from .domain import ValidationError
from .tagging import canonical_source_fields, label_library_snapshot, profile_for_asset


RULE_VERSION = "production-diagnosis-v0.3"
GENERIC_METHODS = {"", "problem优先", "knowledge优先"}
TAG_SPLIT = re.compile(r"[、,，;；|]+")
YEAR_PATTERN = re.compile(r"20\d{2}")


def now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def split_tags(value: Any) -> list[str]:
    if value in (None, "", [], {}):
        return []
    if isinstance(value, list):
        values = value
    else:
        values = TAG_SPLIT.split(str(value))
    return [str(item).strip() for item in values if str(item).strip()]


def structural_keys(asset: dict[str, Any]) -> list[str]:
    """Use explicit structures only; knowledge tags alone never define a group."""
    fields = canonical_source_fields(asset)
    signatures = split_tags(fields.get("signatures"))
    if signatures:
        return signatures
    method = str(fields.get("method_skeleton") or "").strip()
    return [] if method in GENERIC_METHODS else [method]


def build_diagnostic_run(snapshot: dict[str, Any], assets: list[dict[str, Any]]) -> dict[str, Any]:
    if not assets:
        raise ValidationError("diagnosis requires standardized question assets")
    papers = sorted({str(asset.get("source_name") or "未知试卷") for asset in assets})
    years = sorted({year for paper in papers for year in YEAR_PATTERN.findall(paper)})
    papers_by_key: dict[str, set[str]] = defaultdict(set)
    for asset in assets:
        for key in structural_keys(asset):
            papers_by_key[key].add(str(asset.get("source_name") or "未知试卷"))

    results = [_diagnose_asset(asset, papers, years, papers_by_key) for asset in assets]
    summary = {
        "asset_count": len(results),
        "paper_count": len(papers),
        "year_count": len(years),
        "high_frequency_count": sum(item["frequency"]["level"] == "高频" for item in results),
        "quality_candidate_count": sum(item["quality"]["recommendation"] == "AI候选好题" for item in results),
        "frequency_unresolved_count": sum(item["frequency"]["level"] == "不可判断" for item in results),
        "trend_unresolved_count": sum(item["trend"]["status"] == "证据不足" for item in results),
        "coverage_unresolved_count": sum(item["coverage"]["status"] == "无法判断" for item in results),
        "priority_unresolved_count": sum(item["production_priority"]["status"] != "已形成" for item in results),
    }
    checksum = hashlib.sha256(json.dumps(
        {"snapshot": snapshot["immutable_checksum"], "rule": RULE_VERSION, "assets": [item["id"] for item in assets]},
        ensure_ascii=False, sort_keys=True,
    ).encode("utf-8")).hexdigest()
    return {
        "id": f"diagnosis-{checksum[:14]}",
        "source_snapshot_id": snapshot["id"],
        "source_checksum": snapshot["immutable_checksum"],
        "rule_version": RULE_VERSION,
        "label_library_snapshot": label_library_snapshot(),
        "status": "completed_with_evidence_limits" if len(years) < 2 else "completed",
        "scope": {"papers": papers, "years": years, "paper_count": len(papers), "asset_count": len(assets)},
        "evidence_limits": [
            "当前只有一个考试年份，只能计算同年跨地区复现度，不能形成多年趋势结论。"
        ] if len(years) < 2 else [],
        "summary": summary,
        "results": results,
        "created_at": now(),
    }


def _diagnose_asset(
    asset: dict[str, Any],
    papers: list[str],
    years: list[str],
    papers_by_key: dict[str, set[str]],
) -> dict[str, Any]:
    fields = canonical_source_fields(asset)
    tag_profile = profile_for_asset(asset)
    keys = structural_keys(asset)
    ranked_keys = sorted(keys, key=lambda key: (-len(papers_by_key[key]), key))
    primary_key = ranked_keys[0] if ranked_keys else None
    numerator = len(papers_by_key[primary_key]) if primary_key else 0
    denominator = len(papers)
    rate = numerator / denominator if denominator and primary_key else None
    if primary_key is None:
        frequency_level = "不可判断"
        frequency_reason = "缺少可验证的共同底层结构；不会仅凭同一知识点把题目归为同一高频题型。"
    elif numerator >= 5 and rate is not None and rate >= 0.18:
        frequency_level = "高频"
        frequency_reason = f"底层结构“{primary_key}”出现在 {numerator}/{denominator} 套同年跨地区试卷中。"
    elif numerator >= 2:
        frequency_level = "中频"
        frequency_reason = f"底层结构“{primary_key}”出现在 {numerator}/{denominator} 套同年跨地区试卷中。"
    else:
        frequency_level = "低频"
        frequency_reason = f"底层结构“{primary_key}”仅出现在 {numerator}/{denominator} 套同年跨地区试卷中。"

    text = str(asset.get("raw_text") or asset.get("text") or "")
    has_visual = bool(fields.get("has_visual"))
    image_ok = asset.get("image_integrity") == "preserved"
    difficulty = _number(fields.get("difficulty"))
    question_type = str(tag_profile.get("question_type") or "未分类")
    task_tags = tag_profile["question"]
    core_knowledge_tags = tag_profile["knowledge"]["core"]
    visual_forms = split_tags(fields.get("visual_forms"))
    structure_complete = bool(text and asset.get("question_no") and (not has_visual or image_ok))
    typical = numerator >= 2
    cognitive = difficulty >= 3 and (bool(task_tags) or "基础" not in question_type)
    migration = bool(primary_key and (len(task_tags) >= 2 or len(core_knowledge_tags) >= 2 or bool(visual_forms)))
    dimensions = {
        "结构完整": _dimension(structure_complete, "题干、题号及声明的题图均可追溯" if structure_complete else "题干、题号或题图完整性不足"),
        "典型性": _dimension(typical, f"同构结构覆盖 {numerator} 套试卷" if primary_key else "没有共同底层结构证据"),
        "认知价值": _dimension(cognitive, "包含中等以上认知要求或明确任务" if cognitive else "当前字段显示偏基础或任务信息不足"),
        "迁移价值": _dimension(migration, "结构、任务、知识或图表形成可迁移组合" if migration else "迁移证据不足"),
        "科学性": {"status": "待人工核验", "reason": "现有清单只有 AI 初标，尚未接入官方答案与解析交叉校验"},
    }
    score = sum(value["status"] == "支持" for key, value in dimensions.items() if key != "科学性")
    if asset.get("issue_codes"):
        recommendation = "异常复核"
    elif score >= 3:
        recommendation = "AI候选好题"
    elif score == 2:
        recommendation = "备选"
    else:
        recommendation = "暂不推荐"

    exam_value = "高" if recommendation == "AI候选好题" and frequency_level == "高频" else (
        "中" if recommendation in {"AI候选好题", "备选"} or frequency_level in {"高频", "中频"} else "低"
    )
    priority_reason = "尚未接入视频覆盖与学生需求证据，不能把考试价值直接等同于生产优先级。"
    if asset.get("issue_codes"):
        priority_reason = "题目资产存在异常，先复核原题与题图，再进入生产排序。"
    return {
        "asset_id": asset["id"],
        "source_name": asset.get("source_name"),
        "question_no": asset.get("question_no"),
        "question_type": question_type,
        "difficulty": difficulty,
        "structural_keys": ranked_keys,
        "task_tags": task_tags,
        "tag_profile": tag_profile,
        "frequency": {
            "level": frequency_level, "numerator": numerator, "denominator": denominator,
            "rate": round(rate, 4) if rate is not None else None, "scope": "同年跨地区可比试卷",
            "reason": frequency_reason,
        },
        "quality": {
            "recommendation": recommendation, "final_conclusion": "待教研复核", "score": score,
            "score_denominator": 4, "dimensions": dimensions,
            "reason": f"四项可计算质量维度中 {score}/4 项获得支持；科学性单独待核验。",
        },
        "trend": {
            "status": "证据不足" if len(years) < 2 else "待时序计算",
            "years": years,
            "reason": "至少需要两个年份，且必须按时间冻结规则后才能判断升温或退潮。" if len(years) < 2 else "已具备多年份入口，等待时序回测执行器。",
        },
        "coverage": {
            "status": "无法判断", "evidence_level": "E0",
            "reason": "当前项目尚未接入视频逐字稿、截图或教师人工覆盖结论。",
        },
        "production_priority": {
            "status": "待补证据", "recommendation": None, "preliminary_exam_value": exam_value,
            "reason": priority_reason,
        },
        "candidate_pool": recommendation == "AI候选好题",
        "issue_codes": asset.get("issue_codes", []),
    }


def select_gold_sample(
    diagnostic_run: dict[str, Any],
    assets: list[dict[str, Any]],
    size: int = 40,
) -> dict[str, Any]:
    if size < 10 or size > 100:
        raise ValidationError("gold sample size must be between 10 and 100")
    if size > len(assets):
        size = len(assets)
    results = {item["asset_id"]: item for item in diagnostic_run["results"]}
    selected: list[dict[str, Any]] = []
    selected_ids: set[str] = set()
    covered: set[str] = set()
    paper_counts: Counter[str] = Counter()

    while len(selected) < size:
        candidates = [asset for asset in assets if asset["id"] not in selected_ids]
        if not candidates:
            break
        best = max(candidates, key=lambda asset: _sample_score(asset, results[asset["id"]], covered, paper_counts))
        result = results[best["id"]]
        features = _sample_features(best, result)
        new_features = sorted(features - covered)
        selected.append({
            "asset_id": best["id"], "source_name": best.get("source_name"), "question_no": best.get("question_no"),
            "status": "待校准", "teacher_reason": "", "frequency_level": result["frequency"]["level"],
            "quality_recommendation": result["quality"]["recommendation"],
            "rationale": _sample_rationale(best, result, new_features),
        })
        selected_ids.add(best["id"])
        covered.update(features)
        paper_counts[str(best.get("source_name") or "未知试卷")] += 1

    digest = hashlib.sha256(json.dumps(
        {"diagnosis": diagnostic_run["id"], "size": size, "assets": [item["asset_id"] for item in selected]},
        ensure_ascii=False, sort_keys=True,
    ).encode("utf-8")).hexdigest()
    asset_index = assets_by_id(assets)
    return {
        "id": f"gold-sample-{digest[:14]}", "diagnostic_run_id": diagnostic_run["id"],
        "source_snapshot_id": diagnostic_run["source_snapshot_id"], "status": "待教研校准",
        "target_size": size, "actual_size": len(selected), "items": selected,
        "coverage": {
            "paper_count": len({item["source_name"] for item in selected}),
            "question_types": sorted({results[item["asset_id"]]["question_type"] for item in selected}),
            "difficulty_levels": sorted({results[item["asset_id"]]["difficulty"] for item in selected}),
            "with_visual_count": sum(bool(asset_index[item["asset_id"]].get("source_fields", {}).get("has_visual")) for item in selected),
            "exception_count": sum(bool(item["issue_codes"]) for item in diagnostic_run["results"] if item["asset_id"] in selected_ids),
        },
        "created_at": now(),
    }


def assets_by_id(assets: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {asset["id"]: asset for asset in assets}


def _sample_features(asset: dict[str, Any], result: dict[str, Any]) -> set[str]:
    fields = asset.get("source_fields", {})
    features = {
        f"paper:{asset.get('source_name')}", f"type:{result['question_type']}",
        f"difficulty:{result['difficulty']}", f"visual:{bool(fields.get('has_visual'))}",
        f"frequency:{result['frequency']['level']}", f"quality:{result['quality']['recommendation']}",
    }
    features.update(f"structure:{key}" for key in result["structural_keys"])
    features.update(f"visual-form:{tag}" for tag in split_tags(fields.get("visual_forms")))
    features.update(f"issue:{code}" for code in asset.get("issue_codes", []))
    return features


def _sample_score(
    asset: dict[str, Any],
    result: dict[str, Any],
    covered: set[str],
    paper_counts: Counter[str],
) -> tuple[float, str]:
    features = _sample_features(asset, result)
    weights = {"paper": 8, "issue": 10, "type": 5, "structure": 4, "visual-form": 3, "difficulty": 2, "visual": 2, "frequency": 2, "quality": 2}
    novelty = sum(weights.get(feature.split(":", 1)[0], 1) for feature in features if feature not in covered)
    paper = str(asset.get("source_name") or "未知试卷")
    balance = max(0, 4 - paper_counts[paper])
    diagnostic_value = 3 if result["quality"]["recommendation"] in {"AI候选好题", "异常复核"} else 0
    return novelty + balance + diagnostic_value, str(asset["id"])


def _sample_rationale(asset: dict[str, Any], result: dict[str, Any], new_features: list[str]) -> str:
    reasons = []
    if asset.get("issue_codes"):
        reasons.append("包含异常样本，可校准质量门禁")
    if result["structural_keys"]:
        reasons.append(f"覆盖底层结构：{result['structural_keys'][0]}")
    reasons.append(f"覆盖{result['question_type']}、难度 {result['difficulty']}")
    if new_features:
        reasons.append(f"新增 {len(new_features)} 个抽样覆盖特征")
    return "；".join(reasons)


def _dimension(supported: bool, reason: str) -> dict[str, str]:
    return {"status": "支持" if supported else "证据不足", "reason": reason}


def _number(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0
