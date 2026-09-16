"""Read-only video evidence index: screenshots + transcript pointers from Feishu.

Does not download transcript bodies into the repo. Prefer the latest 定稿, then
录音稿, then a collection-doc link. Joins onto existing video_assets by video_id
or normalised title.
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import os
import re
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Any

from .domain import ValidationError
from .video_evidence import STRONG_TRANSCRIPT_STATES, WEAK_TRANSCRIPT_STATES, normalize_video_title


SYNC_VERSION = "video-evidence-index-v0.1"
INDEXED_DINGGAO = "索引定稿-待打开核验"
INDEXED_RECORDING = "索引录音稿-待打开核验"
INDEXED_TRANSCRIPT_STATES = {INDEXED_DINGGAO, INDEXED_RECORDING}

SCREENSHOT_SHEETS = [
    {
        "id": "sheet-e1mSBg",
        "spreadsheet_token": "shtcnbn6dCyvVtQAoaizuI3ip8e",
        "sheet_id": "e1mSBg",
        "label": "化学章节结构表·预定NaOH探究等",
        "url": "https://guanghe.feishu.cn/sheets/shtcnbn6dCyvVtQAoaizuI3ip8e?sheet=e1mSBg",
    },
    {
        "id": "sheet-L43kZ6",
        "spreadsheet_token": "shtcnbn6dCyvVtQAoaizuI3ip8e",
        "sheet_id": "L43kZ6",
        "label": "化学章节结构表·中考总复习目录",
        "url": "https://guanghe.feishu.cn/sheets/shtcnbn6dCyvVtQAoaizuI3ip8e?sheet=L43kZ6",
    },
    {
        "id": "sheet-jG6GT9",
        "spreadsheet_token": "Tx9ps9ENsh9zfIt4UBycXV6snjd",
        "sheet_id": "jG6GT9",
        "label": "9-新-人教-重难点培优-专题化目录",
        "url": "https://guanghe.feishu.cn/sheets/Tx9ps9ENsh9zfIt4UBycXV6snjd?sheet=jG6GT9",
    },
]

TRANSCRIPT_TABLES = [
    {
        "id": "base-chem-pmo",
        "base_token": "X6dYbmeCQaqQXOs6IyFcMmsSn5e",
        "table_id": "tblj9lbXLbNSQZSn",
        "label": "重难点培优PMO·初中化学",
        "url": "https://guanghe.feishu.cn/base/X6dYbmeCQaqQXOs6IyFcMmsSn5e?table=tblj9lbXLbNSQZSn",
        "name_fields": ("视频名称",),
        "link_fields": ("视频名称 / 文档链接",),
        "dinggao_fields": ("定稿文件",),
        "recording_fields": (),
        "collection_fields": ("视频名称 / 文档链接",),
    },
    {
        "id": "base-new-zk-b",
        "base_token": "bascngzeMOrKJSqqhcSKnNVy5Qd",
        "table_id": "tblgZZeQq4j9ytap",
        "label": "初中化学项目总表·新中考B级课",
        "url": "https://guanghe.feishu.cn/base/bascngzeMOrKJSqqhcSKnNVy5Qd?table=tblgZZeQq4j9ytap",
        "name_fields": ("视频名称", "VM后台视频名称", "成片命名"),
        "link_fields": ("文档集合",),
        "dinggao_fields": (),
        "recording_fields": ("脚本/录音稿",),
        "collection_fields": ("文档集合",),
    },
    {
        "id": "base-hard-b-total",
        "base_token": "bascngzeMOrKJSqqhcSKnNVy5Qd",
        "table_id": "tblHrcHQX3jz0hq3",
        "label": "初中化学项目总表·B级重难点培优课【总】",
        "url": "https://guanghe.feishu.cn/base/bascngzeMOrKJSqqhcSKnNVy5Qd?table=tblHrcHQX3jz0hq3",
        "name_fields": ("视频名称", "VM后台视频名称", "成片命名", "知识点名称"),
        "link_fields": ("文档集合", "合集文档", "视频名称 / 文档链接"),
        "dinggao_fields": ("定稿文件",),
        "recording_fields": ("脚本/录音稿", "录音稿"),
        "collection_fields": ("文档集合", "合集文档", "视频名称 / 文档链接"),
    },
    {
        "id": "wiki-new-textbook-pmo",
        "base_token": "FqH7bLYwQa3Qk4sm84Tc9wrjnyb",
        "table_id": "tblycycOv7SGXEiZ",
        "label": "新教材课程更新PMO（wiki bitable）",
        "url": "https://guanghe.feishu.cn/wiki/IupkwT7XoiIAKyk0bWCc73W6nhe?table=tblycycOv7SGXEiZ",
        "name_fields": ("视频名称", "知识点名称", "课程名称"),
        "link_fields": ("文档集合", "视频名称 / 文档链接", "合集文档"),
        "dinggao_fields": ("定稿文件",),
        "recording_fields": ("脚本/录音稿", "录音稿"),
        "collection_fields": ("文档集合", "视频名称 / 文档链接", "合集文档"),
    },
]

TIMECODE = re.compile(r"\d{1,2}\s*[:：]\s*\d{2}(?:\s*[-–—~～到至]\s*\d{1,2}\s*[:：]\s*\d{2})?")
MARKDOWN_LINK = re.compile(r"\[([^\]]*)\]\((https?://[^)]+)\)")
ZERO_WIDTH = re.compile(r"[\u200b-\u200f\u202a-\u202e\ufeff\u2060\u180e]")


def now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def fetch_video_evidence_sources(raw_dir: Path, *, identity: str = "user") -> dict[str, Any]:
    """Pull screenshot sheets and transcript tables read-only into raw_dir."""
    raw_dir.mkdir(parents=True, exist_ok=True)
    env = {**os.environ, "LARK_CLI_NO_PROXY": "1"}
    for key in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy"):
        env.pop(key, None)
    report: dict[str, Any] = {"fetched_at": now(), "sheets": {}, "tables": {}, "identity": identity}
    for sheet in SCREENSHOT_SHEETS:
        out = raw_dir / f"{sheet['id']}.csv-get.json"
        payload = _run([
            "lark-cli", "sheets", "+csv-get",
            "--spreadsheet-token", sheet["spreadsheet_token"],
            "--sheet-id", sheet["sheet_id"],
            "--output-path", str(out),
            "--as", identity,
        ], env)
        report["sheets"][sheet["id"]] = {
            "label": sheet["label"], "sheet_id": sheet["sheet_id"],
            "revision": (payload.get("data") or payload).get("revision"),
            "path": str(out),
        }
    for table in TRANSCRIPT_TABLES:
        out = raw_dir / f"{table['id']}.records.ndjson"
        try:
            records = _fetch_all_records(table["base_token"], table["table_id"], env, identity=identity)
            out.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in records), encoding="utf-8")
            report["tables"][table["id"]] = {
                "label": table["label"], "base_token": table["base_token"], "table_id": table["table_id"],
                "record_count": len(records), "path": str(out), "status": "ok",
            }
        except ValidationError as error:
            report["tables"][table["id"]] = {
                "label": table["label"], "base_token": table["base_token"], "table_id": table["table_id"],
                "record_count": 0, "path": str(out), "status": "failed", "error": str(error),
            }
            if not out.is_file():
                out.write_text("", encoding="utf-8")

    (raw_dir / "fetch-report.json").write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    return report


def build_video_evidence_index(raw_dir: Path) -> dict[str, Any]:
    """Normalise raw Feishu exports into a local evidence index snapshot."""
    if not raw_dir.is_dir():
        raise ValidationError(f"missing video evidence raw dir: {raw_dir}")
    entries: dict[str, dict[str, Any]] = {}
    by_name: dict[str, str] = {}
    source_counts = {"sheets": 0, "tables": 0}

    def upsert(video_id: str | None, name: str | None) -> dict[str, Any]:
        name_key = normalize_video_title(name) if name else ""
        existing_key = None
        if video_id and f"id:{video_id}" in entries:
            existing_key = f"id:{video_id}"
        elif name_key and name_key in by_name:
            existing_key = by_name[name_key]
        if existing_key is None:
            existing_key = _entry_key(video_id, name)
            entries[existing_key] = _blank_entry(video_id, name)
        entry = entries[existing_key]
        if name:
            variants = _name_variants(name)
            entry["aliases"] = sorted(set(entry["aliases"] + variants))
            entry["primary_name"] = entry["primary_name"] or name
            entry["normalized_name"] = entry["normalized_name"] or normalize_video_title(name)
            for variant in variants:
                by_name[normalize_video_title(variant)] = existing_key
        if video_id:
            entry["video_ids"] = sorted(set(entry["video_ids"] + [video_id]))
            entry["primary_video_id"] = entry["primary_video_id"] or video_id
            # Keep id-keyed alias for later joins.
            entries[f"id:{video_id}"] = entry
        return entry

    for sheet in SCREENSHOT_SHEETS:
        path = raw_dir / f"{sheet['id']}.csv-get.json"
        if not path.is_file():
            continue
        rows = _sheet_rows(path)
        source_counts["sheets"] += 1
        for row in rows:
            name = _first_present(row, (
                "知识点名称（视频名称）", "知识点名称\n（视频名称）", "视频名称（紫色为迭代视频；黄色为新增视频）",
                "视频名称", "原视频名称", "视频改名（参考题型大全F列）", "知识点名称",
            ))
            video_id = _first_present(row, ("视频ID", "VM后台id", "知识点ID"))
            if not name and not video_id:
                continue
            entry = upsert(video_id, name)
            locators = _extract_timecodes(row)
            shots = _screenshot_tokens(row, sheet["id"])
            if locators:
                entry["segment_locators"] = sorted(set(entry["segment_locators"] + locators))
            if shots:
                entry["screenshot_tokens"] = sorted(set(entry["screenshot_tokens"] + shots))
            entry["sources"].append({"kind": "screenshot_sheet", "source_id": sheet["id"], "label": sheet["label"], "url": sheet["url"]})
            ai_file = _first_present(row, ("AI逐字稿文件",))
            ai_status = _first_present(row, ("AI逐字稿匹配状态",))
            if ai_file:
                _offer_transcript(entry, {
                    "kind": _kind_from_name(ai_file),
                    "title": ai_file,
                    "url": None,
                    "source_id": sheet["id"],
                    "source_label": sheet["label"],
                    "match_status": ai_status or None,
                })

    for table in TRANSCRIPT_TABLES:
        path = raw_dir / f"{table['id']}.records.ndjson"
        if not path.is_file():
            continue
        source_counts["tables"] += 1
        for raw in _read_ndjson(path):
            fields = raw.get("fields") or raw
            name = _clean(_first_field(fields, table["name_fields"]))
            video_id = _clean(_first_field(fields, ("视频ID", "VM后台id", "backend_video_id", "知识点ID")))
            if not name and not video_id:
                continue
            entry = upsert(video_id or None, name or None)
            for attachment in _attachments(_first_field(fields, table["dinggao_fields"])):
                _offer_transcript(entry, {
                    "kind": "定稿",
                    "title": attachment.get("name") or "定稿文件",
                    "url": attachment.get("url") or attachment.get("tmp_url"),
                    "file_token": attachment.get("file_token") or attachment.get("token"),
                    "source_id": table["id"],
                    "source_label": table["label"],
                })
            for attachment in _attachments(_first_field(fields, table["recording_fields"])):
                _offer_transcript(entry, {
                    "kind": _kind_from_name(attachment.get("name") or "录音稿"),
                    "title": attachment.get("name") or "脚本/录音稿",
                    "url": attachment.get("url") or attachment.get("tmp_url"),
                    "file_token": attachment.get("file_token") or attachment.get("token"),
                    "source_id": table["id"],
                    "source_label": table["label"],
                })
            for link in _links(_first_field(fields, table["collection_fields"] + table["link_fields"])):
                _offer_transcript(entry, {
                    "kind": "合集文档",
                    "title": link["title"],
                    "url": link["url"],
                    "source_id": table["id"],
                    "source_label": table["label"],
                })
            entry["sources"].append({"kind": "transcript_table", "source_id": table["id"], "label": table["label"], "url": table["url"]})

    # Deduplicate object identity aliases created by id: keys.
    unique_entries: dict[int, dict[str, Any]] = {}
    for entry in entries.values():
        unique_entries[id(entry)] = entry
    for entry in unique_entries.values():
        entry["preferred_transcript"] = _prefer_transcript(entry["transcript_candidates"])
        entry["sources"] = _dedupe_sources(entry["sources"])
        entry["key"] = _entry_key(entry.get("primary_video_id"), entry.get("primary_name"))

    values = sorted(unique_entries.values(), key=lambda item: (item["normalized_name"], item["primary_video_id"] or ""))
    checksum = hashlib.sha256(json.dumps([
        (item["key"], (item.get("preferred_transcript") or {}).get("kind"), (item.get("preferred_transcript") or {}).get("title"),
         item.get("segment_locators"), item.get("screenshot_tokens"))
        for item in values
    ], ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()
    summary = {
        "entry_count": len(values),
        "with_preferred_transcript": sum(1 for item in values if item.get("preferred_transcript")),
        "preferred_dinggao": sum(1 for item in values if (item.get("preferred_transcript") or {}).get("kind") == "定稿"),
        "preferred_recording": sum(1 for item in values if (item.get("preferred_transcript") or {}).get("kind") == "录音稿"),
        "preferred_collection": sum(1 for item in values if (item.get("preferred_transcript") or {}).get("kind") == "合集文档"),
        "with_segment_locator": sum(1 for item in values if item.get("segment_locators")),
        "with_screenshot_token": sum(1 for item in values if item.get("screenshot_tokens")),
        "source_sheet_count": source_counts["sheets"],
        "source_table_count": source_counts["tables"],
    }
    return {
        "id": f"video-evidence-index-{checksum[:12]}",
        "sync_version": SYNC_VERSION,
        "status": "synced_local_readonly",
        "selection_policy": "同一视频多份逐字稿时优先最后一份定稿，其次录音稿，再次合集云文档链接；不下载正文进仓库。",
        "sources": {
            "sheets": SCREENSHOT_SHEETS,
            "tables": [
                {
                    "id": item["id"], "base_token": item["base_token"], "table_id": item["table_id"],
                    "label": item["label"], "url": item["url"],
                }
                for item in TRANSCRIPT_TABLES
            ],
        },
        "summary": summary,
        "entries": values,
        "checksum": checksum,
        "synced_at": now(),
        "evidence_limits": [
            "索引只保存链接、附件名与截图/时间码定位，不把逐字稿正文写入代码仓库。",
            "合集云文档内的附件需打开文档后再核验；索引命中不等于已完成人工审稿。",
            "覆盖诊断可将索引定稿/录音稿视为可核验指针，但仍需打开核对小问与作答边界。",
        ],
    }


def apply_evidence_index_to_videos(video_assets: list[dict[str, Any]], snapshot: dict[str, Any]) -> dict[str, Any]:
    """Join index entries onto video assets; upgrade unmatched transcript status when 定稿/录音稿 exists."""
    by_id: dict[str, dict[str, Any]] = {}
    by_name: dict[str, dict[str, Any]] = {}
    for entry in snapshot.get("entries") or []:
        for video_id in entry.get("video_ids") or []:
            by_id[str(video_id)] = entry
        if entry.get("primary_video_id"):
            by_id[str(entry["primary_video_id"])] = entry
        for alias in [entry.get("primary_name"), *entry.get("aliases", [])]:
            if alias:
                by_name[normalize_video_title(str(alias))] = entry

    matched = dinggao = recording = collection = locator = screenshot = 0
    for asset in video_assets:
        entry = None
        for candidate_id in [asset.get("video_id"), *(asset.get("alias_video_ids") or [])]:
            if candidate_id and str(candidate_id) in by_id:
                entry = by_id[str(candidate_id)]
                break
        if entry is None:
            entry = _lookup_name(by_name, str(asset.get("video_name") or ""))
        if entry is None:
            continue
        matched += 1
        preferred = entry.get("preferred_transcript")
        asset["evidence_index"] = {
            "snapshot_id": snapshot["id"],
            "entry_key": entry["key"],
            "preferred_transcript": preferred,
            "transcript_candidates": entry.get("transcript_candidates") or [],
            "segment_locators": entry.get("segment_locators") or [],
            "screenshot_tokens": entry.get("screenshot_tokens") or [],
            "sources": entry.get("sources") or [],
            "selection_policy": snapshot.get("selection_policy"),
        }
        if entry.get("segment_locators") and not asset.get("segment_locator"):
            asset["segment_locator"] = entry["segment_locators"][0]
            locator += 1
        if entry.get("screenshot_tokens"):
            asset["screenshot_tokens"] = sorted(set(list(asset.get("screenshot_tokens") or []) + entry["screenshot_tokens"]))
            screenshot += 1
            asset["issue_codes"] = [code for code in asset.get("issue_codes") or [] if code != "video_screenshot_not_materialized"]
        if preferred:
            pointer = preferred.get("title") or preferred.get("url") or ""
            prefix = f"[{preferred.get('kind')}] {pointer}".strip()
            if not asset.get("transcript_evidence"):
                asset["transcript_evidence"] = prefix
            if preferred.get("kind") == "定稿":
                dinggao += 1
                if asset.get("transcript_status") in {"未匹配", "None", "", None}:
                    asset["transcript_status"] = INDEXED_DINGGAO
                    asset["issue_codes"] = [code for code in asset.get("issue_codes") or [] if code != "transcript_not_verified"]
            elif preferred.get("kind") == "录音稿":
                recording += 1
                if asset.get("transcript_status") in {"未匹配", "None", "", None}:
                    asset["transcript_status"] = INDEXED_RECORDING
                    asset["issue_codes"] = [code for code in asset.get("issue_codes") or [] if code != "transcript_not_verified"]
            elif preferred.get("kind") == "合集文档":
                collection += 1
        status = asset.get("transcript_status")
        if status in STRONG_TRANSCRIPT_STATES or status in INDEXED_TRANSCRIPT_STATES:
            asset["evidence_level"] = "E2"
        elif status in WEAK_TRANSCRIPT_STATES or asset.get("screenshot_tokens"):
            asset["evidence_level"] = "E1"
    return {
        "matched_assets": matched,
        "dinggao_preferred": dinggao,
        "recording_preferred": recording,
        "collection_preferred": collection,
        "segment_locator_filled": locator,
        "screenshot_token_filled": screenshot,
    }


def _name_variants(name: str) -> list[str]:
    text = _clean(name)
    variants = [text] if text else []
    stripped = re.sub(r"^【[^】]+】", "", text).strip()
    stripped = re.sub(r"^(?:重难点培优|新中考|教材同步)[-–—]?", "", stripped).strip()
    stripped = re.sub(r"^\d+\s*号\s*[-–—+]?", "", stripped).strip()
    stripped = re.sub(r"^第?\d+\s*单元\s*[-–—+]?", "", stripped).strip()
    if stripped and stripped not in variants:
        variants.append(stripped)
    # drop trailing difficulty / editor marks
    simplified = re.sub(r"[【\[][^】\]]+[】\]].*$", "", stripped).strip(" -–—+")
    if simplified and simplified not in variants:
        variants.append(simplified)
    return variants


def _lookup_name(by_name: dict[str, dict[str, Any]], video_name: str) -> dict[str, Any] | None:
    key = normalize_video_title(video_name)
    if not key:
        return None
    if key in by_name:
        return by_name[key]
    for name_key, entry in by_name.items():
        if len(name_key) < 6:
            continue
        if name_key in key or key in name_key:
            return entry
    return None


def _blank_entry(video_id: str | None, name: str | None) -> dict[str, Any]:
    return {
        "key": _entry_key(video_id, name),
        "primary_video_id": video_id or None,
        "primary_name": name or None,
        "normalized_name": normalize_video_title(name or video_id or ""),
        "video_ids": [video_id] if video_id else [],
        "aliases": [name] if name else [],
        "transcript_candidates": [],
        "preferred_transcript": None,
        "segment_locators": [],
        "screenshot_tokens": [],
        "sources": [],
    }


def _entry_key(video_id: str | None, name: str | None) -> str:
    if video_id:
        return f"id:{video_id}"
    return f"name:{normalize_video_title(name or '')}"


def _prefer_transcript(candidates: list[dict[str, Any]]) -> dict[str, Any] | None:
    if not candidates:
        return None
    order = {"定稿": 0, "录音稿": 1, "合集文档": 2, "其他": 3}
    ranked = sorted(
        enumerate(candidates),
        key=lambda pair: (order.get(pair[1].get("kind"), 9), -pair[0]),
    )
    return ranked[0][1]


def _offer_transcript(entry: dict[str, Any], candidate: dict[str, Any]) -> None:
    title = _clean(candidate.get("title"))
    url = candidate.get("url")
    if not title and not url:
        return
    signature = json.dumps({"kind": candidate.get("kind"), "title": title, "url": url}, ensure_ascii=False, sort_keys=True)
    existing = {
        json.dumps({"kind": item.get("kind"), "title": item.get("title"), "url": item.get("url")}, ensure_ascii=False, sort_keys=True)
        for item in entry["transcript_candidates"]
    }
    if signature in existing:
        return
    entry["transcript_candidates"].append({
        "kind": candidate.get("kind") or "其他",
        "title": title,
        "url": url,
        "file_token": candidate.get("file_token"),
        "source_id": candidate.get("source_id"),
        "source_label": candidate.get("source_label"),
        "match_status": candidate.get("match_status"),
    })


def _kind_from_name(name: str) -> str:
    text = str(name or "")
    if "定稿" in text:
        return "定稿"
    if "录音" in text or "脚本" in text:
        return "录音稿"
    return "其他"


def _sheet_rows(path: Path) -> list[dict[str, str]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    data = payload.get("data") or payload
    # output-path may wrap again
    if isinstance(data, dict) and "annotated_csv" not in data and "data" in data:
        data = data["data"]
    csv_text = data.get("annotated_csv") or ""
    if not csv_text and path.with_suffix(".csv").is_file():
        csv_text = path.with_suffix(".csv").read_text(encoding="utf-8")
    lines = []
    for line in csv_text.splitlines():
        line = re.sub(r"^\[row=\d+\]\s*", "", line)
        if line.strip():
            lines.append(line)
    if not lines:
        return []
    reader = csv.DictReader(io.StringIO("\n".join(lines)))
    rows = []
    for row in reader:
        cleaned = {_clean_header(key): ("" if value is None else str(value).strip()) for key, value in row.items() if key}
        rows.append(cleaned)
    return rows


def _clean_header(value: str) -> str:
    return ZERO_WIDTH.sub("", str(value or "")).replace("\r", "").strip()


def _first_present(row: dict[str, str], keys: tuple[str, ...]) -> str | None:
    for key in keys:
        for actual, value in row.items():
            if _clean_header(actual) == _clean_header(key) or key in actual:
                text = _clean(value)
                if text:
                    return text
    return None


def _extract_timecodes(row: dict[str, str]) -> list[str]:
    found: list[str] = []
    for key, value in row.items():
        if "截图" in key or "时间" in key or "片段" in key:
            for match in TIMECODE.finditer(value or ""):
                found.append(re.sub(r"\s+", "", match.group(0)).replace("：", ":"))
        else:
            for match in TIMECODE.finditer(value or ""):
                # only keep bare timecodes from non-screenshot columns when short
                token = re.sub(r"\s+", "", match.group(0)).replace("：", ":")
                if len(value or "") <= 24:
                    found.append(token)
    return list(dict.fromkeys(found))


def _screenshot_tokens(row: dict[str, str], source_id: str) -> list[str]:
    tokens: list[str] = []
    for key, value in row.items():
        if "截图" not in key:
            continue
        text = _clean(value)
        if not text:
            continue
        if TIMECODE.search(text):
            tokens.append(f"{source_id}:{text}")
        elif text not in {"", "-", "/"}:
            tokens.append(f"{source_id}:{text[:40]}")
    return tokens


def _first_field(fields: dict[str, Any], keys: tuple[str, ...]) -> Any:
    for key in keys:
        if key in fields and fields[key] not in (None, "", [], {}):
            return fields[key]
    return None


def _attachments(value: Any) -> list[dict[str, Any]]:
    if not value:
        return []
    if isinstance(value, list):
        return [item for item in value if isinstance(item, dict)]
    if isinstance(value, dict):
        return [value]
    return []


def _links(value: Any) -> list[dict[str, str]]:
    results: list[dict[str, str]] = []
    if value in (None, "", [], {}):
        return results
    if isinstance(value, list):
        for item in value:
            results.extend(_links(item))
        return results
    if isinstance(value, dict):
        url = value.get("link") or value.get("url") or value.get("text")
        title = value.get("text") or value.get("name") or url
        if url and str(url).startswith("http"):
            results.append({"title": _clean(title) or url, "url": str(url)})
        return results
    text = str(value)
    for match in MARKDOWN_LINK.finditer(text):
        results.append({"title": _clean(match.group(1)) or match.group(2), "url": match.group(2)})
    if not results:
        for token in re.findall(r"https://guanghe\.feishu\.cn/[^\s)]+", text):
            results.append({"title": token, "url": token})
    return results


def _clean(value: Any) -> str:
    return ZERO_WIDTH.sub("", str(value or "")).strip()


def _dedupe_sources(sources: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen = set()
    result = []
    for item in sources:
        key = (item.get("kind"), item.get("source_id"))
        if key in seen:
            continue
        seen.add(key)
        result.append(item)
    return result


def _read_ndjson(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _fetch_all_records(base_token: str, table_id: str, env: dict[str, str], *, identity: str) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    offset = 0
    limit = 2000
    while True:
        target = Path(f"/tmp/l4_ev_page_{table_id}_{offset}.ndjson")
        listing = _run([
            "lark-cli", "base", "+record-list",
            "--base-token", base_token, "--table-id", table_id,
            "--format", "ndjson", "--output", str(target),
            "--limit", str(limit), "--offset", str(offset),
            "--overwrite", "--as", identity,
        ], env)
        chunk = _read_ndjson(target) if target.is_file() else []
        records.extend(chunk)
        data = listing.get("data") or listing
        has_more = bool(data.get("has_more"))
        if not chunk or not has_more:
            break
        offset += len(chunk)
        if offset > 20000:
            raise ValidationError(f"table {table_id} pagination exceeded safety cap")
    return records


def _run(command: list[str], env: dict[str, str]) -> dict[str, Any]:
    result = subprocess.run(command, capture_output=True, text=True, env=env)
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "").strip()
        raise ValidationError(f"lark-cli failed ({' '.join(command[:6])}…): {detail[:500]}")
    text = (result.stdout or "").strip()
    if not text:
        return {}
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return {"raw": text}
