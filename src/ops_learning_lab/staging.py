"""Validated atomic persistence for staged Pack Updates."""

from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
import stat
from typing import Iterable

from .domain import StagedPackUpdate, UPDATE_ID_PATTERN
from .storage import (
    STAGED_UPDATES,
    StorageError,
    _read_confined_regular_file,
    _write_atomic,
)


def _require_safe_directory(path: Path, parent: Path, label: str) -> None:
    if path.is_symlink() or not path.is_dir():
        raise StorageError(f"{label} is missing or unsafe")
    try:
        path.resolve(strict=True).relative_to(parent.resolve(strict=True))
    except (OSError, ValueError) as exc:
        raise StorageError(f"{label} escapes the learning home") from exc
    metadata = path.stat()
    if hasattr(os, "getuid") and metadata.st_uid != os.getuid():
        raise StorageError(f"{label} is not owned by the current user")
    if metadata.st_mode & 0o077:
        raise StorageError(f"{label} permissions must be 0700")


@dataclass(frozen=True, slots=True)
class PackUpdateRepository:
    """Product Shell capability: staged updates only, never raw intake."""

    root: Path

    @classmethod
    def open(cls, learning_home: Path) -> PackUpdateRepository:
        staged = learning_home / STAGED_UPDATES.parent
        root = learning_home / STAGED_UPDATES
        _require_safe_directory(staged, learning_home, "staged directory")
        _require_safe_directory(root, learning_home, "staged updates")
        return cls(root)

    def stage(self, update: StagedPackUpdate) -> StagedPackUpdate:
        _require_safe_directory(self.root, self.root.parent.parent, "staged updates")
        path = self.root / f"{update.update_id}.json"
        if path.is_symlink():
            raise StorageError("staged update cannot be a symbolic link")
        encoded = (
            json.dumps(update.to_dict(), indent=2, sort_keys=True, ensure_ascii=False)
            + "\n"
        ).encode("utf-8")
        if path.exists():
            existing = self.get(update.update_id)
            if existing != update:
                raise StorageError("staged update identity collides with different data")
            return existing
        _write_atomic(path, encoded, 0o600)
        return self.get(update.update_id)

    def get(self, update_id: str) -> StagedPackUpdate:
        if not isinstance(update_id, str) or not UPDATE_ID_PATTERN.fullmatch(update_id):
            raise StorageError("update_id does not match the schema")
        data = _read_confined_regular_file(
            self.root / f"{update_id}.json",
            self.root,
            "staged update",
        )
        try:
            value = json.loads(data.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise StorageError(f"cannot read staged update {update_id}") from exc
        return StagedPackUpdate.from_dict(value)

    def list(self) -> Iterable[StagedPackUpdate]:
        _require_safe_directory(self.root, self.root.parent.parent, "staged updates")
        for path in sorted(self.root.glob("update-*.json")):
            if path.is_symlink() or not stat.S_ISREG(path.stat().st_mode):
                raise StorageError("staged updates must be regular files")
            yield self.get(path.stem)
