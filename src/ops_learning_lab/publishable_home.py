"""One retained learning-home capability for approval and export."""

from __future__ import annotations

from pathlib import Path

from ._bound_directory import _BoundDirectory
from .bundle_repository import BundleRepository
from .domain import PACK_ID_PATTERN, SchemaError
from .export_approval import ExportApprovalRepository
from .export_repository import ExportRepository
from .json_contract import JsonContractError, decode_json_object
from .promotion_models import AcceptedPackSnapshot, LearningPack
from .storage import StorageError, _inside_git_worktree


class PublishableHome:
    """Open publishable stores relative to one retained home descriptor."""

    def __init__(self, root: _BoundDirectory) -> None:
        self._root = root
        self.root = root.path

    @classmethod
    def open(cls, path: Path) -> PublishableHome:
        root = _BoundDirectory.open(path, "learning home")
        try:
            if _inside_git_worktree(root.path):
                raise StorageError("learning home cannot be inside a Git worktree")
            marker = root.read_regular(
                ".ops-learning-lab-home",
                "learning home marker",
            )
            if marker is None:
                raise StorageError("learning home is not initialized")
            try:
                value = decode_json_object(marker, "learning home marker")
            except JsonContractError as exc:
                raise StorageError("learning home marker is invalid") from exc
            if value != {"schema_version": 1}:
                raise StorageError("learning home marker is invalid")
            return cls(root)
        except BaseException:
            root.close()
            raise

    def bundles(self) -> BundleRepository:
        snapshots = self._root.open_child_directory(
            "snapshots",
            "snapshots directory",
        )
        try:
            bundles = snapshots.open_child_directory(
                "learning-packs",
                "Learning Pack snapshot directory",
            )
        finally:
            snapshots.close()
        return BundleRepository.from_directory(bundles)

    def approvals(self, *, create: bool = False) -> ExportApprovalRepository:
        snapshots = self._root.open_child_directory(
            "snapshots",
            "snapshots directory",
        )
        try:
            approvals = snapshots.open_child_directory(
                "export-approvals",
                "export approval directory",
                create=create,
            )
        finally:
            snapshots.close()
        return ExportApprovalRepository(approvals)

    def exports(self) -> ExportRepository:
        return ExportRepository.from_directory(
            self._root.open_child_directory(
                "exports",
                "exports directory",
            )
        )

    def accepted_snapshot(self, pack_id: str) -> AcceptedPackSnapshot:
        if not isinstance(pack_id, str) or not PACK_ID_PATTERN.fullmatch(pack_id):
            raise StorageError("pack_id does not match the schema")
        packs = self._root.open_child_directory("packs", "packs directory")
        try:
            pack = packs.open_child_directory(
                pack_id,
                "Learning Pack directory",
            )
        finally:
            packs.close()
        try:
            encoded = pack.read_regular("pack.json", "Learning Pack")
            if encoded is None:
                raise StorageError("accepted Learning Pack does not exist")
            try:
                accepted = LearningPack.from_dict(
                    decode_json_object(encoded, "Learning Pack")
                )
            except (
                JsonContractError,
                SchemaError,
                TypeError,
                ValueError,
            ) as exc:
                raise SchemaError(
                    "Learning Pack does not match the schema"
                ) from exc
            if accepted.pack_id != pack_id:
                raise SchemaError(
                    "Learning Pack directory does not match its pack_id"
                )
            return AcceptedPackSnapshot.from_pack(accepted)
        finally:
            pack.close()

    def close(self) -> None:
        self._root.close()

    def __enter__(self) -> PublishableHome:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()
