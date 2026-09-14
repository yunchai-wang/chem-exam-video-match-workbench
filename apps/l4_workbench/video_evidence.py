"""Freeze video manifests and build conservative evidence candidates."""

from __future__ import annotations

import hashlib
import json
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any

from .diagnosis import GENERIC_METHODS, split_tags
from .domain import ValidationError


VIDEO_ADAPTER_VERSION = "video-manifest-v0.1"
COVERAGE_RULE_VERSION = "production-coverage-v0.2"
STRONG_TRANSCRIPT_STATES = {"强匹配-文件名", "强匹配-文件名+正文", "本地素材直接匹配"}
WEAK_TRANSCRIPT_STATES = {"弱匹配待人工复核", "弱匹配待复核"}

# A production match cannot rely on a structure label copied from an older
# promotion-oriented manifest. At least one anchor group must also appear in
# the video's title, hierarchy or content evidence. Each inner tuple is an AND
# group; groups are alternatives.
STRUCTURE_ANCHOR_GROUPS: dict[str, tuple[tuple[str, ...], ...]] = {
    "NaOH与CO2反应探究": (("NaOH", "CO2"), ("氢氧化钠", "二氧化碳")),
    "催化剂探究": (("催化剂",), ("催化", "反应速率")),
    "化合价与化学式": (("化合价", "化学式"),),
    "实验室制取氧气": (("实验室", "制取氧气"), ("实验室制氧气",), ("制取氧气",)),
    "工艺流程": (("工艺流程",), ("流程题",)),
    "排除干扰因素": (("排除干扰",),),
    "控制变量实验": (("控制变量",),),
    "推断题": (("推断题",),),
    "方程式配平": (("方程式", "配平"),),
    "水的净化": (("水的净化",),),
    "测定空气中氧气含量": (("空气", "氧气含量"),),
    "溶液稀释与质量分数": (("溶液", "稀释"), ("溶液", "质量分数")),
    "溶解度曲线": (("溶解度", "曲线"),),
    "燃烧条件探究": (("燃烧", "条件"),),
    "电解水实验": (("电解水",),),
    "石灰水与CO2异常": (("石灰水", "CO2"), ("石灰水", "二氧化碳")),
    "碳酸盐鉴别": (("碳酸盐", "鉴别"),),
    "科普阅读": (("科普", "阅读"),),
    "表格图像综合计算": (("表格", "计算"), ("图像", "计算")),
    "质量守恒与方程式计算": (("质量守恒", "计算"), ("方程式", "计算")),
    "酸碱中和pH图像": (("中和", "pH"), ("酸碱", "pH")),
    "酸碱盐性质探究": (("酸碱盐", "探究"), ("酸碱盐性质",)),
    "金属与酸图像": (("金属", "酸", "图像"),),
    "金属回收工艺": (("金属", "回收"),),
    "金属置换先后": (("金属", "置换"), ("金属", "反应先后")),
}


def now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def build_video_import(snapshot: dict[str, Any], records: list[dict[str, Any]]) -> dict[str, Any]:
    if not records:
        raise ValidationError("video manifest contains no records")
    missing_required = [index for index, record in enumerate(records, 1) if not record.get("video_id") or not record.get("video_name")]
    if missing_required:
        raise ValidationError(f"video manifest rows missing video_id/video_name: {missing_required[:10]}")

    grouped: dict[str, list[dict[str, Any]]] = {}
    for record in records:
        grouped.setdefault(str(record["video_id"]), []).append(record)
    assets = []
    duplicate_ids = []
    for video_id, rows in grouped.items():
        if len(rows) > 1:
            duplicate_ids.append(video_id)
        record = max(rows, key=_evidence_rank)
        transcript_status = str(record.get("transcript_match_status_v5") or record.get("transcript_match_status") or "未匹配")
        screenshot_tokens = split_tags(record.get("screenshot_tokens"))
        evidence_level = "E2" if transcript_status in STRONG_TRANSCRIPT_STATES else ("E1" if transcript_status in WEAK_TRANSCRIPT_STATES or screenshot_tokens else "E0")
        issues = []
        if len(rows) > 1:
            issues.append("duplicate_video_id")
        if transcript_status in WEAK_TRANSCRIPT_STATES:
            issues.append("transcript_match_needs_review")
        elif transcript_status in {"未匹配", "None", ""}:
            issues.append("transcript_not_verified")
        if not screenshot_tokens:
            issues.append("video_screenshot_not_materialized")
        signatures = split_tags(record.get("signatures"))
        method = str(record.get("method_skeleton") or "").strip()
        structures = signatures or ([] if method in GENERIC_METHODS else [method])
        assets.append({
            "id": f"video-{hashlib.sha256(video_id.encode('utf-8')).hexdigest()[:14]}",
            "source_snapshot_id": snapshot["id"], "video_id": video_id,
            "video_name": record["video_name"], "source": record.get("source"),
            "hierarchy": record.get("hierarchy"), "question_type": record.get("primary_type") or record.get("question_type"),
            "structural_keys": structures, "task_tags": split_tags(record.get("task_tags")),
            "visual_forms": split_tags(record.get("visual_forms")), "method_models": split_tags(record.get("method_models")),
            "content_summary": record.get("content_summary") or "", "transcript_evidence": record.get("transcript_evidence") or "",
            "transcript_status": transcript_status, "screenshot_refs": record.get("screenshot_refs") or "",
            "screenshot_tokens": screenshot_tokens, "evidence_level": evidence_level,
            "issue_codes": issues, "duplicate_source_rows": len(rows), "raw_fields": record,
        })
    status_counts = Counter(asset["transcript_status"] for asset in assets)
    import_id = hashlib.sha256(f"{snapshot['immutable_checksum']}|{VIDEO_ADAPTER_VERSION}".encode("utf-8")).hexdigest()
    return {
        "id": f"video-import-{import_id[:14]}", "source_snapshot_id": snapshot["id"],
        "adapter_version": VIDEO_ADAPTER_VERSION, "status": "completed_with_issues" if any(asset["issue_codes"] for asset in assets) else "completed",
        "record_count": len(records), "video_asset_count": len(assets), "duplicate_video_ids": duplicate_ids,
        "transcript_status_counts": dict(status_counts), "video_assets": assets, "created_at": now(),
    }


def build_coverage_run(
    diagnostic_run: dict[str, Any],
    gold_sample: dict[str, Any],
    video_import: dict[str, Any],
    video_assets: list[dict[str, Any]],
) -> dict[str, Any]:
    if not video_assets:
        raise ValidationError("coverage diagnosis requires video assets")
    diagnosis_by_asset = {item["asset_id"]: item for item in diagnostic_run["results"]}
    video_by_structure: dict[str, list[dict[str, Any]]] = {}
    for video in video_assets:
        for key in video["structural_keys"]:
            video_by_structure.setdefault(key, []).append(video)
    results = []
    for sample_item in gold_sample["items"]:
        question = diagnosis_by_asset[sample_item["asset_id"]]
        candidates: dict[str, dict[str, Any]] = {}
        question_tasks = set(_meaningful_tasks(question))
        for key in question["structural_keys"]:
            for video in video_by_structure.get(key, []):
                if not _supports_structure(video, key):
                    continue
                overlap = sorted(question_tasks & set(video["task_tags"]))
                candidate = _coverage_candidate(video, key, overlap)
                previous = candidates.get(video["video_id"])
                if previous is None or candidate["rank_score"] > previous["rank_score"]:
                    candidates[video["video_id"]] = candidate
        ranked = sorted(candidates.values(), key=lambda item: (-item["rank_score"], item["video_id"]))[:3]
        if not question["structural_keys"]:
            status = "无法判断"
            reason = "题目缺少可验证的共同底层结构，不能仅凭知识点召回视频。"
        elif not ranked:
            status = "未发现可核验证据"
            reason = "现有视频元数据中没有命中同一底层结构；这不等同于确认课库绝对未覆盖。"
        elif ranked[0]["coverage_candidate"]:
            status = "部分覆盖候选"
            reason = "至少一个视频同时满足底层结构和设问任务门禁；仍需核对小问、作答边界及原视频截图。"
        else:
            status = "证据不足"
            reason = "仅找到结构相近或逐字稿证据较弱的视频，按生产口径不判定已覆盖。"
        results.append({
            "asset_id": question["asset_id"], "source_name": question["source_name"], "question_no": question["question_no"],
            "status": status, "reason": reason, "candidates": ranked, "teacher_conclusion": "待复核",
        })
    summary = Counter(item["status"] for item in results)
    digest = hashlib.sha256(json.dumps({
        "diagnosis": diagnostic_run["id"], "sample": gold_sample["id"], "video_import": video_import["id"], "rule": COVERAGE_RULE_VERSION,
    }, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()
    return {
        "id": f"coverage-{digest[:14]}", "diagnostic_run_id": diagnostic_run["id"],
        "gold_sample_id": gold_sample["id"], "video_import_id": video_import["id"],
        "rule_version": COVERAGE_RULE_VERSION, "status": "completed_with_manual_review",
        "summary": dict(summary), "result_count": len(results), "results": results,
        "evidence_limits": ["原宣传匹配结论未被复用为生产覆盖结论。", "自动结果最高只到“部分覆盖候选”，充分覆盖必须核验小问与作答边界。"],
        "created_at": now(),
    }


def load_video_records(path: Path) -> list[dict[str, Any]]:
    path = Path(path).expanduser().resolve()
    if not path.is_file() or path.suffix.lower() != ".json":
        raise ValidationError("video manifest path must be an existing JSON file")
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, list) or not all(isinstance(item, dict) for item in value):
        raise ValidationError("video manifest must be a JSON row list")
    return value


def _coverage_candidate(video: dict[str, Any], structure: str, task_overlap: list[str]) -> dict[str, Any]:
    strong_transcript = video["transcript_status"] in STRONG_TRANSCRIPT_STATES
    coverage_candidate = bool(task_overlap and strong_transcript)
    rank_score = 4 + min(len(task_overlap), 3) * 3 + (3 if strong_transcript else 0) + (1 if video["screenshot_tokens"] else 0)
    if coverage_candidate:
        reason = f"同一底层结构“{structure}”，且任务交集为{'、'.join(task_overlap)}；逐字稿摘要可核验。"
    elif task_overlap:
        reason = f"结构与任务相交，但逐字稿匹配状态为“{video['transcript_status']}”，证据不足。"
    else:
        reason = f"只命中底层结构“{structure}”，未通过设问任务门禁。"
    return {
        "video_id": video["video_id"], "video_name": video["video_name"], "structure": structure,
        "task_overlap": task_overlap, "transcript_status": video["transcript_status"],
        "evidence_level": video["evidence_level"], "screenshot_materialized": False,
        "coverage_candidate": coverage_candidate, "rank_score": rank_score, "reason": reason,
    }


def _meaningful_tasks(question: dict[str, Any]) -> list[str]:
    task_hints = list(question.get("task_tags") or [])
    for key in question["structural_keys"]:
        if any(token in key for token in ("探究", "控制变量")):
            task_hints.extend(["设计方案", "解释原因"])
        if any(token in key for token in ("曲线", "图像", "表格")):
            task_hints.append("信息提取")
        if any(token in key for token in ("流程", "回收")):
            task_hints.extend(["操作", "写方程式"])
    return sorted(set(task_hints))


def _supports_structure(video: dict[str, Any], structure: str) -> bool:
    evidence_text = " ".join(str(video.get(key) or "") for key in (
        "video_name", "hierarchy", "content_summary", "transcript_evidence",
    )).lower()
    anchor_groups = STRUCTURE_ANCHOR_GROUPS.get(structure, ((structure,),))
    return any(all(token.lower() in evidence_text for token in group) for group in anchor_groups)


def _evidence_rank(record: dict[str, Any]) -> tuple[int, int]:
    status = str(record.get("transcript_match_status_v5") or record.get("transcript_match_status") or "")
    rank = 2 if status in STRONG_TRANSCRIPT_STATES else (1 if status in WEAK_TRANSCRIPT_STATES else 0)
    return rank, len(str(record.get("content_summary") or ""))
