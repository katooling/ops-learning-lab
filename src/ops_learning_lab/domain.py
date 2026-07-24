"""Deterministic domain schemas for private intake."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import re
from typing import Any


SCHEMA_VERSION = 1
SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
ID_PATTERN = re.compile(r"^intake-[0-9a-f]{20}$")


class SchemaError(ValueError):
    """Raised when persisted data does not satisfy a domain schema."""


def _require_non_empty(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise SchemaError(f"{field} must be a non-empty string")
    return value


def _require_rfc3339(value: Any, field: str) -> str:
    text = _require_non_empty(value, field)
    normalized = text[:-1] + "+00:00" if text.endswith("Z") else text
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise SchemaError(f"{field} must be an RFC 3339 timestamp") from exc
    if parsed.tzinfo is None:
        raise SchemaError(f"{field} must include a timezone")
    return text


@dataclass(frozen=True, slots=True)
class SourceReference:
    source_type: str
    source_id: str
    observed_at: str

    def __post_init__(self) -> None:
        _require_non_empty(self.source_type, "source_type")
        _require_non_empty(self.source_id, "source_id")
        _require_rfc3339(self.observed_at, "observed_at")

    def to_dict(self) -> dict[str, str]:
        return {
            "source_type": self.source_type,
            "source_id": self.source_id,
            "observed_at": self.observed_at,
        }

    @classmethod
    def from_dict(cls, value: Any) -> SourceReference:
        if not isinstance(value, dict):
            raise SchemaError("source must be an object")
        expected = {"source_type", "source_id", "observed_at"}
        if set(value) != expected:
            raise SchemaError("source fields do not match the schema")
        return cls(
            source_type=value["source_type"],
            source_id=value["source_id"],
            observed_at=value["observed_at"],
        )


@dataclass(frozen=True, slots=True)
class IntakeManifest:
    intake_id: str
    content_sha256: str
    byte_count: int
    raw_file: str
    source: SourceReference
    schema_version: int = SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.schema_version != SCHEMA_VERSION:
            raise SchemaError(f"unsupported schema_version: {self.schema_version}")
        if not isinstance(self.intake_id, str) or not ID_PATTERN.fullmatch(self.intake_id):
            raise SchemaError("intake_id does not match the schema")
        if (
            not isinstance(self.content_sha256, str)
            or not SHA256_PATTERN.fullmatch(self.content_sha256)
        ):
            raise SchemaError("content_sha256 must be a lowercase SHA-256 digest")
        if not isinstance(self.byte_count, int) or isinstance(self.byte_count, bool):
            raise SchemaError("byte_count must be an integer")
        if self.byte_count < 0:
            raise SchemaError("byte_count must be non-negative")
        if self.raw_file != "raw.bin":
            raise SchemaError("raw_file must be raw.bin")
        if not isinstance(self.source, SourceReference):
            raise SchemaError("source must be a SourceReference")

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "intake_id": self.intake_id,
            "content_sha256": self.content_sha256,
            "byte_count": self.byte_count,
            "raw_file": self.raw_file,
            "source": self.source.to_dict(),
        }

    @classmethod
    def from_dict(cls, value: Any) -> IntakeManifest:
        if not isinstance(value, dict):
            raise SchemaError("manifest must be an object")
        expected = {
            "schema_version",
            "intake_id",
            "content_sha256",
            "byte_count",
            "raw_file",
            "source",
        }
        if set(value) != expected:
            raise SchemaError("manifest fields do not match the schema")
        return cls(
            schema_version=value["schema_version"],
            intake_id=value["intake_id"],
            content_sha256=value["content_sha256"],
            byte_count=value["byte_count"],
            raw_file=value["raw_file"],
            source=SourceReference.from_dict(value["source"]),
        )
