"""Safe persistence and read capability for accepted Learning Packs."""

from __future__ import annotations

from contextlib import contextmanager
import json
import os
from pathlib import Path
import stat
from threading import RLock
from typing import Iterator

from .domain import PACK_ID_PATTERN, SchemaError
from .json_contract import JsonContractError, decode_json_object
from .promotion_models import (
    AcceptedPackSnapshot,
    LearningPack,
    PromotionRecord,
)
from .storage import StorageError, _read_confined_regular_file, _write_atomic

try:
    import fcntl as _fcntl
except ImportError:  # pragma: no cover - Windows keeps the in-process lock.
    _fcntl = None


_PROMOTION_LOCK = RLock()


class PackRepository:
    """Safe accepted-pack persistence with one cross-process promotion lock."""

    def __init__(self, root: Path) -> None:
        self.root = root

    @classmethod
    def open(cls, learning_home: Path) -> PackRepository:
        root = learning_home / "packs"
        if root.is_symlink() or not root.is_dir():
            raise StorageError("packs directory is missing or unsafe")
        try:
            root.resolve(strict=True).relative_to(learning_home.resolve(strict=True))
        except (OSError, ValueError) as exc:
            raise StorageError("packs directory escapes the learning home") from exc
        return cls(root)

    def _path(self, pack_id: str) -> Path:
        if not isinstance(pack_id, str) or not PACK_ID_PATTERN.fullmatch(pack_id):
            raise StorageError("pack_id does not match the schema")
        pack_root = self.root / pack_id
        if pack_root.is_symlink():
            raise StorageError("Learning Pack directory cannot be a symbolic link")
        return pack_root / "pack.json"

    def get(self, pack_id: str) -> LearningPack | None:
        path = self._path(pack_id)
        if not path.exists() and not path.is_symlink():
            return None
        pack_root = path.parent
        if not pack_root.is_dir():
            raise StorageError("Learning Pack directory is missing or unsafe")
        data = _read_confined_regular_file(path, pack_root, "Learning Pack")
        try:
            return LearningPack.from_dict(decode_json_object(data, "Learning Pack"))
        except (JsonContractError, SchemaError, TypeError, ValueError) as exc:
            raise SchemaError("Learning Pack does not match the schema") from exc

    def list(self) -> Iterator[LearningPack]:
        for path in sorted(self.root.iterdir()):
            if path.name.startswith("."):
                continue
            if path.is_symlink() or not path.is_dir():
                raise StorageError("Learning Pack entries must be safe directories")
            pack = self.get(path.name)
            if pack is not None:
                yield pack

    def snapshot(self, pack_id: str) -> AcceptedPackSnapshot | None:
        """Return accepted content without staged/private or Promotion capabilities."""
        pack = self.get(pack_id)
        return AcceptedPackSnapshot.from_pack(pack) if pack is not None else None

    @contextmanager
    def locked(self) -> Iterator[None]:
        lock_path = self.root / ".promotion.lock"
        with _PROMOTION_LOCK:
            flags = os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0)
            try:
                descriptor = os.open(lock_path, flags, 0o600)
            except OSError as exc:
                raise StorageError("cannot open Promotion lock") from exc
            try:
                metadata = os.fstat(descriptor)
                if not stat.S_ISREG(metadata.st_mode):
                    raise StorageError("Promotion lock must be a regular file")
                os.fchmod(descriptor, 0o600)
                if _fcntl is not None:
                    _fcntl.flock(descriptor, _fcntl.LOCK_EX)
                yield
            finally:
                os.close(descriptor)

    def write(self, pack: LearningPack) -> None:
        path = self._path(pack.pack_id)
        pack_root = path.parent
        if pack_root.exists() and not pack_root.is_dir():
            raise StorageError("Learning Pack directory is missing or unsafe")
        created = not pack_root.exists()
        pack_root.mkdir(mode=0o700, exist_ok=True)
        if pack_root.is_symlink() or not pack_root.is_dir():
            raise StorageError("Learning Pack directory is missing or unsafe")
        os.chmod(pack_root, 0o700)
        if path.is_symlink():
            raise StorageError("Learning Pack cannot be a symbolic link")
        encoded = (
            json.dumps(pack.to_dict(), indent=2, ensure_ascii=False, sort_keys=True)
            + "\n"
        ).encode("utf-8")
        try:
            _write_atomic(path, encoded, 0o600)
        except BaseException:
            if created:
                try:
                    pack_root.rmdir()
                except OSError:
                    pass
            raise

    def find_promotion_by_update(
        self,
        update_id: str,
    ) -> tuple[LearningPack, PromotionRecord] | None:
        found: tuple[LearningPack, PromotionRecord] | None = None
        for pack in self.list():
            for record in pack.promotions:
                if record.update_id != update_id:
                    continue
                if found is not None:
                    raise StorageError("staged update has multiple Promotion records")
                found = (pack, record)
        return found
