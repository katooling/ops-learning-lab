"""Content-addressed sanitized Learning Pack Bundle snapshots."""

from __future__ import annotations

import json
from pathlib import Path
from threading import RLock

from ._bound_directory import _BoundDirectory
from .domain import SHA256_PATTERN, SchemaError
from .json_contract import JsonContractError, decode_json_object
from .learning_bundle import LearningPackBundle
from .promotion_models import AcceptedPackSnapshot
from .storage import StorageError


_BUNDLE_LOCK = RLock()


class BundleRepository:
    """Persist public bundle snapshots without exposing any private store."""

    def __init__(self, root: Path, directory: _BoundDirectory) -> None:
        self.root = root
        self._directory = directory

    @classmethod
    def open(cls, learning_home: Path) -> BundleRepository:
        home = learning_home.resolve(strict=True)
        snapshots = home / "snapshots"
        if snapshots.is_symlink() or not snapshots.is_dir():
            raise StorageError("snapshots directory is missing or unsafe")
        try:
            snapshots.resolve(strict=True).relative_to(home)
        except (OSError, ValueError) as exc:
            raise StorageError("snapshots directory escapes the learning home") from exc
        root = snapshots / "learning-packs"
        if root.is_symlink():
            raise StorageError(
                "Learning Pack snapshot directory cannot be a symbolic link"
            )
        try:
            root.mkdir(exist_ok=True)
        except OSError as exc:
            raise StorageError(
                "Learning Pack snapshot directory is unsafe"
            ) from exc
        if root.is_symlink() or not root.is_dir():
            raise StorageError("Learning Pack snapshot directory is unsafe")
        return cls(
            root,
            _BoundDirectory.open(root, "Learning Pack snapshot directory"),
        )

    def _leaf(self, bundle_sha256: str) -> str:
        if (
            not isinstance(bundle_sha256, str)
            or not SHA256_PATTERN.fullmatch(bundle_sha256)
        ):
            raise StorageError("bundle digest does not match the schema")
        return f"{bundle_sha256}.json"

    def get(self, bundle_sha256: str) -> LearningPackBundle | None:
        encoded = self._directory.read_regular(
            self._leaf(bundle_sha256),
            "Learning Pack Bundle",
        )
        if encoded is None:
            return None
        try:
            return LearningPackBundle.from_dict(
                decode_json_object(encoded, "Learning Pack Bundle")
            )
        except (JsonContractError, SchemaError, TypeError, ValueError) as exc:
            raise SchemaError(
                "Learning Pack Bundle does not match the schema"
            ) from exc

    def snapshot(self, bundle_sha256: str) -> LearningPackBundle | None:
        bundle = self.get(bundle_sha256)
        if bundle is not None and bundle.bundle_sha256 != bundle_sha256:
            raise SchemaError(
                "Learning Pack Bundle filename does not match its digest"
            )
        return bundle

    def require_current(
        self,
        bundle_sha256: str,
        snapshot: AcceptedPackSnapshot,
    ) -> LearningPackBundle:
        """Load one canonical bundle and bind it to the current accepted pack."""

        if not isinstance(snapshot, AcceptedPackSnapshot):
            raise SchemaError(
                "current bundle verification requires an Accepted Pack snapshot"
            )
        bundle = self.snapshot(bundle_sha256)
        if bundle is None:
            raise StorageError("Learning Pack Bundle snapshot was not found")
        if (
            bundle.pack_id != snapshot.pack_id
            or bundle.title != snapshot.title
            or bundle.pack_version != snapshot.version
            or bundle.accepted_snapshot_sha256 != snapshot.content_sha256
            or bundle.claims != snapshot.claims
        ):
            raise SchemaError(
                "Learning Pack Bundle does not match the current accepted snapshot"
            )
        return bundle

    def save(self, bundle: LearningPackBundle) -> LearningPackBundle:
        if not isinstance(bundle, LearningPackBundle):
            raise SchemaError("bundle save requires a Learning Pack Bundle")
        leaf = self._leaf(bundle.bundle_sha256)
        encoded = (
            json.dumps(
                bundle.to_dict(),
                indent=2,
                ensure_ascii=False,
                sort_keys=True,
            )
            + "\n"
        ).encode("utf-8")
        with _BUNDLE_LOCK:
            existing = self.get(bundle.bundle_sha256)
            if existing is not None:
                if existing != bundle:
                    raise StorageError(
                        "bundle digest collides with different content"
                    )
                return existing
            outcome = self._directory.atomic_replace(leaf, encoded, 0o600)
            if not outcome.directory_synced:
                visible = self._directory.read_regular(
                    leaf,
                    "Learning Pack Bundle",
                )
                if visible != encoded:
                    raise StorageError(
                        "Learning Pack Bundle replacement is uncertain"
                    )
        return bundle

    def close(self) -> None:
        self._directory.close()

    def __enter__(self) -> BundleRepository:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()
