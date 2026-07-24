"""Immutable approval for one exact standalone-export source."""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import json
import re
from threading import RLock
from typing import Any

from ._bound_directory import _BoundDirectory
from .domain import PACK_ID_PATTERN, SHA256_PATTERN, SchemaError
from .json_contract import JsonContractError, decode_json_object
from .learning_bundle import LearningPackBundle
from .promotion_models import AcceptedPackSnapshot
from .storage import StorageError


APPROVAL_SCHEMA_VERSION = 1
APPROVAL_ID_PATTERN = re.compile(r"^export-approval-[0-9a-f]{20}$")
_APPROVAL_LOCK = RLock()


def _canonical_bytes(value: dict[str, Any]) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


@dataclass(frozen=True, slots=True)
class ExportApproval:
    """A learner decision bound to complete immutable source identities."""

    approval_id: str
    approval_sha256: str
    bundle_sha256: str
    accepted_snapshot_sha256: str
    pack_id: str
    pack_version: int
    schema_version: int = APPROVAL_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.schema_version != APPROVAL_SCHEMA_VERSION:
            raise SchemaError("unsupported export approval schema_version")
        if (
            not isinstance(self.bundle_sha256, str)
            or not SHA256_PATTERN.fullmatch(self.bundle_sha256)
        ):
            raise SchemaError("approval bundle_sha256 must be a SHA-256 digest")
        if (
            not isinstance(self.accepted_snapshot_sha256, str)
            or not SHA256_PATTERN.fullmatch(self.accepted_snapshot_sha256)
        ):
            raise SchemaError(
                "approval accepted_snapshot_sha256 must be a SHA-256 digest"
            )
        if not isinstance(self.pack_id, str) or not PACK_ID_PATTERN.fullmatch(
            self.pack_id
        ):
            raise SchemaError("approval pack_id does not match the schema")
        if (
            not isinstance(self.pack_version, int)
            or isinstance(self.pack_version, bool)
            or self.pack_version < 1
        ):
            raise SchemaError("approval pack_version must be a positive integer")
        expected = sha256(_canonical_bytes(self._content_dict())).hexdigest()
        if self.approval_sha256 != expected:
            raise SchemaError("approval_sha256 does not match approval content")
        if self.approval_id != f"export-approval-{expected[:20]}":
            raise SchemaError("approval_id must derive from approval_sha256")

    def _content_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "bundle_sha256": self.bundle_sha256,
            "accepted_snapshot_sha256": self.accepted_snapshot_sha256,
            "pack_id": self.pack_id,
            "pack_version": self.pack_version,
        }

    def to_dict(self) -> dict[str, Any]:
        return {
            **self._content_dict(),
            "approval_id": self.approval_id,
            "approval_sha256": self.approval_sha256,
        }

    @classmethod
    def build(
        cls,
        bundle: LearningPackBundle,
        snapshot: AcceptedPackSnapshot,
    ) -> ExportApproval:
        if not isinstance(bundle, LearningPackBundle):
            raise SchemaError("export approval requires a Learning Pack Bundle")
        bundle.require_snapshot(snapshot)
        content = {
            "schema_version": APPROVAL_SCHEMA_VERSION,
            "bundle_sha256": bundle.bundle_sha256,
            "accepted_snapshot_sha256": snapshot.content_sha256,
            "pack_id": snapshot.pack_id,
            "pack_version": snapshot.version,
        }
        digest = sha256(_canonical_bytes(content)).hexdigest()
        return cls(
            **content,
            approval_id=f"export-approval-{digest[:20]}",
            approval_sha256=digest,
        )

    @classmethod
    def from_dict(cls, value: Any) -> ExportApproval:
        expected = {
            "schema_version",
            "approval_id",
            "approval_sha256",
            "bundle_sha256",
            "accepted_snapshot_sha256",
            "pack_id",
            "pack_version",
        }
        if not isinstance(value, dict) or set(value) != expected:
            raise SchemaError("export approval fields do not match the schema")
        return cls(**value)

    def require_bundle(self, bundle: LearningPackBundle) -> None:
        if not isinstance(bundle, LearningPackBundle):
            raise SchemaError("export approval requires a Learning Pack Bundle")
        if (
            self.bundle_sha256 != bundle.bundle_sha256
            or self.accepted_snapshot_sha256
            != bundle.accepted_snapshot_sha256
            or self.pack_id != bundle.pack_id
            or self.pack_version != bundle.pack_version
        ):
            raise SchemaError(
                "export approval does not match the Learning Pack Bundle"
            )


class ExportApprovalRepository:
    """Store one immutable decision for each approved complete bundle."""

    def __init__(self, directory: _BoundDirectory) -> None:
        self._directory = directory
        self.root = directory.path

    def _leaf(self, bundle_sha256: str) -> str:
        if (
            not isinstance(bundle_sha256, str)
            or not SHA256_PATTERN.fullmatch(bundle_sha256)
        ):
            raise StorageError("approval bundle digest does not match the schema")
        return f"{bundle_sha256}.json"

    def get(self, bundle_sha256: str) -> ExportApproval | None:
        encoded = self._directory.read_regular(
            self._leaf(bundle_sha256),
            "export approval",
        )
        if encoded is None:
            return None
        try:
            approval = ExportApproval.from_dict(
                decode_json_object(encoded, "export approval")
            )
        except (JsonContractError, SchemaError, TypeError, ValueError) as exc:
            raise SchemaError("export approval does not match the schema") from exc
        if approval.bundle_sha256 != bundle_sha256:
            raise SchemaError("export approval filename does not match its bundle")
        return approval

    def save(self, approval: ExportApproval) -> ExportApproval:
        if not isinstance(approval, ExportApproval):
            raise SchemaError("approval save requires an ExportApproval")
        encoded = (
            json.dumps(
                approval.to_dict(),
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            )
            + "\n"
        ).encode("utf-8")
        leaf = self._leaf(approval.bundle_sha256)
        with _APPROVAL_LOCK:
            outcome = self._directory.atomic_create(leaf, encoded, 0o600)
            if not outcome.created:
                existing = self.get(approval.bundle_sha256)
                if existing != approval:
                    raise StorageError(
                        "export approval is immutable for one bundle digest"
                    )
                return approval
            if not outcome.directory_synced:
                visible = self._directory.read_regular(leaf, "export approval")
                if visible != encoded:
                    raise StorageError("export approval replacement is uncertain")
        return approval

    def require(self, bundle: LearningPackBundle) -> ExportApproval:
        approval = self.get(bundle.bundle_sha256)
        if approval is None:
            raise StorageError(
                "Learning Pack Bundle has not been explicitly approved for export"
            )
        approval.require_bundle(bundle)
        return approval

    def close(self) -> None:
        self._directory.close()

    def __enter__(self) -> ExportApprovalRepository:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()
