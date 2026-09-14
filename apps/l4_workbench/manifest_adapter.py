"""Preview local structured question manifests without mutating source files."""

from __future__ import annotations

import csv
import json
from datetime import datetime
from pathlib import Path
from typing import Any

from .base_adapter import suggest_mapping
from .domain import ValidationError


MANIFEST_EXTENSIONS = {".json", ".csv"}
IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp"}
MAX_MANIFEST_BYTES = 100 * 1024 * 1024


class LocalManifestAdapter:
    """Read JSON/CSV rows and report whether referenced local images are usable."""

    def preview(self, request: dict[str, Any]) -> dict[str, Any]:
        path = self._validated_path(request.get("path"))
        limit = int(request.get("limit", 5))
        if limit < 1 or limit > 200:
            raise ValidationError("manifest preview limit must be between 1 and 200")
        records = self.load_records(path)
        fields = self._field_names(records)
        mapping = suggest_mapping(fields)
        image_report = self.image_report(records, mapping.get("question_image"), path.parent)
        return {
            "adapter": "local-manifest-readonly-v1",
            "path": str(path),
            "source_name": path.name,
            "fields": [{"name": name, "type": "local_value"} for name in fields],
            "records": [
                {"record_id": self.record_id(row, index), "fields": row}
                for index, row in enumerate(records[:limit], 1)
            ],
            "record_count": len(records),
            "has_more": len(records) > limit,
            "suggested_mapping": mapping,
            "image_report": image_report,
            "fetched_at": datetime.now().isoformat(timespec="seconds"),
        }

    def load_records(self, raw_path: str | Path) -> list[dict[str, Any]]:
        path = self._validated_path(raw_path)
        if path.suffix.lower() == ".csv":
            with path.open(encoding="utf-8-sig", errors="replace", newline="") as stream:
                records = [dict(row) for row in csv.DictReader(stream)]
        else:
            try:
                value = json.loads(path.read_text(encoding="utf-8"))
            except json.JSONDecodeError as error:
                raise ValidationError(f"manifest JSON is invalid: {error}") from error
            if isinstance(value, list):
                records = value
            elif isinstance(value, dict):
                records = next((value[key] for key in ("records", "rows", "data", "items") if isinstance(value.get(key), list)), None)
                if records is None:
                    raise ValidationError("manifest JSON must be a row list or contain records/rows/data/items")
            else:
                raise ValidationError("manifest JSON must contain structured rows")
        if not all(isinstance(item, dict) for item in records):
            raise ValidationError("every manifest row must be an object")
        return records

    def image_paths(self, records: list[dict[str, Any]], image_field: str | None, base_dir: Path) -> list[Path]:
        unique: dict[str, Path] = {}
        if not image_field:
            return []
        for record in records:
            for raw_value in self.path_values(record.get(image_field)):
                path = self.resolve_image_path(raw_value, base_dir)
                if path and path.is_file() and path.suffix.lower() in IMAGE_EXTENSIONS:
                    unique[str(path)] = path
        return list(unique.values())

    def image_report(self, records: list[dict[str, Any]], image_field: str | None, base_dir: Path) -> dict[str, Any]:
        referenced = 0
        existing = 0
        missing = 0
        unique_existing: set[str] = set()
        suspicious: dict[str, dict[str, Any]] = {}
        if image_field:
            for record in records:
                values = self.path_values(record.get(image_field))
                if values:
                    referenced += 1
                row_has_image = False
                for raw_value in values:
                    path = self.resolve_image_path(raw_value, base_dir)
                    if path and path.is_file() and path.suffix.lower() in IMAGE_EXTENSIONS:
                        unique_existing.add(str(path))
                        row_has_image = True
                        inspection = self.inspect_image(path)
                        if inspection.get("suspicious"):
                            suspicious[str(path)] = inspection
                if row_has_image:
                    existing += 1
                elif values:
                    missing += 1
        return {
            "mapped_field": image_field,
            "records_with_image_reference": referenced,
            "records_with_existing_local_image": existing,
            "records_with_missing_local_image": missing,
            "unique_existing_local_images": len(unique_existing),
            "suspicious_local_images": len(suspicious),
            "suspicious_examples": [
                {"name": Path(path).name, **inspection}
                for path, inspection in list(suspicious.items())[:10]
            ],
            "coverage_rate": (existing / referenced) if referenced else None,
        }

    @staticmethod
    def inspect_image(path: Path) -> dict[str, Any]:
        """Read PNG dimensions cheaply; extreme page-like crops need review."""
        width = None
        height = None
        try:
            with path.open("rb") as stream:
                header = stream.read(24)
            if header.startswith(b"\x89PNG\r\n\x1a\n") and len(header) >= 24:
                width = int.from_bytes(header[16:20], "big")
                height = int.from_bytes(header[20:24], "big")
        except OSError:
            pass
        ratio = (height / width) if width and height else None
        return {
            "width": width, "height": height, "height_width_ratio": round(ratio, 2) if ratio else None,
            "suspicious": bool(ratio and ratio > 10),
            "reason": "图片纵横比异常，可能误裁为整份试卷" if ratio and ratio > 10 else None,
        }

    @staticmethod
    def resolve_image_path(raw_value: str, base_dir: Path) -> Path | None:
        value = str(raw_value).strip()
        if not value or value.startswith(("http://", "https://")):
            return None
        path = Path(value).expanduser()
        return (path if path.is_absolute() else base_dir / path).resolve()

    @staticmethod
    def record_id(record: dict[str, Any], index: int) -> str:
        for field in ("question_id", "题目ID", "id", "ID"):
            value = record.get(field)
            if value not in (None, ""):
                return str(value)
        return f"row-{index}"

    @staticmethod
    def path_values(value: Any) -> list[str]:
        if value in (None, "", []):
            return []
        if isinstance(value, str):
            return [value]
        if isinstance(value, list):
            result = []
            for item in value:
                if isinstance(item, str):
                    result.append(item)
                elif isinstance(item, dict):
                    candidate = item.get("path") or item.get("url") or item.get("name")
                    if candidate:
                        result.append(str(candidate))
            return result
        if isinstance(value, dict):
            candidate = value.get("path") or value.get("url") or value.get("name")
            return [str(candidate)] if candidate else []
        return [str(value)]

    @staticmethod
    def _field_names(records: list[dict[str, Any]]) -> list[str]:
        names: list[str] = []
        seen = set()
        for record in records[:1000]:
            for name in record:
                if name not in seen:
                    names.append(str(name))
                    seen.add(name)
        return names

    @staticmethod
    def _validated_path(raw_path: Any) -> Path:
        path = Path(str(raw_path or "")).expanduser().resolve()
        if not path.is_file():
            raise ValidationError("manifest path must be an existing file")
        if path.suffix.lower() not in MANIFEST_EXTENSIONS:
            raise ValidationError("local manifest must be JSON or CSV")
        if path.stat().st_size > MAX_MANIFEST_BYTES:
            raise ValidationError("local manifest exceeds the 100 MiB limit")
        return path
