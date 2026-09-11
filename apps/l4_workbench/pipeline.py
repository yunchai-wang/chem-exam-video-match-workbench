"""Local-first source capture for immutable teaching-material snapshots."""

from __future__ import annotations

import hashlib
import json
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from .domain import ValidationError


ALLOWED_EXTENSIONS = {
    ".pdf", ".doc", ".docx", ".txt", ".md", ".json", ".csv",
    ".xls", ".xlsx", ".png", ".jpg", ".jpeg", ".webp",
}
REMOTE_SOURCE_TYPES = {"feishu_base"}
LOCAL_SOURCE_TYPES = {"local_files", "local_folder", "cb_export"}
SENSITIVE_KEYS = {"token", "password", "secret", "cookie", "authorization", "api_key"}


def timestamp() -> str:
    return datetime.now().isoformat(timespec="seconds")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


class SourceSnapshotManager:
    def __init__(
        self,
        root: Path,
        max_file_bytes: int = 100 * 1024 * 1024,
        max_total_bytes: int = 500 * 1024 * 1024,
        max_files: int = 1000,
    ) -> None:
        self.root = Path(root)
        self.max_file_bytes = max_file_bytes
        self.max_total_bytes = max_total_bytes
        self.max_files = max_files

    def capture(self, request: dict[str, Any]) -> dict[str, Any]:
        source_type = str(request.get("source_type", ""))
        if source_type not in LOCAL_SOURCE_TYPES | REMOTE_SOURCE_TYPES:
            raise ValidationError(f"unsupported source_type: {source_type}")
        self._reject_sensitive_values(request)

        snapshot_id = f"snapshot-{uuid4().hex[:12]}"
        snapshot_dir = self.root / snapshot_id
        raw_dir = snapshot_dir / "raw"
        files: list[dict[str, Any]] = []
        status = "frozen"
        candidates: list[tuple[Path, Path]] = []

        if source_type == "feishu_base":
            url = str(request.get("url", "")).strip()
            if not url.startswith("https://"):
                raise ValidationError("feishu_base requires an https URL")
            status = "pending_adapter"
        else:
            candidates = self._resolve_local_candidates(source_type, request)

        snapshot_dir.mkdir(parents=True, exist_ok=False)
        try:
            if source_type != "feishu_base":
                files = self._copy_candidates(candidates, raw_dir)
                if not files:
                    status = "pending_adapter" if source_type == "cb_export" else "empty"

            config = {
                key: value for key, value in request.items()
                if key not in {"paths", "path"} and key.lower() not in SENSITIVE_KEYS
            }
            manifest = {
                "id": snapshot_id,
                "source_type": source_type,
                "source_label": str(request.get("source_label", source_type)),
                "status": status,
                "created_at": timestamp(),
                "data_cutoff": request.get("data_cutoff"),
                "config": config,
                "files": files,
                "file_count": len(files),
                "total_bytes": sum(item["size_bytes"] for item in files),
            }
            manifest["immutable_checksum"] = hashlib.sha256(
                json.dumps(manifest, ensure_ascii=False, sort_keys=True).encode("utf-8")
            ).hexdigest()
            (snapshot_dir / "manifest.json").write_text(
                json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
            )
            return manifest
        except Exception:
            shutil.rmtree(snapshot_dir, ignore_errors=True)
            raise

    def capture_remote_export(self, request: dict[str, Any], export_payload: dict[str, Any]) -> dict[str, Any]:
        """Freeze the exact read-only Base result used by downstream standardization."""
        self._reject_sensitive_values(request)
        if request.get("source_type") != "feishu_base":
            raise ValidationError("remote export currently supports feishu_base only")
        url = str(request.get("url", "")).strip()
        if not url.startswith("https://"):
            raise ValidationError("feishu_base requires an https URL")
        snapshot_id = f"snapshot-{uuid4().hex[:12]}"
        snapshot_dir = self.root / snapshot_id
        raw_dir = snapshot_dir / "raw"
        snapshot_dir.mkdir(parents=True, exist_ok=False)
        try:
            raw_dir.mkdir(parents=True)
            target = raw_dir / "base-export.json"
            target.write_text(json.dumps(export_payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            file_record = {
                "name": target.name,
                "relative_path": target.relative_to(self.root.parent).as_posix(),
                "size_bytes": target.stat().st_size,
                "sha256": sha256_file(target),
                "extension": ".json",
            }
            config = {
                "source_type": "feishu_base", "source_label": str(request.get("source_label", "Feishu Base")),
                "url": url, "base_token": export_payload.get("base_token"), "table_id": export_payload.get("table_id"),
                "view_id": export_payload.get("view_id"), "record_limit": request.get("limit"),
            }
            manifest = {
                "id": snapshot_id, "source_type": "feishu_base", "source_label": config["source_label"],
                "status": "partial" if export_payload.get("has_more") else "frozen", "created_at": timestamp(),
                "data_cutoff": request.get("data_cutoff"), "config": config, "files": [file_record],
                "file_count": 1, "total_bytes": file_record["size_bytes"],
                "record_count": export_payload.get("record_count", 0), "has_more": bool(export_payload.get("has_more")),
            }
            manifest["immutable_checksum"] = hashlib.sha256(
                json.dumps(manifest, ensure_ascii=False, sort_keys=True).encode("utf-8")
            ).hexdigest()
            (snapshot_dir / "manifest.json").write_text(
                json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
            )
            return manifest
        except Exception:
            shutil.rmtree(snapshot_dir, ignore_errors=True)
            raise

    def _resolve_local_candidates(self, source_type: str, request: dict[str, Any]) -> list[tuple[Path, Path]]:
        if source_type == "local_folder":
            folder = Path(str(request.get("path", ""))).expanduser().resolve()
            if not folder.is_dir():
                raise ValidationError("local_folder path must be an existing directory")
            candidates = []
            for path in sorted(folder.rglob("*")):
                if path.is_symlink() or not path.is_file() or path.name.startswith("."):
                    continue
                if path.suffix.lower() in ALLOWED_EXTENSIONS:
                    candidates.append((path.resolve(), path.relative_to(folder)))
            return candidates

        raw_paths = request.get("paths") or ([] if not request.get("path") else [request["path"]])
        if not isinstance(raw_paths, list):
            raise ValidationError("paths must be a list")
        candidates = []
        for raw_path in raw_paths:
            path = Path(str(raw_path)).expanduser()
            if path.is_symlink():
                raise ValidationError(f"symbolic links are not accepted: {path}")
            path = path.resolve()
            if not path.is_file():
                raise ValidationError(f"source file does not exist: {path}")
            if path.suffix.lower() not in ALLOWED_EXTENSIONS:
                raise ValidationError(f"unsupported teaching-material format: {path.suffix}")
            candidates.append((path, Path(path.name)))
        return candidates

    def _copy_candidates(self, candidates: list[tuple[Path, Path]], raw_dir: Path) -> list[dict[str, Any]]:
        if len(candidates) > self.max_files:
            raise ValidationError(f"too many files: {len(candidates)} > {self.max_files}")
        total = 0
        records = []
        for source, relative in candidates:
            size = source.stat().st_size
            if size > self.max_file_bytes:
                raise ValidationError(f"file exceeds size limit: {source.name}")
            total += size
            if total > self.max_total_bytes:
                raise ValidationError("source snapshot exceeds total size limit")
            safe_parts = [part for part in relative.parts if part not in {"", ".", ".."}]
            safe_relative = Path(*safe_parts) if safe_parts else Path(source.name)
            target = raw_dir / safe_relative
            if target.exists():
                target = target.with_name(f"{source.stem}-{sha256_file(source)[:8]}{source.suffix}")
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
            records.append({
                "name": source.name,
                "relative_path": target.relative_to(self.root.parent).as_posix(),
                "size_bytes": size,
                "sha256": sha256_file(target),
                "extension": source.suffix.lower(),
            })
        return records

    @staticmethod
    def _reject_sensitive_values(request: dict[str, Any]) -> None:
        found = sorted(key for key in request if key.lower() in SENSITIVE_KEYS)
        if found:
            raise ValidationError(f"credentials must use local secure storage, not snapshot fields: {', '.join(found)}")
