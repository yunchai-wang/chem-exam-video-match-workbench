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
    old_map = {(item["dimension"], item["old"]): item["new"] for item in snapshot.get("old_to_new", [])}
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
        candidates = [suggested] if suggested else _suggest(value, list(active[dim]))
        queue.append({
            "id": previous.get("id") or f"unmatched-{hashlib.sha256(f'{dim}|{value}'.encode('utf-8')).hexdigest()[:12]}",
            "dimension": dim, "dimension_label": DIMENSION_LABELS[dim], "label": value,
            "bucket": entry["bucket"], "occurrence_count": entry["occurrence_count"],
            "sample_asset_ids": entry["sample_asset_ids"],
            "suggested_labels": [item for item in candidates if item][:3],
            "suggestion_source": "显式旧→新映射" if suggested else ("字符重合启发式" if candidates else "无"),
            "status": previous.get("status", "待映射"), "mapped_to": previous.get("mapped_to"),
            "snapshot_id": snapshot["id"], "created_at": previous.get("created_at") or now(), "updated_at": now(),
        })
    total_tags = sum(sum(item.values()) for item in counts.values())
    audit = {
        "snapshot_id": snapshot["id"], "asset_count": len(assets), "tag_count": total_tags,
        "counts": counts, "queue_size": len(queue),
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
