"""Freeze video manifests and build conservative evidence candidates."""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any

from .diagnosis import GENERIC_METHODS, split_tags
from .domain import ValidationError


VIDEO_ADAPTER_VERSION = "video-manifest-v0.3"
COVERAGE_RULE_VERSION = "production-coverage-v0.4"
STRONG_TRANSCRIPT_STATES = {"强匹配-文件名", "强匹配-文件名+正文", "本地素材直接匹配"}
WEAK_TRANSCRIPT_STATES = {"弱匹配待人工复核", "弱匹配待复核"}
INDEXED_TRANSCRIPT_STATES = {"索引定稿-待打开核验", "索引录音稿-待打开核验"}

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
    listings = []
    duplicate_ids = []
    for video_id, rows in grouped.items():
        if len(rows) > 1:
            duplicate_ids.append(video_id)
        record = max(rows, key=_evidence_rank)
        listings.append({"video_id": video_id, "record": record, "rows": rows})

    title_groups: dict[str, list[dict[str, Any]]] = {}
    for listing in listings:
        key = normalize_video_title(str(listing["record"].get("video_name") or ""))
        title_groups.setdefault(key, []).append(listing)

    clusters: list[list[dict[str, Any]]] = []
    for title_key, title_listings in title_groups.items():
        catalogs = {_catalog_name(item["record"]) for item in title_listings}
        if title_key and len(catalogs) > 1:
            clusters.append(title_listings)
        else:
            clusters.extend([[item] for item in title_listings])

    assets = []
    overlap_groups = []
    for cluster in clusters:
        cluster_rows = [row for listing in cluster for row in listing["rows"]]
        record = max(cluster_rows, key=_evidence_rank)
        video_ids = sorted({str(listing["video_id"]) for listing in cluster})
        video_id = str(record["video_id"])
        catalogs = sorted({_catalog_name(row) for row in cluster_rows})
        is_cross_catalog_cluster = len(catalogs) > 1
        confirmed_identity = is_cross_catalog_cluster and _identity_is_confirmed(cluster_rows, catalogs)
        canonical_key = (
            f"title:{normalize_video_title(str(record['video_name']))}"
            if is_cross_catalog_cluster else f"video-id:{video_id}"
        )
        transcript_status = str(record.get("transcript_match_status_v5") or record.get("transcript_match_status") or "未匹配")
        screenshot_tokens = sorted({token for row in cluster_rows for token in split_tags(row.get("screenshot_tokens"))})
        evidence_level = "E2" if transcript_status in STRONG_TRANSCRIPT_STATES else ("E1" if transcript_status in WEAK_TRANSCRIPT_STATES or screenshot_tokens else "E0")
        issues = []
        if any(len(listing["rows"]) > 1 for listing in cluster):
            issues.append("duplicate_video_id")
        if is_cross_catalog_cluster and not confirmed_identity:
            issues.append("cross_catalog_identity_needs_review")
        if transcript_status in WEAK_TRANSCRIPT_STATES:
            issues.append("transcript_match_needs_review")
        elif transcript_status in {"未匹配", "None", ""}:
            issues.append("transcript_not_verified")
        if not screenshot_tokens:
            issues.append("video_screenshot_not_materialized")
        structures = sorted({
            value
            for row in cluster_rows
            for value in (
                split_tags(row.get("signatures"))
                or ([] if str(row.get("method_skeleton") or "").strip() in GENERIC_METHODS else [str(row.get("method_skeleton") or "").strip()])
            )
            if value
        })
        memberships = _catalog_memberships(cluster_rows)
        lesson_mode = _lesson_mode(record)
        identity_status = (
            "已确认同一视频" if confirmed_identity
            else ("候选同一视频" if is_cross_catalog_cluster else "单一目录实体")
        )
        assets.append({
            "id": f"video-{hashlib.sha256(canonical_key.encode('utf-8')).hexdigest()[:14]}",
            "source_snapshot_id": snapshot["id"], "video_id": video_id,
            "video_name": record["video_name"], "source": record.get("source"),
            "catalogs": catalogs, "catalog_memberships": memberships,
            "alias_video_ids": video_ids, "identity_status": identity_status,
            "identity_basis": (
                "backend_id_or_explicit_reuse" if confirmed_identity
                else ("exact_normalized_title_across_catalogs" if is_cross_catalog_cluster else "video_id")
            ),
            "hierarchy": record.get("hierarchy"), "question_type": record.get("primary_type") or record.get("question_type"),
            "structural_keys": structures,
            "task_tags": sorted({
                value for row in cluster_rows
                for field in ("task_tags", "question_tags", "问题标签")
                for value in split_tags(row.get(field))
            }),
            "lesson_mode": lesson_mode,
            "knowledge_contract": (
                "concept_lesson_video" if lesson_mode == "概念课"
                else ("problem_lesson_video" if lesson_mode == "解题课" else "待识别")
            ),
            "all_knowledge_tags": sorted({
                value for row in cluster_rows
                for field in ("knowledge_tags", "知识点标签", "全部涉及知识")
                for value in split_tags(row.get(field))
            }),
            "teaching_target_tags": sorted({value for row in cluster_rows for value in split_tags(row.get("teaching_target_tags") or row.get("核心知识点标签"))}),
            "prerequisite_knowledge_tags": sorted({
                value for row in cluster_rows
                for value in split_tags(row.get("prerequisite_knowledge_tags") or row.get("前置知识点标签") or row.get("工具知识点"))
            }),
            "mentioned_knowledge_tags": sorted({value for row in cluster_rows for value in split_tags(row.get("mentioned_knowledge_tags") or row.get("仅提及知识点"))}),
            "question_tags": sorted({value for row in cluster_rows for value in split_tags(row.get("question_tags") or row.get("问题标签"))}),
            "solution_tags": sorted({value for row in cluster_rows for value in split_tags(row.get("solution_tags") or row.get("解法标签"))}),
            "condition_tags": sorted({value for row in cluster_rows for value in split_tags(row.get("condition_tags") or row.get("条件标签"))}),
            "context_tags": sorted({value for row in cluster_rows for value in split_tags(row.get("context_tags") or row.get("情景标签") or row.get("情境标签"))}),
            "thinking_method_tags": sorted({value for row in cluster_rows for value in split_tags(row.get("thinking_method_tags") or row.get("思想方法标签"))}),
            "segment_type": record.get("segment_type") or record.get("视频片段类型") or "未标注",
            "segment_locator": record.get("segment_locator") or record.get("时间码") or record.get("页码") or "",
            "visual_forms": sorted({value for row in cluster_rows for value in split_tags(row.get("visual_forms"))}),
            "method_models": sorted({value for row in cluster_rows for value in split_tags(row.get("method_models"))}),
            "content_summary": record.get("content_summary") or "", "transcript_evidence": record.get("transcript_evidence") or "",
            "transcript_status": transcript_status, "screenshot_refs": record.get("screenshot_refs") or "",
            "screenshot_tokens": screenshot_tokens, "evidence_level": evidence_level,
            "issue_codes": issues, "duplicate_source_rows": len(cluster_rows), "raw_fields": record,
        })
        if is_cross_catalog_cluster:
            overlap_groups.append({
                "entity_id": assets[-1]["id"], "video_name": record["video_name"],
                "catalogs": catalogs, "alias_video_ids": video_ids,
                "identity_status": identity_status,
            })
    status_counts = Counter(asset["transcript_status"] for asset in assets)
    catalog_counts = Counter(_catalog_name(record) for record in records)
    catalog_unique_counts = Counter(catalog for asset in assets for catalog in asset["catalogs"])
    confirmed_overlap_count = sum(item["identity_status"] == "已确认同一视频" for item in overlap_groups)
    import_id = hashlib.sha256(f"{snapshot['immutable_checksum']}|{VIDEO_ADAPTER_VERSION}".encode("utf-8")).hexdigest()
    return {
        "id": f"video-import-{import_id[:14]}", "source_snapshot_id": snapshot["id"],
        "adapter_version": VIDEO_ADAPTER_VERSION, "status": "completed_with_issues" if any(asset["issue_codes"] for asset in assets) else "completed",
        "record_count": len(records), "listing_count": len(records), "video_asset_count": len(assets),
        "catalog_counts": dict(catalog_counts), "catalog_unique_entity_counts": dict(catalog_unique_counts),
        "cross_catalog_entity_count": len(overlap_groups),
        "confirmed_cross_catalog_entity_count": confirmed_overlap_count,
        "candidate_cross_catalog_entity_count": len(overlap_groups) - confirmed_overlap_count,
        "overlap_groups": overlap_groups,
        "duplicate_video_ids": duplicate_ids,
        "transcript_status_counts": dict(status_counts), "video_assets": assets, "created_at": now(),
    }


def build_coverage_run(
    diagnostic_run: dict[str, Any],
    gold_sample: dict[str, Any],
    video_import: dict[str, Any],
    video_assets: list[dict[str, Any]],
    exclusions: list[dict[str, Any]] | None = None,
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
                question_core = set(question.get("tag_profile", {}).get("knowledge", {}).get("core", []))
                target_overlap = sorted(question_core & set(video.get("teaching_target_tags", [])))
                mentioned_overlap = sorted(question_core & set(video.get("mentioned_knowledge_tags", [])))
                target_gate = _teaching_target_gate(question_core, video, target_overlap, mentioned_overlap)
                candidate = _coverage_candidate(video, key, overlap, target_overlap, target_gate)
                previous = candidates.get(video["video_id"])
                if previous is None or candidate["rank_score"] > previous["rank_score"]:
                    candidates[video["video_id"]] = candidate
        ranked = sorted(candidates.values(), key=lambda item: (-item["rank_score"], item["video_id"]))[:3]
        item = {
            "asset_id": question["asset_id"], "source_name": question["source_name"], "question_no": question["question_no"],
            "status": "", "reason": "", "candidates": ranked, "teacher_conclusion": "待复核",
            "structure_missing": not bool(question.get("structural_keys")),
        }
        _recompute_coverage_result(item)
        results.append(item)
    digest = hashlib.sha256(json.dumps({
        "diagnosis": diagnostic_run["id"], "sample": gold_sample["id"], "video_import": video_import["id"], "rule": COVERAGE_RULE_VERSION,
    }, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()
    run = {
        "id": f"coverage-{digest[:14]}", "diagnostic_run_id": diagnostic_run["id"],
        "gold_sample_id": gold_sample["id"], "video_import_id": video_import["id"],
        "rule_version": COVERAGE_RULE_VERSION, "status": "completed_with_manual_review",
        "summary": {}, "result_count": len(results), "results": results,
        "label_library_snapshot_id": diagnostic_run.get("label_library_snapshot", {}).get("id"),
        "evidence_limits": [
            "原宣传匹配结论未被复用为生产覆盖结论。",
            "错误选项或内容中仅提及的知识点不算视频教学目标。",
            "自动结果最高只到“部分覆盖候选”，充分覆盖必须核验小问与作答边界。",
            "教师可排除错配候选；排除需写理由，不阻塞后续 AI 运行。",
        ],
        "created_at": now(),
    }
    apply_candidate_exclusions(run, exclusions or [])
    return run


def apply_candidate_exclusions(run: dict[str, Any], exclusions: list[dict[str, Any]]) -> dict[str, Any]:
    """Mark teacher-excluded video candidates and recompute per-question coverage status."""
    active = {
        (item["asset_id"], item["video_id"]): item
        for item in exclusions
        if item.get("status", "excluded") == "excluded"
    }
    for result in run.get("results") or []:
        rewritten = []
        for candidate in result.get("candidates") or []:
            key = (result["asset_id"], candidate["video_id"])
            if key in active:
                exclusion = active[key]
                reason = str(exclusion.get("reason") or "").strip()
                rewritten.append({
                    **candidate,
                    "excluded": True,
                    "coverage_candidate": False,
                    "rank_score": -1,
                    "exclusion_id": exclusion.get("id"),
                    "reason": f"教师已排除本候选{'：' + reason if reason else '。'}",
                })
            else:
                rewritten.append({**candidate, "excluded": False})
        rewritten.sort(key=lambda item: (-item.get("rank_score", 0), item["video_id"]))
        result["candidates"] = rewritten
        _recompute_coverage_result(result)
    run["summary"] = dict(Counter(item["status"] for item in run.get("results") or []))
    run["exclusion_count"] = sum(
        1 for result in run.get("results") or [] for candidate in result.get("candidates") or [] if candidate.get("excluded")
    )
    return run


def _recompute_coverage_result(result: dict[str, Any]) -> None:
    if result.get("structure_missing"):
        result["status"] = "无法判断"
        result["reason"] = "题目缺少可验证的共同底层结构，不能仅凭知识点召回视频。"
        return
    active = [item for item in result.get("candidates") or [] if not item.get("excluded")]
    if not active:
        if any(item.get("excluded") for item in result.get("candidates") or []):
            result["status"] = "未发现可核验证据"
            result["reason"] = "可用候选均已被教师排除；这不等于确认课库绝对未覆盖。"
        else:
            result["status"] = "未发现可核验证据"
            result["reason"] = "现有视频元数据中没有命中同一底层结构；这不等同于确认课库绝对未覆盖。"
        return
    if active[0].get("coverage_candidate"):
        result["status"] = "部分覆盖候选"
        result["reason"] = "至少一个视频同时满足底层结构和设问任务门禁；仍需核对小问、作答边界及原视频截图。"
    else:
        result["status"] = "证据不足"
        result["reason"] = "仅找到结构相近或逐字稿证据较弱的视频，按生产口径不判定已覆盖。"


def load_video_records(path: Path) -> list[dict[str, Any]]:
    path = Path(path).expanduser().resolve()
    if not path.is_file() or path.suffix.lower() != ".json":
        raise ValidationError("video manifest path must be an existing JSON file")
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, list) or not all(isinstance(item, dict) for item in value):
        raise ValidationError("video manifest must be a JSON row list")
    return value


def _coverage_candidate(
    video: dict[str, Any],
    structure: str,
    task_overlap: list[str],
    target_overlap: list[str],
    target_gate: str,
) -> dict[str, Any]:
    strong_transcript = (
        video["transcript_status"] in STRONG_TRANSCRIPT_STATES
        or video["transcript_status"] in INDEXED_TRANSCRIPT_STATES
    )
    coverage_candidate = bool(task_overlap and strong_transcript and target_gate in {"passed", "question_core_unresolved"})
    rank_score = 4 + min(len(task_overlap), 3) * 3 + (3 if strong_transcript else 0) + (2 if target_overlap else 0) + (1 if video["screenshot_tokens"] else 0)
    if coverage_candidate:
        target_reason = f"，教学目标交集为{'、'.join(target_overlap)}" if target_overlap else "；题目核心知识待识别，暂未启用知识目标门禁"
        index_note = "索引定稿/录音稿可打开核验" if video["transcript_status"] in INDEXED_TRANSCRIPT_STATES else "逐字稿摘要可核验"
        reason = f"同一底层结构“{structure}”，任务交集为{'、'.join(task_overlap)}{target_reason}；{index_note}。"
    elif target_gate == "mentioned_only":
        reason = "共同核心知识在视频中只是被提及，并非该片段教学目标，按生产口径不算覆盖。"
    elif target_gate == "target_missing":
        reason = "题目已有核心知识标签，但视频未标教学目标；不能把逐字稿中的出现直接当作覆盖。"
    elif target_gate == "target_mismatch":
        reason = "底层结构相近，但视频教学目标与题目核心知识不相交。"
    elif task_overlap:
        reason = f"结构与任务相交，但逐字稿匹配状态为“{video['transcript_status']}”，证据不足。"
    else:
        reason = f"只命中底层结构“{structure}”，未通过设问任务门禁。"
    preferred = ((video.get("evidence_index") or {}).get("preferred_transcript") or {})
    return {
        "video_id": video["video_id"], "video_name": video["video_name"], "structure": structure,
        "catalogs": video.get("catalogs") or [video.get("source") or "未标注课库"],
        "catalog_memberships": video.get("catalog_memberships") or [],
        "alias_video_ids": video.get("alias_video_ids") or [video["video_id"]],
        "identity_status": video.get("identity_status") or "单一目录实体",
        "task_overlap": task_overlap, "transcript_status": video["transcript_status"],
        "teaching_target_overlap": target_overlap, "teaching_target_gate": target_gate,
        "teaching_target_tags": video.get("teaching_target_tags", []),
        "lesson_mode": video.get("lesson_mode", "未识别"),
        "knowledge_contract": video.get("knowledge_contract", "待识别"),
        "prerequisite_knowledge_tags": video.get("prerequisite_knowledge_tags", []),
        "mentioned_knowledge_tags": video.get("mentioned_knowledge_tags", []),
        "segment_type": video.get("segment_type", "未标注"), "segment_locator": video.get("segment_locator", ""),
        "evidence_level": video["evidence_level"], "screenshot_materialized": bool(video.get("screenshot_tokens")),
        "preferred_transcript": preferred or None,
        "coverage_candidate": coverage_candidate, "rank_score": rank_score, "reason": reason,
    }


def _teaching_target_gate(
    question_core: set[str],
    video: dict[str, Any],
    target_overlap: list[str],
    mentioned_overlap: list[str],
) -> str:
    if not question_core:
        return "question_core_unresolved"
    if target_overlap:
        return "passed"
    if mentioned_overlap:
        return "mentioned_only"
    if not video.get("teaching_target_tags"):
        return "target_missing"
    return "target_mismatch"


def _lesson_mode(record: dict[str, Any]) -> str:
    value = " ".join(str(record.get(key) or "") for key in ("lesson_mode", "course_type", "课程类型", "content_type"))
    if "概念" in value:
        return "概念课"
    if any(marker in value for marker in ("解题", "培优", "总复习", "题型")):
        return "解题课"
    return "未识别"


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


def normalize_video_title(value: str) -> str:
    """Normalize only presentation differences; keep 上/中/下 and semantic words."""
    text = unicodedata.normalize("NFKC", value or "").lower().replace("图象", "图像")
    return re.sub(r"[\s·—_\-:：,，。()（）【】\[\]]+", "", text)


def _catalog_name(record: dict[str, Any]) -> str:
    return str(record.get("catalog") or record.get("source") or "未标注课库").strip() or "未标注课库"


def _catalog_memberships(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    memberships = []
    seen = set()
    for row in rows:
        membership = {
            "catalog": _catalog_name(row),
            "listing_video_id": str(row.get("video_id") or ""),
            "backend_video_id": str(row.get("backend_video_id") or row.get("vm_backend_id") or ""),
            "sheet_id": str(row.get("sheet_id") or ""),
            "source_row": int(row.get("source_row") or 0),
            "sheet_url": str(row.get("sheet_url") or ""),
            "hierarchy": str(row.get("hierarchy") or ""),
            "explicit_reuse_source": str(row.get("explicit_reuse_source") or ""),
        }
        key = tuple(membership.values())
        if key not in seen:
            seen.add(key)
            memberships.append(membership)
    return sorted(memberships, key=lambda item: (item["catalog"], item["source_row"], item["listing_video_id"]))


def _identity_is_confirmed(rows: list[dict[str, Any]], catalogs: list[str]) -> bool:
    backend_ids = {str(row.get("backend_video_id") or row.get("vm_backend_id") or "").strip() for row in rows}
    backend_ids.discard("")
    if len(backend_ids) == 1 and all(row.get("backend_video_id") or row.get("vm_backend_id") for row in rows):
        return True
    reuse_text = " ".join(str(row.get("explicit_reuse_source") or "") for row in rows)
    return "共用" in reuse_text or any(catalog in reuse_text for catalog in catalogs)
