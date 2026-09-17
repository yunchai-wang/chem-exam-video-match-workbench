"""Read-only sync of the live six-dimension label library into a local snapshot.

The Feishu Base stays the source of truth. This module (1) optionally pulls
the six tables with ``lark-cli`` (read-only, user identity, proxy disabled),
(2) normalises rows into a versioned vocabulary with explicit status and
old→new mappings, and (3) audits existing question tags against it. Unknown
labels go to the unmatched queue; nothing is silently dropped or rewritten.
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Any

from .domain import ValidationError
from .tagging import DEFAULT_LABEL_LIBRARY_SNAPSHOT, TAG_DIMENSIONS

SYNC_VERSION = "label-library-sync-v0.1"
BATCH_ALIGN_VERSION = "label-batch-align-v0.2"
DIMENSION_LABELS = {
    "knowledge": "知识点", "solution": "解法", "condition": "条件",
    "question": "问题", "context": "情景", "thinking_method": "思想方法",
}
LEVEL_FIELDS = {
    "knowledge": ["层级1", "层级2", "层级3（旧标签）", "层级4（新标签）"],
    "solution": ["解法标签1级", "解法标签2级"],
    "condition": ["条件标签1级", "条件标签2级-定稿", "条件标签2级"],
    "question": ["问题标签1级", "问题标签2级"],
    "context": ["情景标签1级", "情景标签2级"],
    "thinking_method": ["思想方法标签"],
}
TERMINAL_FIELDS = {
    "knowledge": "层级4（新标签）", "solution": "解法标签2级", "condition": "条件标签2级-定稿",
    "question": "问题标签2级", "context": "情景标签2级", "thinking_method": "思想方法标签",
}
STATUS_FIELDS = ("删除/改", "删除", "Text 5")
STATUS_MAP = {
    "": "现行", "不用改": "现行", "不删除": "现行", "恢复": "现行",
    "删除": "已删除", "已改": "已修改", "新增": "新增", "待定": "待定",
}
NOTE_FIELDS = ("备注", "备注by云钗", "备注by吕莉/天怡", "给AI的备注", "天怡批注", "二次备注", "对于标签的补充说明")
ACTIVE_STATUSES = {"现行", "已修改", "新增"}


def now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def dimension_tables(reference: dict[str, Any] | None = None) -> dict[str, str]:
    reference = reference or DEFAULT_LABEL_LIBRARY_SNAPSHOT
    return {dimension: meta["table_id"] for dimension, meta in reference["source"]["tables"].items()}


def fetch_label_tables(raw_dir: Path, base_token: str | None = None, *, identity: str = "user") -> dict[str, Any]:
    """Pull field schemas and all records read-only via lark-cli into raw_dir."""
    base_token = base_token or DEFAULT_LABEL_LIBRARY_SNAPSHOT["source"]["base_token"]
    raw_dir.mkdir(parents=True, exist_ok=True)
    env = {**os.environ, "LARK_CLI_NO_PROXY": "1"}
    report: dict[str, Any] = {"base_token": base_token, "tables": {}, "fetched_at": now()}
    base_meta = _run(["lark-cli", "base", "+base-get", "--base-token", base_token, "--as", identity], env)
    report["base"] = (base_meta.get("data") or {}).get("base", {})
    table_list = _run(["lark-cli", "base", "+table-list", "--base-token", base_token, "--as", identity], env)
    live_tables = {item["id"]: item for item in (table_list.get("data") or {}).get("tables", [])}
    for dimension, table_id in dimension_tables().items():
        fields = _run(["lark-cli", "base", "+field-list", "--base-token", base_token, "--table-id", table_id, "--as", identity], env)
        (raw_dir / f"{table_id}.fields.json").write_text(json.dumps(fields, ensure_ascii=False, indent=1), encoding="utf-8")
        target = raw_dir / f"{table_id}.records.ndjson"
        listing = _run([
            "lark-cli", "base", "+record-list", "--base-token", base_token, "--table-id", table_id,
            "--format", "ndjson", "--output", str(target), "--as", identity,
        ], env)
        data = listing.get("data") or listing
        if data.get("has_more"):
            raise ValidationError(f"table {table_id} returned has_more=true; refuse to store a truncated vocabulary")
        report["tables"][dimension] = {
            "table_id": table_id, "records_count": data.get("records_count"),
            "name": live_tables.get(table_id, {}).get("name"), "rev": live_tables.get(table_id, {}).get("rev"),
        }
    (raw_dir / "fetch-report.json").write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    return report


def build_label_library_snapshot(raw_dir: Path, *, values_path: Path | None = None) -> dict[str, Any]:
    """Normalise raw NDJSON tables into a versioned local vocabulary snapshot."""
    reference = DEFAULT_LABEL_LIBRARY_SNAPSHOT
    tables = dimension_tables(reference)
    fetch_report = _read_json(raw_dir / "fetch-report.json") or {}
    dimensions: dict[str, Any] = {}
    old_to_new: list[dict[str, Any]] = []
    for dimension, table_id in tables.items():
        records_path = raw_dir / f"{table_id}.records.ndjson"
        if not records_path.is_file():
            raise ValidationError(f"missing raw records for {DIMENSION_LABELS[dimension]}: {records_path.name}")
        rows = [json.loads(line) for line in records_path.read_text(encoding="utf-8").splitlines() if line.strip()]
        labels = [_normalize_row(dimension, row) for row in rows]
        labels = [item for item in labels if item["label"]]
        status_counts: dict[str, int] = {}
        for item in labels:
            status_counts[item["status"]] = status_counts.get(item["status"], 0) + 1
        active = [item["label"] for item in labels if item["status"] in ACTIVE_STATUSES]
        seen: dict[str, int] = {}
        for label in active:
            seen[label] = seen.get(label, 0) + 1
        for item in labels:
            if item.get("old_label") and item["old_label"] != item["label"]:
                old_to_new.append({"dimension": dimension, "old": item["old_label"], "new": item["label"], "record_id": item["record_id"], "status": item["status"]})
        fields_meta = _read_json(raw_dir / f"{table_id}.fields.json") or {}
        field_names = [field.get("name") for field in ((fields_meta.get("data") or {}).get("fields") or [])]
        dimensions[dimension] = {
            "label": DIMENSION_LABELS[dimension], "table_id": table_id,
            "reference_revision": reference["source"]["tables"][dimension]["revision"],
            "live_revision": (fetch_report.get("tables", {}).get(dimension) or {}).get("rev"),
            "total": len(rows), "labelled": len(labels), "unlabelled_rows": len(rows) - len(labels),
            "status_counts": dict(sorted(status_counts.items())),
            "active_count": len(active),
            "duplicate_active_labels": sorted(label for label, count in seen.items() if count > 1),
            "fields": field_names,
            "labels": labels,
        }
    checksum = hashlib.sha256(json.dumps(
        {dim: [(item["record_id"], item["label"], item["status"]) for item in meta["labels"]] for dim, meta in dimensions.items()},
        ensure_ascii=False, sort_keys=True,
    ).encode("utf-8")).hexdigest()
    snapshot = {
        "id": f"junior-chem-label-library-live-{checksum[:12]}",
        "parent_snapshot_id": reference["id"],
        "taxonomy_snapshot_id": reference["taxonomy_snapshot_id"],
        "subject": reference["subject"],
        "status": "synced_local_readonly",
        "sync_version": SYNC_VERSION,
        "source": {
            **{key: value for key, value in reference["source"].items() if key != "tables"},
            "base_name": (fetch_report.get("base") or {}).get("name"),
            "base_revision": (fetch_report.get("base") or {}).get("revision"),
            "tables": {dim: {"table_id": meta["table_id"], "revision": meta["live_revision"] or meta["reference_revision"], "record_count": meta["total"]} for dim, meta in dimensions.items()},
            "fetched_at": fetch_report.get("fetched_at"),
            "identity": "user (read-only)",
        },
        "prompt_versions": reference["prompt_versions"],
        "dimensions": list(TAG_DIMENSIONS),
        "question_types": reference["question_types"],
        "policies": reference["policies"],
        "contract_version": reference["contract_version"],
        "vocabulary": {
            dim: {key: value for key, value in meta.items() if key != "labels"} | {
                "labels": [{k: v for k, v in item.items() if k not in {"notes"}} for item in meta["labels"]],
            }
            for dim, meta in dimensions.items()
        },
        "old_to_new": old_to_new,
        "summary": {
            "total_rows": sum(meta["total"] for meta in dimensions.values()),
            "active_labels": sum(meta["active_count"] for meta in dimensions.values()),
            "deleted_labels": sum(meta["status_counts"].get("已删除", 0) for meta in dimensions.values()),
            "pending_labels": sum(meta["status_counts"].get("待定", 0) for meta in dimensions.values()),
            "old_to_new_count": len(old_to_new),
            "duplicate_active_count": sum(len(meta["duplicate_active_labels"]) for meta in dimensions.values()),
        },
        "checksum": checksum,
        "values_path": str(values_path) if values_path else None,
        "synced_at": now(),
        "evidence_limits": [
            "标签值只保存在使用者本地快照，不进入代码仓库；飞书 Base 仍是唯一真源。",
            "状态来自表内“删除/改”等人工字段：空值视为现行，“删除”视为已删除，“待定”不进入现行词表。",
            "旧→新映射只来自知识点表的“层级3（旧标签）→层级4（新标签）”显式字段，不做模糊推断。",
        ],
    }
    if values_path:
        values_path.parent.mkdir(parents=True, exist_ok=True)
        values_path.write_text(json.dumps({"snapshot_id": snapshot["id"], "dimensions": dimensions}, ensure_ascii=False, indent=1), encoding="utf-8")
    return snapshot


def audit_tag_profiles(assets: list[dict[str, Any]], snapshot: dict[str, Any], existing_queue: list[dict[str, Any]]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Compare every asset tag against the live vocabulary; queue unknowns without dropping them."""
    vocab = snapshot["vocabulary"]
    active = {dim: {item["label"]: item for item in meta["labels"] if item["status"] in ACTIVE_STATUSES} for dim, meta in vocab.items()}
    inactive = {dim: {item["label"]: item for item in meta["labels"] if item["status"] not in ACTIVE_STATUSES} for dim, meta in vocab.items()}
    old_map = mapping_targets(snapshot)
    counts = {dim: {"matched": 0, "deprecated": 0, "unknown": 0} for dim in vocab}
    occurrences: dict[tuple[str, str], dict[str, Any]] = {}
    for asset in assets:
        profile = asset.get("tag_profile") or {}
        per_dimension = {
            "knowledge": (profile.get("knowledge") or {}).get("all", []),
            "solution": profile.get("solution", []), "condition": profile.get("condition", []),
            "question": profile.get("question", []), "context": profile.get("context", []),
            "thinking_method": profile.get("thinking_method", []),
        }
        for dim, values in per_dimension.items():
            for value in values or []:
                value = str(value).strip()
                if not value:
                    continue
                if value in active[dim]:
                    counts[dim]["matched"] += 1
                    continue
                bucket = "deprecated" if value in inactive[dim] or (dim, value) in old_map else "unknown"
                counts[dim][bucket] += 1
                entry = occurrences.setdefault((dim, value), {"occurrence_count": 0, "sample_asset_ids": [], "bucket": bucket})
                entry["occurrence_count"] += 1
                if len(entry["sample_asset_ids"]) < 5:
                    entry["sample_asset_ids"].append(asset.get("id"))
    preserved = {(item["dimension"], item["label"]): item for item in existing_queue}
    queue: list[dict[str, Any]] = []
    for (dim, value), entry in sorted(occurrences.items(), key=lambda pair: (-pair[1]["occurrence_count"], pair[0])):
        previous = preserved.get((dim, value), {})
        suggested = old_map.get((dim, value))
        candidates = suggested if suggested else _suggest(value, list(active[dim]))
        queue.append({
            "id": previous.get("id") or f"unmatched-{hashlib.sha256(f'{dim}|{value}'.encode('utf-8')).hexdigest()[:12]}",
            "dimension": dim, "dimension_label": DIMENSION_LABELS[dim], "label": value,
            "bucket": entry["bucket"], "occurrence_count": entry["occurrence_count"],
            "sample_asset_ids": entry["sample_asset_ids"],
            "suggested_labels": [item for item in candidates if item],
            "suggestion_source": ("显式旧→新映射" if len(suggested) == 1 else "一对多旧→新待判定") if suggested else ("字符重合启发式" if candidates else "无"),
            "status": previous.get("status", "待映射"), "mapped_to": previous.get("mapped_to"),
            "snapshot_id": snapshot["id"], "created_at": previous.get("created_at") or now(), "updated_at": now(),
        })
    seen = {(item["dimension"], item["label"]) for item in queue}
    for previous in existing_queue:
        key = (previous.get("dimension"), previous.get("label"))
        if key in seen or previous.get("status") not in {"已映射", "保留为项目扩展", "忽略"}:
            continue
        # Keep resolved history even after tag profiles were rewritten away from the old label.
        retained = dict(previous)
        retained["occurrence_count"] = previous.get("occurrence_count") or 0
        retained["snapshot_id"] = snapshot["id"]
        retained["updated_at"] = now()
        queue.append(retained)
    queue.sort(key=lambda item: (
        0 if item.get("status") == "待映射" else 1,
        -(item.get("occurrence_count") or 0),
        item.get("dimension") or "",
        item.get("label") or "",
    ))
    total_tags = sum(sum(item.values()) for item in counts.values())
    audit = {
        "snapshot_id": snapshot["id"], "asset_count": len(assets), "tag_count": total_tags,
        "counts": counts,
        "queue_size": sum(1 for item in queue if item.get("status") == "待映射"),
        "matched_rate": round(sum(item["matched"] for item in counts.values()) / total_tags, 4) if total_tags else None,
        "audited_at": now(),
        "conclusion": (
            "现有题目标签基本不在现行标签库词表内：它们是导入清单自带的粗粒度项目标签，应通过待映射队列对齐到标签库，而不是直接当作库内标签使用。"
            if total_tags and sum(item["matched"] for item in counts.values()) / total_tags < 0.2
            else "现有题目标签大部分命中现行标签库；未命中项已进入待映射队列。"
        ),
    }
    return audit, queue


def _normalize_row(dimension: str, row: dict[str, Any]) -> dict[str, Any]:
    levels = [str(row.get(field) or "").strip() for field in LEVEL_FIELDS[dimension]]
    terminal = row.get("末级标签") or []
    label = str(terminal[0]).strip() if isinstance(terminal, list) and terminal else ""
    if not label:
        # Only the terminal-level field may stand in for a missing 末级标签;
        # hierarchy levels (层级1/2, 1级) are blocks, not labels.
        label = str(row.get(TERMINAL_FIELDS[dimension]) or "").strip()
    status_raw = ""
    for field in STATUS_FIELDS:
        value = row.get(field)
        if isinstance(value, list) and value:
            status_raw = str(value[0]).strip()
            break
    status = STATUS_MAP.get(status_raw, f"其他：{status_raw}" if status_raw else "现行")
    if dimension == "knowledge":
        block = [levels[0], levels[1]]
        old_label = levels[2] or None
    else:
        block = [_first(row.get("知识板块1级")), _first(row.get("知识板块2级"))]
        old_label = None
    parent = row.get("父记录")
    parent_id = parent[0].get("id") if isinstance(parent, list) and parent and isinstance(parent[0], dict) else None
    adjustments = row.get("调整记录") if isinstance(row.get("调整记录"), list) else []
    example = row.get("例题截图")
    notes = "；".join(str(row.get(field)).strip() for field in NOTE_FIELDS if row.get(field))
    return {
        "record_id": row.get("record_id"), "label": label, "status": status, "status_raw": status_raw,
        "levels": [value for value in levels if value], "knowledge_block": [value for value in block if value],
        "old_label": old_label, "parent_record_id": parent_id, "adjustments": adjustments,
        "has_example": bool(example), "has_notes": bool(notes), "notes": notes,
    }


def _suggest(value: str, candidates: list[str]) -> list[str]:
    if not value or not candidates:
        return []
    grams = {value[i:i + 2] for i in range(len(value) - 1)} or {value}
    scored = []
    for candidate in candidates:
        if value in candidate or candidate in value:
            scored.append((2.0, candidate))
            continue
        candidate_grams = {candidate[i:i + 2] for i in range(len(candidate) - 1)} or {candidate}
        overlap = len(grams & candidate_grams) / len(grams)
        if overlap >= 0.5:
            scored.append((overlap, candidate))
    return [candidate for _, candidate in sorted(scored, key=lambda pair: (-pair[0], len(pair[1])))[:3]]


def active_labels_by_dimension(snapshot: dict[str, Any]) -> dict[str, set[str]]:
    return {
        dim: {item["label"] for item in meta.get("labels", []) if item.get("status") in ACTIVE_STATUSES}
        for dim, meta in (snapshot.get("vocabulary") or {}).items()
    }


def mapping_targets(snapshot: dict[str, Any]) -> dict[tuple[str, str], list[str]]:
    """Preserve every active destination; a taxonomy split is not a rename."""
    active = active_labels_by_dimension(snapshot)
    targets: dict[tuple[str, str], list[str]] = {}
    for item in snapshot.get("old_to_new", []):
        if item.get("status", "现行") not in ACTIVE_STATUSES or item["new"] not in active.get(item["dimension"], set()):
            continue
        values = targets.setdefault((item["dimension"], item["old"]), [])
        if item["new"] not in values:
            values.append(item["new"])
    return targets


def classify_unmatched_label(item: dict[str, Any], active: set[str]) -> dict[str, Any]:
    """Decide whether a queue item can be auto-mapped, kept as project extension, or needs review.

    Auto-map is intentionally conservative: explicit old→new, exact active match, or unique
    verified one-to-one renames. String containment is only a review suggestion.
    """
    value = str(item.get("label") or "").strip()
    dimension = str(item.get("dimension") or "")
    suggested = [str(label).strip() for label in (item.get("suggested_labels") or []) if str(label).strip()]
    source = item.get("suggestion_source") or "无"
    if not value:
        return {"action": "needs_review", "mapped_to": None, "reason": "空标签"}
    if value in active:
        return {"action": "auto_map", "mapped_to": value, "reason": "已是现行标签"}
    if "/" in value or "／" in value:
        return {"action": "project_extension", "mapped_to": None, "reason": "复合标签保留，不折叠为一个末级"}
    if source == "显式旧→新映射" and len(suggested) == 1 and suggested[0] in active:
        return {"action": "auto_map", "mapped_to": suggested[0], "reason": "显式旧→新映射"}

    unique = _unique_containment_target(value, active)
    if unique:
        return {"action": "needs_review", "mapped_to": unique["label"], "reason": "字符串包含不证明语义等价"}

    if _looks_like_project_chapter(dimension, value, suggested, int(item.get("occurrence_count") or 0)):
        return {"action": "project_extension", "mapped_to": None, "reason": "粗粒度项目章节/高频无唯一现行对应"}

    if suggested and source == "字符重合启发式":
        return {"action": "needs_review", "mapped_to": suggested[0], "reason": "启发式建议待教师确认"}
    return {"action": "needs_review", "mapped_to": None, "reason": "无高把握现行对应"}


def propose_batch_label_alignment(
    queue: list[dict[str, Any]],
    snapshot: dict[str, Any],
    *,
    min_occurrence: int = 1,
) -> dict[str, Any]:
    active = active_labels_by_dimension(snapshot)
    proposals: list[dict[str, Any]] = []
    for item in queue:
        if item.get("status") != "待映射":
            continue
        if int(item.get("occurrence_count") or 0) < min_occurrence:
            continue
        candidate = dict(item)
        targets = mapping_targets(snapshot).get((item.get("dimension"), item.get("label")))
        if targets:
            candidate.update(suggested_labels=targets, suggestion_source="显式旧→新映射" if len(targets) == 1 else "一对多旧→新待判定")
        elif candidate.get("suggestion_source") == "显式旧→新映射":
            candidate["suggestion_source"] = "历史建议待重新核验"
        decision = classify_unmatched_label(candidate, active.get(item.get("dimension") or "", set()))
        proposals.append({
            "id": item["id"],
            "dimension": item["dimension"],
            "dimension_label": item.get("dimension_label") or DIMENSION_LABELS.get(item["dimension"], item["dimension"]),
            "label": item["label"],
            "occurrence_count": item.get("occurrence_count") or 0,
            "suggested_labels": item.get("suggested_labels") or [],
            "suggestion_source": item.get("suggestion_source"),
            **decision,
        })
    proposals.sort(key=lambda row: (-row["occurrence_count"], row["dimension"], row["label"]))
    summary = {
        "auto_map": sum(1 for row in proposals if row["action"] == "auto_map"),
        "project_extension": sum(1 for row in proposals if row["action"] == "project_extension"),
        "needs_review": sum(1 for row in proposals if row["action"] == "needs_review"),
        "proposal_count": len(proposals),
        "auto_occurrence_total": sum(row["occurrence_count"] for row in proposals if row["action"] == "auto_map"),
        "extension_occurrence_total": sum(row["occurrence_count"] for row in proposals if row["action"] == "project_extension"),
    }
    return {
        "version": BATCH_ALIGN_VERSION,
        "snapshot_id": snapshot.get("id"),
        "min_occurrence": min_occurrence,
        "summary": summary,
        "proposals": proposals,
        "policy": (
            "只自动采纳现行全等或当前词表中唯一的一对一旧→新映射；"
            "字符重合启发式永不自动落库；粗粒度章节可批量标为项目扩展。"
        ),
    }


def apply_batch_label_alignment(
    queue: list[dict[str, Any]],
    assets: list[dict[str, Any]],
    snapshot: dict[str, Any],
    *,
    mode: str = "high_confidence_only",
    min_occurrence: int = 1,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Apply conservative batch decisions; optionally rewrite asset tag profiles for 已映射."""
    if mode not in {"high_confidence_only", "high_confidence_and_extensions"}:
        raise ValidationError("mode must be high_confidence_only or high_confidence_and_extensions")
    proposal = propose_batch_label_alignment(queue, snapshot, min_occurrence=min_occurrence)
    active = active_labels_by_dimension(snapshot)
    by_id = {item["id"]: item for item in queue}
    applied: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    mappings: dict[tuple[str, str], str] = {}

    for row in proposal["proposals"]:
        entry = by_id.get(row["id"])
        if entry is None or entry.get("status") != "待映射":
            skipped.append({**row, "skip_reason": "队列项不存在或已处理"})
            continue
        if row["action"] == "auto_map":
            mapped_to = row["mapped_to"]
            if not mapped_to or mapped_to not in active.get(entry["dimension"], set()):
                skipped.append({**row, "skip_reason": "目标不是现行标签"})
                continue
            if not dry_run:
                entry.update({
                    "status": "已映射",
                    "mapped_to": mapped_to,
                    "reason": f"批量对齐·{row['reason']}",
                    "batch_align_version": BATCH_ALIGN_VERSION,
                    "updated_at": now(),
                })
                mappings[(entry["dimension"], entry["label"])] = mapped_to
            applied.append({**row, "decision": "已映射"})
        elif row["action"] == "project_extension" and mode == "high_confidence_and_extensions":
            if not dry_run:
                entry.update({
                    "status": "保留为项目扩展",
                    "mapped_to": None,
                    "reason": f"批量对齐·{row['reason']}",
                    "batch_align_version": BATCH_ALIGN_VERSION,
                    "updated_at": now(),
                })
            applied.append({**row, "decision": "保留为项目扩展"})
        else:
            skipped.append({**row, "skip_reason": "需教师确认" if row["action"] == "needs_review" else "本模式不处理项目扩展"})

    rewrite = {"asset_count": 0, "replacement_count": 0}
    if mappings and not dry_run:
        rewrite = rewrite_tag_profiles_with_mappings(assets, mappings)

    return {
        "version": BATCH_ALIGN_VERSION,
        "mode": mode,
        "dry_run": dry_run,
        "snapshot_id": snapshot.get("id"),
        "proposal_summary": proposal["summary"],
        "applied_count": len(applied),
        "skipped_count": len(skipped),
        "applied": applied,
        "skipped": skipped[:50],
        "rewrite": rewrite,
        "policy": proposal["policy"],
    }


def rewrite_tag_profiles_with_mappings(
    assets: list[dict[str, Any]],
    mappings: dict[tuple[str, str], str],
) -> dict[str, Any]:
    """Replace mapped project labels inside tag profiles; never drop unmapped unknowns."""
    touched_assets = 0
    replacements = 0
    for asset in assets:
        profile = asset.get("tag_profile")
        if not isinstance(profile, dict):
            continue
        changed = False

        def replace_list(dimension: str, values: list[Any] | None) -> list[Any]:
            nonlocal replacements, changed
            result = []
            for value in values or []:
                text = str(value).strip()
                mapped = mappings.get((dimension, text))
                if mapped and mapped != text:
                    result.append(mapped)
                    replacements += 1
                    changed = True
                else:
                    result.append(value)
            # de-dupe while preserving order
            seen = set()
            ordered = []
            for item in result:
                key = str(item)
                if key in seen:
                    continue
                seen.add(key)
                ordered.append(item)
            return ordered

        knowledge = profile.get("knowledge")
        if isinstance(knowledge, dict):
            for key in ("all", "core", "prerequisite", "distractor", "mentioned", "mention_only"):
                if key in knowledge and isinstance(knowledge[key], list):
                    knowledge[key] = replace_list("knowledge", knowledge[key])
        for dimension in ("solution", "condition", "question", "context", "thinking_method"):
            if dimension in profile and isinstance(profile[dimension], list):
                profile[dimension] = replace_list(dimension, profile[dimension])
        units = profile.get("units")
        if isinstance(units, list):
            for unit in units:
                if not isinstance(unit, dict):
                    continue
                for dimension in ("question", "solution", "knowledge"):
                    if dimension == "knowledge" and isinstance(unit.get("knowledge"), dict):
                        for key in ("all", "core", "prerequisite", "distractor", "mentioned", "mention_only"):
                            if key in unit["knowledge"] and isinstance(unit["knowledge"][key], list):
                                unit["knowledge"][key] = replace_list("knowledge", unit["knowledge"][key])
                    elif isinstance(unit.get(dimension), list):
                        unit[dimension] = replace_list(dimension, unit[dimension])
        if changed:
            touched_assets += 1
            asset["tag_profile"] = profile
    return {"asset_count": touched_assets, "replacement_count": replacements}


def _unique_containment_target(value: str, active: set[str]) -> dict[str, str] | None:
    if len(value) < 3:
        return None
    forward = sorted(label for label in active if value != label and value in label)
    reverse = sorted(
        label for label in active
        if value != label and len(label) >= 4 and label in value and len(value) - len(label) <= 6
    )
    if len(forward) == 1:
        return {"label": forward[0], "reason": "唯一正向包含"}
    if not forward and len(reverse) == 1:
        return {"label": reverse[0], "reason": "唯一逆向包含"}
    return None


def _looks_like_project_chapter(
    dimension: str,
    value: str,
    suggested: list[str],
    occurrence_count: int,
) -> bool:
    if "/" in value or "／" in value:
        return True
    # Question/solution/context tags are usually leaf-like tasks; only slash-compounds
    # are auto-marked as project extensions. Coarse chapter buckets live in knowledge.
    if dimension != "knowledge":
        return False
    if occurrence_count >= 20 and not suggested:
        return True
    if occurrence_count >= 40 and len(value) <= 10 and ("与" in value or "和" in value):
        return True
    if occurrence_count >= 80 and len(value) <= 6 and not suggested:
        return True
    return False


def _first(value: Any) -> str:
    if isinstance(value, list) and value:
        return str(value[0]).strip()
    return str(value or "").strip()


def _read_json(path: Path) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None


def _run(command: list[str], env: dict[str, str]) -> dict[str, Any]:
    try:
        completed = subprocess.run(command, env=env, capture_output=True, text=True, timeout=180, check=False)
    except FileNotFoundError as error:
        raise ValidationError("lark-cli 未安装或不在 PATH 中") from error
    except subprocess.TimeoutExpired as error:
        raise ValidationError(f"lark-cli 超时：{' '.join(command[:4])}") from error
    text = completed.stdout.strip() or completed.stderr.strip()
    try:
        payload = json.loads(text[text.index("{"):]) if "{" in text else {}
    except json.JSONDecodeError as error:
        raise ValidationError(f"lark-cli 返回了无法解析的输出：{text[:200]}") from error
    if payload.get("ok") is False:
        message = (payload.get("error") or {}).get("message") or "unknown lark-cli error"
        raise ValidationError(f"lark-cli 调用失败：{message}")
    return payload
