#!/usr/bin/env python3
"""Merge three Feishu Sheet exports into one local video evidence manifest.

The script intentionally consumes read-only JSON exports produced by
``lark-cli sheets +csv-get --output-path``. Raw Feishu rows and the generated
manifest belong under the ignored ``outputs/`` directory and are never
committed to Git.
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import re
import unicodedata
from collections import Counter
from pathlib import Path
from typing import Any, Callable


CATALOGS = {
    "新中考培优": {
        "prefix": "XZK",
        "sheet_id": "L43kZ6",
        "header_row": 2,
        "title": lambda row: row.get("K") or row.get("H") or "",
        "hierarchy": ("A", "B", "C"),
        "backend_id": None,
        "sheet_url": "https://guanghe.feishu.cn/sheets/shtcnbn6dCyvVtQAoaizuI3ip8e?sheet=L43kZ6",
    },
    "重难点培优": {
        "prefix": "ZND",
        "sheet_id": "jG6GT9",
        "header_row": 1,
        "title": lambda row: row.get("E") or "",
        "hierarchy": ("C", "D", "AN", "AO", "AP"),
        "backend_id": "H",
        "sheet_url": "https://guanghe.feishu.cn/sheets/Tx9ps9ENsh9zfIt4UBycXV6snjd?sheet=jG6GT9",
    },
    "教材同步": {
        "prefix": "JCTB",
        "sheet_id": "e1mSBg",
        "header_row": 1,
        "title": lambda row: row.get("H") or row.get("AF") or "",
        "hierarchy": ("C", "D", "E"),
        "backend_id": "L",
        "sheet_url": "https://guanghe.feishu.cn/sheets/shtcnbn6dCyvVtQAoaizuI3ip8e?sheet=e1mSBg",
    },
}


def parse_export(path: Path) -> tuple[dict[int, dict[str, str]], dict[int, str]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    text = payload.get("annotated_csv")
    columns = payload.get("col_indices")
    if not isinstance(text, str) or not isinstance(columns, list):
        raise ValueError(f"{path} is not a lark-cli csv-get data payload")

    records: dict[int, dict[str, str]] = {}
    for chunk in re.split(r"(?m)(?=^\[row=\d+\]\s*)", text):
        match = re.match(r"^\[row=(\d+)\]\s*", chunk)
        if not match:
            continue
        values = next(csv.reader(io.StringIO(chunk[match.end() :])), [])
        records[int(match.group(1))] = {
            str(columns[index]): value.strip()
            for index, value in enumerate(values)
            if index < len(columns) and value.strip()
        }
    return records, {index: str(column) for index, column in enumerate(columns)}


def build_catalog_records(
    catalog: str,
    export_path: Path,
    enriched_lookup: dict[tuple[Any, ...], dict[str, Any]],
) -> list[dict[str, Any]]:
    config = CATALOGS[catalog]
    rows, _ = parse_export(export_path)
    header = rows.get(int(config["header_row"]), {})
    screenshot_columns = [column for column, label in header.items() if label == "视频截图"]
    fill_columns = tuple(config["hierarchy"])
    inherited: dict[str, str] = {}
    records = []
    title_getter: Callable[[dict[str, str]], str] = config["title"]

    for row_number in sorted(rows):
        if row_number <= int(config["header_row"]):
            continue
        row = rows[row_number]
        for column in fill_columns:
            if row.get(column):
                inherited[column] = row[column]
        title = title_getter(row).strip()
        if not title:
            continue

        listing_id = f"{config['prefix']}-{row_number}"
        existing = enriched_lookup.get((catalog, "title", normalize_title(title)), {})
        if not existing:
            row_candidate = enriched_lookup.get((catalog, "row", row_number), {})
            if normalize_title(str(row_candidate.get("video_name") or "")) == normalize_title(title):
                existing = row_candidate
        hierarchy = " - ".join(inherited.get(column, "") for column in fill_columns if inherited.get(column))
        screenshot_refs = "；".join(
            f"{column}:{row[column]}" for column in screenshot_columns if row.get(column)
        )
        selected_fields = [
            hierarchy, title, row.get("I", ""), row.get("J", ""), row.get("K", ""),
            row.get("L", ""), row.get("U", ""), row.get("AR", ""), row.get("BV", ""),
            row.get("BW", ""), screenshot_refs,
        ]
        record = dict(existing)
        record.update({
            "video_id": listing_id,
            "source": catalog,
            "catalog": catalog,
            "sheet_id": config["sheet_id"],
            "source_row": row_number,
            "sheet_url": config["sheet_url"],
            "video_name": title,
            "hierarchy": hierarchy,
            "backend_video_id": row.get(config["backend_id"], "") if config["backend_id"] else "",
            "explicit_reuse_source": row.get("F", "") if catalog == "重难点培优" else row.get("AQ", ""),
            "catalog_status": row.get("G") or row.get("AP") or row.get("M") or "",
            "screenshot_refs": existing.get("screenshot_refs") or screenshot_refs,
            "content_summary": existing.get("content_summary") or " ".join(value for value in selected_fields if value),
        })
        records.append(record)
    return records


def normalize_title(value: str) -> str:
    text = unicodedata.normalize("NFKC", value or "").lower().replace("图象", "图像")
    text = re.sub(r"(?:^|\n)[0-9a-f]{24,36}(?:$|\n)", "", text)
    return re.sub(r"[\s·—_\-:：,，。()（）【】\[\]]+", "", text)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--textbook-sync", type=Path, required=True)
    parser.add_argument("--advanced", type=Path, required=True)
    parser.add_argument("--zhongkao-review", type=Path, required=True)
    parser.add_argument("--enriched-manifest", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    enriched: list[dict[str, Any]] = []
    if args.enriched_manifest:
        enriched = json.loads(args.enriched_manifest.read_text(encoding="utf-8"))
        if not isinstance(enriched, list):
            raise ValueError("enriched manifest must be a JSON row list")
    enriched_lookup: dict[tuple[Any, ...], dict[str, Any]] = {}
    for row in enriched:
        source = str(row.get("source") or "")
        if not source:
            continue
        if row.get("source_row"):
            enriched_lookup[(source, "row", int(row["source_row"]))] = row
        title_key = normalize_title(str(row.get("video_name") or ""))
        if title_key:
            enriched_lookup[(source, "title", title_key)] = row

    records = []
    records.extend(build_catalog_records("教材同步", args.textbook_sync, enriched_lookup))
    records.extend(build_catalog_records("重难点培优", args.advanced, enriched_lookup))
    records.extend(build_catalog_records("新中考培优", args.zhongkao_review, enriched_lookup))
    records.extend(row for row in enriched if str(row.get("source") or "") not in CATALOGS)

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(records, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    counts = Counter(str(row.get("source") or "未标注课库") for row in records)
    print(json.dumps({"output": str(args.output.resolve()), "listing_count": len(records), "catalog_counts": counts}, ensure_ascii=False, default=dict))


if __name__ == "__main__":
    main()
