"""Local, content-addressed copies of registered production outputs."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

from .domain import ValidationError


def snapshot_output(source: Path, root: Path) -> dict[str, Any]:
    if not source.is_file():
        return {}
    data = source.read_bytes()
    checksum = hashlib.sha256(data).hexdigest()
    directory = root.resolve() / "artifact_outputs"
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / f"{checksum}{source.suffix.lower()}"
    # Exclusive creation makes retries idempotent without overwriting history.
    try:
        with target.open("xb") as handle:
            handle.write(data)
    except FileExistsError:
        if target.read_bytes() != data:
            raise ValidationError("产出存档校验不一致，请重新登记")
    return {"stored_path": str(target), "sha256": checksum, "byte_count": len(data)}


def verified_output_path(output: dict[str, Any], root: Path) -> Path:
    stored = output.get("stored_path")
    if not stored:
        raise ValidationError("该文件尚未存档，请重新登记产出")
    target = Path(stored).resolve()
    if not target.is_relative_to((root / "artifact_outputs").resolve()) or not target.is_file():
        raise ValidationError("产出存档不可读")
    if hashlib.sha256(target.read_bytes()).hexdigest() != output.get("sha256"):
        raise ValidationError("产出存档已变化，不能下载或确认该版本")
    if target.stat().st_size == 0:
        raise ValidationError("产出文件为空，不能下载或确认")
    return target
