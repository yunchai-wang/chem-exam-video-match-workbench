"""Small JSON store used by the local-first workbench."""

from __future__ import annotations

import json
import shutil
import tempfile
import threading
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Iterator

from .domain import migrate_state, validate_state

try:
    import fcntl
except ImportError:  # pragma: no cover - Windows falls back to the process lock.
    fcntl = None


_LOCKS: dict[str, threading.RLock] = {}
_LOCKS_GUARD = threading.Lock()


class ConcurrentUpdateError(RuntimeError):
    """Raised when saving a stale state revision."""


def _shared_lock(path: Path) -> threading.RLock:
    key = str(path.resolve())
    with _LOCKS_GUARD:
        return _LOCKS.setdefault(key, threading.RLock())


class JsonStore:
    def __init__(self, path: Path, seed_path: Path | None = None, backup_limit: int = 20) -> None:
        self.path = Path(path)
        self.seed_path = Path(seed_path) if seed_path else None
        self.backup_limit = backup_limit
        self._thread_lock = _shared_lock(self.path)
        self.lock_path = self.path.with_suffix(self.path.suffix + ".lock")
        self.backup_dir = self.path.parent / ".backups"

    def initialize(self) -> dict[str, Any]:
        with self._exclusive_lock():
            if not self.path.exists():
                if not self.seed_path or not self.seed_path.exists():
                    raise FileNotFoundError("state is missing and no seed file is available")
                self.path.parent.mkdir(parents=True, exist_ok=True)
                state = migrate_state(json.loads(self.seed_path.read_text(encoding="utf-8")))
                self._save_unlocked(state, backup=False)
        return self.load()

    def load(self) -> dict[str, Any]:
        with self._exclusive_lock():
            state = self._load_unlocked()
        return state

    def save(self, state: dict[str, Any]) -> None:
        expected_revision = int(state.get("metadata", {}).get("state_revision", 0))
        with self._exclusive_lock():
            current = self._load_unlocked() if self.path.exists() else None
            current_revision = int(current.get("metadata", {}).get("state_revision", 0)) if current else 0
            if current and expected_revision != current_revision:
                raise ConcurrentUpdateError(f"stale state revision {expected_revision}; current is {current_revision}")
            state["metadata"]["state_revision"] = current_revision + 1
            state["metadata"]["updated_at"] = datetime.now().isoformat(timespec="seconds")
            self._save_unlocked(state, backup=current is not None)

    @contextmanager
    def transaction(self) -> Iterator[dict[str, Any]]:
        """Lock the full read-modify-write cycle and commit only on success."""
        with self._exclusive_lock():
            state = self._load_unlocked()
            current_revision = state["metadata"]["state_revision"]
            yield state
            state["metadata"]["state_revision"] = current_revision + 1
            state["metadata"]["updated_at"] = datetime.now().isoformat(timespec="seconds")
            self._save_unlocked(state, backup=True)

    def available_backups(self) -> list[Path]:
        if not self.backup_dir.exists():
            return []
        return sorted(self.backup_dir.glob(f"{self.path.stem}.r*.json"), reverse=True)

    def restore_backup(self, backup_path: Path) -> dict[str, Any]:
        target = Path(backup_path).resolve()
        allowed = {path.resolve() for path in self.available_backups()}
        if target not in allowed:
            raise ValueError("backup is not managed by this store")
        restored = migrate_state(json.loads(target.read_text(encoding="utf-8")))
        with self._exclusive_lock():
            current = self._load_unlocked()
            restored["metadata"]["state_revision"] = current["metadata"]["state_revision"] + 1
            restored["metadata"]["updated_at"] = datetime.now().isoformat(timespec="seconds")
            self._save_unlocked(restored, backup=True)
        return restored

    def _load_unlocked(self) -> dict[str, Any]:
        state = migrate_state(json.loads(self.path.read_text(encoding="utf-8")))
        validate_state(state)
        return state

    def _save_unlocked(self, state: dict[str, Any], backup: bool) -> None:
        validate_state(state)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if backup and self.path.exists():
            self._backup_unlocked()
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

    def _backup_unlocked(self) -> None:
        current = self._load_unlocked()
        revision = current["metadata"]["state_revision"]
        stamp = datetime.now().strftime("%Y%m%dT%H%M%S%f")
        self.backup_dir.mkdir(parents=True, exist_ok=True)
        shutil.copy2(self.path, self.backup_dir / f"{self.path.stem}.r{revision:06d}.{stamp}.json")
        for old in self.available_backups()[self.backup_limit :]:
            old.unlink()

    @contextmanager
    def _exclusive_lock(self) -> Iterator[None]:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._thread_lock:
            with self.lock_path.open("a+b") as lock_file:
                if fcntl is not None:
                    fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
                try:
                    yield
                finally:
                    if fcntl is not None:
                        fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)
