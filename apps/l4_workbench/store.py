"""Small JSON store used by the local-first workbench."""

from __future__ import annotations

import json
import shutil
import tempfile
from pathlib import Path
from typing import Any

from .domain import validate_state


class JsonStore:
    def __init__(self, path: Path, seed_path: Path | None = None) -> None:
        self.path = Path(path)
        self.seed_path = Path(seed_path) if seed_path else None

    def initialize(self) -> dict[str, Any]:
        if not self.path.exists():
            if not self.seed_path or not self.seed_path.exists():
                raise FileNotFoundError("state is missing and no seed file is available")
            self.path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(self.seed_path, self.path)
        return self.load()

    def load(self) -> dict[str, Any]:
        state = json.loads(self.path.read_text(encoding="utf-8"))
        validate_state(state)
        return state

    def save(self, state: dict[str, Any]) -> None:
        validate_state(state)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=self.path.parent,
            prefix=self.path.name + ".",
            suffix=".tmp",
            delete=False,
        ) as handle:
            json.dump(state, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            temporary = Path(handle.name)
        temporary.replace(self.path)
