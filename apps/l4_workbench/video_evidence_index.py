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


SYNC_VERSION = "video-evidence-index-v0.3"
INDEXED_DINGGAO = "索引定稿-待打开核验"
INDEXED_RECORDING = "索引录音稿-待打开核验"
INDEXED_TRANSCRIPT_STATES = {INDEXED_DINGGAO, INDEXED_RECORDING}

DOCX_TOKEN = re.compile(r"/docx/([A-Za-z0-9]+)")
SOURCE_TAG = re.compile(r"<source\b([^>]*)/?>", re.I)
CITE_TAG = re.compile(r"<cite\b([^>]*)/?>", re.I)
XML_ATTR = re.compile(r'([\w-]+)="([^"]*)"')
SEGMENT_ROW = re.compile(
    r"<tr>\s*<td><p>(\d{1,2}\s*[:：]\s*\d{2}[^<]*)</p></td>\s*<td><p>([^<]*)</p></td>\s*</tr>",
    re.I,
)
DOC_SUFFIXES = (".docx", ".doc", ".pdf", ".txt")
PPT_SUFFIXES = (".pptx", ".ppt")
MEDIA_SUFFIXES = (".mp3", ".mp4", ".wav", ".m4a", ".mov")

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
        "subject": "初中化学",
        "url": "https://guanghe.feishu.cn/wiki/IupkwT7XoiIAKyk0bWCc73W6nhe?table=tblycycOv7SGXEiZ",
        "name_fields": ("视频名称", "知识点名称", "课程名称"),
        "link_fields": ("文档集合", "视频名称 / 文档链接", "合集文档"),
        "dinggao_fields": ("定稿文件", "定稿"),
        "recording_fields": ("脚本/录音稿", "录音稿", "定稿（或录音稿）", "脚本（制作PPT或动画前的逐字稿）"),
        "collection_fields": ("文档集合", "视频名称 / 文档链接", "合集文档", "集合文档", "教研素材（文档集合）"),
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
            if table.get('subject') and fields.get('学科') and table['subject'] not in fields['学科']:
                continue
            name = _clean(_first_field(fields, table["name_fields"]))
            video_id = _clean(_first_field(fields, ("视频ID", "VM后台id", "backend_video_id", "知识点ID")))
            if not name and not video_id:
                continue
            entry = upsert(video_id or None, name or None)
            for attachment in [a for field in table['dinggao_fields'] for a in _attachments(fields.get(field))]:
                if not _kind_from_collection_attachment(attachment.get('name', ''), attachment.get('type', '')):
                    continue
                _offer_transcript(entry, {
                    "kind": _kind_from_collection_attachment(attachment.get('name', ''), attachment.get('type', '')),
                    "title": attachment.get("name") or "定稿文件",
                    "url": attachment.get("url") or attachment.get("tmp_url"),
                    "file_token": attachment.get("file_token") or attachment.get("token"),
                    "source_id": table["id"],
                    "source_label": table["label"],
                })
            for attachment in [a for field in table['recording_fields'] for a in _attachments(fields.get(field))]:
                if not _kind_from_collection_attachment(attachment.get('name', ''), attachment.get('type', '')):
                    continue
                _offer_transcript(entry, {
                    "kind": _kind_from_name(attachment.get("name") or "录音稿"),
                    "title": attachment.get("name") or "脚本/录音稿",
                    "url": attachment.get("url") or attachment.get("tmp_url"),
                    "file_token": attachment.get("file_token") or attachment.get("token"),
                    "source_id": table["id"],
                    "source_label": table["label"],
                })
            for link in _links([fields.get(field) for field in dict.fromkeys(table["collection_fields"] + table["link_fields"])]):
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
        "selection_policy": "定稿优先，其次录音稿、普通脚本、合集；同级按明确日期取最新，缺日期或同日冲突标为版本待核验。",
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
            "合集云文档可只读解析其中的定稿/录音稿/逐字稿指针与片段表；PPT 定稿与音视频附件不算逐字稿。",
            "索引命中不等于已完成人工审稿；覆盖诊断仍需打开核对小问与作答边界。",
        ],
        "collection_enrichment": None,
    }


def enrich_collection_documents(
    raw_dir: Path,
    snapshot: dict[str, Any],
    *,
    fetch: bool = True,
    identity: str = "user",
) -> dict[str, Any]:
    """Open collection docx links read-only; lift 定稿/录音稿 pointers and segment tables."""
    collections_dir = raw_dir / "collections"
    collections_dir.mkdir(parents=True, exist_ok=True)
    env = {**os.environ, "LARK_CLI_NO_PROXY": "1"}
    for key in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy"):
        env.pop(key, None)

    url_by_token: dict[str, str] = {}
    for entry in snapshot.get("entries") or []:
        for candidate in entry.get("transcript_candidates") or []:
            if candidate.get("kind") != "合集文档":
                continue
            token = _docx_token(candidate.get("url") or "")
            if token:
                url_by_token[token] = str(candidate["url"]).split("?", 1)[0]

    report: dict[str, Any] = {
        "collection_url_count": len(url_by_token),
        "fetched": 0,
        "cached": 0,
        "failed": 0,
        "failures": [],
        "pointer_count": 0,
        "segment_locator_count": 0,
        "entries_touched": 0,
        "preferred_upgraded_from_collection": 0,
    }
    parsed_by_token: dict[str, dict[str, Any]] = {}
    for token, url in sorted(url_by_token.items()):
        cache = collections_dir / f"{token}.fetch.json"
        payload: dict[str, Any] | None = None
        if cache.is_file() and not fetch:
            payload = json.loads(cache.read_text(encoding="utf-8"))
            report["cached"] += 1
        elif cache.is_file() and fetch:
            # Refresh when fetch=True; keep stale cache on soft-fail.
            try:
                payload = _fetch_collection_doc(url, env, identity=identity)
                cache.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
                report["fetched"] += 1
            except ValidationError as error:
                payload = json.loads(cache.read_text(encoding="utf-8"))
                report["cached"] += 1
                report["failures"].append({"token": token, "url": url, "error": str(error), "used_cache": True})
        else:
            try:
                payload = _fetch_collection_doc(url, env, identity=identity)
                cache.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
                report["fetched"] += 1
            except ValidationError as error:
                report["failed"] += 1
                report["failures"].append({"token": token, "url": url, "error": str(error), "used_cache": False})
                continue
        content = (((payload or {}).get("data") or {}).get("document") or {}).get("content") or ""
        if not content and isinstance(payload, dict):
            content = str(payload.get("content") or "")
        parsed_by_token[token] = parse_collection_document(content)

    for entry in snapshot.get("entries") or []:
        before_kind = (entry.get("preferred_transcript") or {}).get("kind")
        touched = False
        for candidate in list(entry.get("transcript_candidates") or []):
            if candidate.get("kind") != "合集文档":
                continue
            token = _docx_token(candidate.get("url") or "")
            parsed = parsed_by_token.get(token)
            if not parsed:
                continue
            touched = True
            for pointer in parsed.get("transcript_pointers") or []:
                _offer_transcript(entry, {
                    "kind": pointer["kind"],
                    "title": pointer["title"],
                    "url": pointer.get("url"),
                    "file_token": pointer.get("file_token"),
                    "source_id": f"collection:{token}",
                    "source_label": f"合集解析·{candidate.get('title') or token}",
                    "match_status": "from_collection_doc",
                })
                report["pointer_count"] += 1
            locators = parsed.get("segment_locators") or []
            if locators:
                entry["segment_locators"] = list(dict.fromkeys([*(entry.get("segment_locators") or []), *locators]))
                report["segment_locator_count"] += len(locators)
        if touched:
            report["entries_touched"] += 1
            entry["preferred_transcript"] = _prefer_transcript(entry.get("transcript_candidates") or [])
            after_kind = (entry.get("preferred_transcript") or {}).get("kind")
            if before_kind == "合集文档" and after_kind in {"定稿", "录音稿"}:
                report["preferred_upgraded_from_collection"] += 1

    values = snapshot.get("entries") or []
    snapshot["summary"] = {
        "entry_count": len(values),
        "with_preferred_transcript": sum(1 for item in values if item.get("preferred_transcript")),
        "preferred_dinggao": sum(1 for item in values if (item.get("preferred_transcript") or {}).get("kind") == "定稿"),
        "preferred_recording": sum(1 for item in values if (item.get("preferred_transcript") or {}).get("kind") == "录音稿"),
        "preferred_collection": sum(1 for item in values if (item.get("preferred_transcript") or {}).get("kind") == "合集文档"),
        "with_segment_locator": sum(1 for item in values if item.get("segment_locators")),
        "with_screenshot_token": sum(1 for item in values if item.get("screenshot_tokens")),
        "source_sheet_count": snapshot.get("summary", {}).get("source_sheet_count", 0),
        "source_table_count": snapshot.get("summary", {}).get("source_table_count", 0),
        "collection_docs_parsed": report["fetched"] + report["cached"],
        "collection_preferred_upgraded": report["preferred_upgraded_from_collection"],
    }
    checksum = hashlib.sha256(json.dumps([
        (item["key"], (item.get("preferred_transcript") or {}).get("kind"), (item.get("preferred_transcript") or {}).get("title"),
         item.get("segment_locators"), item.get("screenshot_tokens"))
        for item in values
    ], ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()
    snapshot["id"] = f"video-evidence-index-{checksum[:12]}"
    snapshot["checksum"] = checksum
    snapshot["sync_version"] = SYNC_VERSION
    snapshot["selection_policy"] = (
        "定稿优先，其次录音稿、普通脚本、合集；同级按明确日期取最新，缺日期或同日冲突标为版本待核验；"
        "合集文档会只读解析其中的定稿/录音稿/逐字稿指针与片段时间表；不下载正文进仓库。"
    )
    snapshot["collection_enrichment"] = report
    snapshot["synced_at"] = now()
    return report


def parse_collection_document(content: str) -> dict[str, Any]:
    """Extract transcript pointers and segment locators from a collection doc XML body."""
    pointers: list[dict[str, Any]] = []
    seen: set[str] = set()

    def add(pointer: dict[str, Any]) -> None:
        signature = json.dumps(
            {"kind": pointer.get("kind"), "title": pointer.get("title"), "url": pointer.get("url"), "file_token": pointer.get("file_token")},
            ensure_ascii=False, sort_keys=True,
        )
        if signature in seen:
            return
        seen.add(signature)
        pointers.append(pointer)

    for match in SOURCE_TAG.finditer(content or ""):
        attrs = dict(XML_ATTR.findall(match.group(1)))
        name = attrs.get("name") or ""
        kind = _kind_from_collection_attachment(name, attrs.get("mime") or "")
        if not kind:
            continue
        add({
            "kind": kind,
            "title": name,
            "url": None,
            "file_token": attrs.get("token"),
            "origin": "collection_attachment",
        })
    for match in CITE_TAG.finditer(content or ""):
        attrs = dict(XML_ATTR.findall(match.group(1)))
        title = attrs.get("title") or ""
        kind = _kind_from_collection_cite(title)
        if not kind:
            continue
        doc_id = attrs.get("doc-id") or ""
        file_type = attrs.get("file-type") or "docx"
        url = None
        if doc_id:
            url = (
                f"https://guanghe.feishu.cn/wiki/{doc_id}"
                if file_type == "wiki"
                else f"https://guanghe.feishu.cn/docx/{doc_id}"
            )
        add({
            "kind": kind,
            "title": title,
            "url": url,
            "file_token": doc_id or None,
            "origin": "collection_cite",
        })

    locators: list[str] = []
    for match in SEGMENT_ROW.finditer(content or ""):
        locator = re.sub(r"\s+", "", match.group(1)).replace("：", ":")
        label = _clean(match.group(2))
        locators.append(f"{locator} {label}".strip() if label else locator)
    return {"transcript_pointers": pointers, "segment_locators": list(dict.fromkeys(locators))}


def _kind_from_collection_attachment(name: str, mime: str) -> str | None:
    text = str(name or "")
    lower = text.lower()
    mime_l = str(mime or "").lower()
    if lower.endswith(PPT_SUFFIXES) or "presentation" in mime_l or "PPT定稿" in text:
        return None
    if lower.endswith(MEDIA_SUFFIXES) or mime_l.startswith(("audio/", "video/", "image/")):
        return None
    is_doc = lower.endswith(DOC_SUFFIXES) or "wordprocessing" in mime_l or "pdf" in mime_l
    if is_doc and any(word in text for word in ('逐字稿', '定稿', '终稿', '录音稿', '脚本')):
        return _kind_from_name(text)
    return None


def _kind_from_collection_cite(title: str) -> str | None:
    text = str(title or "")
    if any(marker in text for marker in ("PPT", "教案", "反馈", "说课", "大纲")):
        return None
    if any(word in text for word in ('逐字稿', '定稿', '终稿', '录音稿', '脚本')):
        return _kind_from_name(text)
    return None


def _docx_token(url: str) -> str | None:
    match = DOCX_TOKEN.search(str(url or ""))
    return match.group(1) if match else None


def _fetch_collection_doc(url: str, env: dict[str, str], *, identity: str) -> dict[str, Any]:
    return _run([
        "lark-cli", "docs", "+fetch",
        "--doc", url,
        "--doc-format", "xml",
        "--as", identity,
    ], env)


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
        if status in STRONG_TRANSCRIPT_STATES:
            asset["evidence_level"] = "E2"
        elif status in INDEXED_TRANSCRIPT_STATES or status in WEAK_TRANSCRIPT_STATES or asset.get("screenshot_tokens"):
            asset["evidence_level"] = "E1"
        if status in INDEXED_TRANSCRIPT_STATES:
            asset['issue_codes'] = list(dict.fromkeys([*asset.get('issue_codes', []), 'transcript_not_verified']))
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
    order = {"定稿": 0, "录音稿": 1, "其他": 2, "合集文档": 3}
    best_rank = min(order.get(item.get('kind'), 9) for item in candidates)
    pool = [item for item in candidates if order.get(item.get('kind'), 9) == best_rank]
    def version_date(item):
        # Only explicit document dates are comparable; source-row modification
        # time and source traversal order are not transcript version evidence.
        text = str(item.get('version_date') or item.get('title') or '')
        dates = re.findall(r'(?<!\d)(20\d{2})[-年_./]?(\d{2})[-月_./]?(\d{2})(?!\d)', text)
        valid = []
        for year, month, day in dates:
            try:
                valid.append(datetime(int(year), int(month), int(day)).isoformat())
            except ValueError:
                pass
        return max(valid, default='')
    dated = [(version_date(item), item) for item in pool]
    latest = max(date for date, item in dated)
    winners = [item for date, item in dated if date == latest]
    certain = len(pool) == 1 or (all(date for date, item in dated) and len(winners) == 1)
    chosen = sorted(winners, key=lambda item: (item.get('url') or '', item.get('file_token') or '', item.get('title') or ''))[0]
    return {**chosen, 'selection_status': '首选版本已定位' if certain else '版本待核验',
            'selection_reason': '唯一同级候选' if len(pool) == 1 else ('同级稿件按明确年月日选择最新' if certain else '缺少可比较日期或日期相同；保留候选，不宣称最新'),
            'candidate_count': len(pool)}


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
        "version_date": candidate.get("version_date"),
    })


def _kind_from_name(name: str) -> str:
    text = str(name or "")
    if any(word in text for word in ('预定稿', '初稿', '待定稿')):
        return "其他"
    if "定稿" in text or "终稿" in text:
        return "定稿"
    if "录音" in text:
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
