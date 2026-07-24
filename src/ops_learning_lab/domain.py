"""Deterministic domain schemas for private intake and staged pack updates."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from hashlib import sha256
import json
import re
from typing import Any


SCHEMA_VERSION = 1
SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
ID_PATTERN = re.compile(r"^intake-[0-9a-f]{20}$")
UPDATE_ID_PATTERN = re.compile(r"^update-[0-9a-f]{20}$")
PACK_ID_PATTERN = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
PROPOSAL_ID_PATTERN = re.compile(r"^proposal-[0-9a-f]{20}$")
FACT_STATUSES = frozenset({"current", "historical", "contradicted", "unverified"})
MATCH_KINDS = frozenset({"strong", "ambiguous", "new_pack"})


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
    retrieval_scope: str | None = None

    def __post_init__(self) -> None:
        _require_non_empty(self.source_type, "source_type")
        _require_non_empty(self.source_id, "source_id")
        _require_rfc3339(self.observed_at, "observed_at")
        if self.retrieval_scope is not None:
            _require_non_empty(self.retrieval_scope, "retrieval_scope")

    def to_dict(self) -> dict[str, str]:
        value = {
            "source_type": self.source_type,
            "source_id": self.source_id,
            "observed_at": self.observed_at,
        }
        if self.retrieval_scope is not None:
            value["retrieval_scope"] = self.retrieval_scope
        return value

    @classmethod
    def from_dict(cls, value: Any) -> SourceReference:
        if not isinstance(value, dict):
            raise SchemaError("source must be an object")
        required = {"source_type", "source_id", "observed_at"}
        allowed = {
            frozenset(required),
            frozenset((*required, "retrieval_scope")),
        }
        if set(value) not in allowed:
            raise SchemaError("source fields do not match the schema")
        return cls(
            source_type=value["source_type"],
            source_id=value["source_id"],
            observed_at=value["observed_at"],
            retrieval_scope=value.get("retrieval_scope"),
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


@dataclass(frozen=True, slots=True)
class PackProfile:
    """Small destination profile used only for deterministic matching."""

    pack_id: str
    title: str
    match_terms: tuple[str, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.pack_id, str) or not PACK_ID_PATTERN.fullmatch(
            self.pack_id
        ):
            raise SchemaError("pack_id must be a lowercase kebab-case identifier")
        _require_non_empty(self.title, "title")
        if not isinstance(self.match_terms, tuple) or len(self.match_terms) < 2:
            raise SchemaError("match_terms must contain at least two terms")
        normalized = tuple(
            _require_non_empty(term, "match_term").lower()
            for term in self.match_terms
        )
        if normalized != self.match_terms or len(set(normalized)) != len(normalized):
            raise SchemaError("match_terms must be unique lowercase terms")

    @property
    def profile_sha256(self) -> str:
        identity = "\0".join((self.pack_id, self.title, *self.match_terms))
        return sha256(identity.encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class MatchCandidate:
    pack_id: str
    title: str
    matched_terms: tuple[str, ...]
    match_profile_sha256: str
    expected_base_version: int | None
    expected_base_sha256: str | None

    def __post_init__(self) -> None:
        if not isinstance(self.pack_id, str) or not PACK_ID_PATTERN.fullmatch(
            self.pack_id
        ):
            raise SchemaError("candidate pack_id does not match the schema")
        _require_non_empty(self.title, "candidate title")
        if not isinstance(self.matched_terms, tuple) or not self.matched_terms:
            raise SchemaError("candidate matched_terms must not be empty")
        if tuple(sorted(set(self.matched_terms))) != self.matched_terms:
            raise SchemaError("candidate matched_terms must be sorted and unique")
        if (
            not isinstance(self.match_profile_sha256, str)
            or not SHA256_PATTERN.fullmatch(self.match_profile_sha256)
        ):
            raise SchemaError("match_profile_sha256 must be a SHA-256 digest")
        base_is_absent = (
            self.expected_base_version is None
            and self.expected_base_sha256 is None
        )
        base_is_present = (
            isinstance(self.expected_base_version, int)
            and not isinstance(self.expected_base_version, bool)
            and self.expected_base_version > 0
            and isinstance(self.expected_base_sha256, str)
            and SHA256_PATTERN.fullmatch(self.expected_base_sha256) is not None
        )
        if not (base_is_absent or base_is_present):
            raise SchemaError(
                "expected base version and digest must both be absent or valid"
            )

    def to_dict(self) -> dict[str, Any]:
        return {
            "pack_id": self.pack_id,
            "title": self.title,
            "matched_terms": list(self.matched_terms),
            "match_profile_sha256": self.match_profile_sha256,
            "expected_base_version": self.expected_base_version,
            "expected_base_sha256": self.expected_base_sha256,
        }

    @classmethod
    def from_dict(cls, value: Any) -> MatchCandidate:
        if not isinstance(value, dict) or set(value) != {
            "pack_id",
            "title",
            "matched_terms",
            "match_profile_sha256",
            "expected_base_version",
            "expected_base_sha256",
        }:
            raise SchemaError("match candidate fields do not match the schema")
        if not isinstance(value["matched_terms"], list):
            raise SchemaError("candidate matched_terms must be a list")
        return cls(
            pack_id=value["pack_id"],
            title=value["title"],
            matched_terms=tuple(value["matched_terms"]),
            match_profile_sha256=value["match_profile_sha256"],
            expected_base_version=value["expected_base_version"],
            expected_base_sha256=value["expected_base_sha256"],
        )


@dataclass(frozen=True, slots=True)
class PackMatch:
    kind: str
    candidates: tuple[MatchCandidate, ...]
    proposed_pack_id: str | None
    reasons: tuple[str, ...]

    def __post_init__(self) -> None:
        if self.kind not in MATCH_KINDS:
            raise SchemaError("match kind does not match the schema")
        if not isinstance(self.candidates, tuple):
            raise SchemaError("match candidates must be a tuple")
        if not isinstance(self.reasons, tuple) or not self.reasons:
            raise SchemaError("match reasons must not be empty")
        for reason in self.reasons:
            _require_non_empty(reason, "match reason")
        candidate_ids = tuple(candidate.pack_id for candidate in self.candidates)
        if len(set(candidate_ids)) != len(candidate_ids):
            raise SchemaError("match candidates must be unique")
        if self.kind == "strong":
            if len(self.candidates) != 1:
                raise SchemaError("a strong match requires exactly one candidate")
            if self.proposed_pack_id != self.candidates[0].pack_id:
                raise SchemaError("a strong match must propose its only candidate")
        elif self.kind == "ambiguous":
            if len(self.candidates) < 2 or self.proposed_pack_id is not None:
                raise SchemaError("an ambiguous match requires learner choice")
        elif self.candidates or self.proposed_pack_id is not None:
            raise SchemaError("a new-pack match cannot select an existing pack")

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "candidates": [candidate.to_dict() for candidate in self.candidates],
            "proposed_pack_id": self.proposed_pack_id,
            "reasons": list(self.reasons),
        }

    @classmethod
    def from_dict(cls, value: Any) -> PackMatch:
        if not isinstance(value, dict) or set(value) != {
            "kind",
            "candidates",
            "proposed_pack_id",
            "reasons",
        }:
            raise SchemaError("pack match fields do not match the schema")
        if not isinstance(value["candidates"], list) or not isinstance(
            value["reasons"], list
        ):
            raise SchemaError("pack match collections must be lists")
        return cls(
            kind=value["kind"],
            candidates=tuple(
                MatchCandidate.from_dict(candidate)
                for candidate in value["candidates"]
            ),
            proposed_pack_id=value["proposed_pack_id"],
            reasons=tuple(value["reasons"]),
        )


@dataclass(frozen=True, slots=True)
class ProposedClaim:
    proposal_id: str
    text: str
    fact_status: str
    source_intake_id: str
    source_content_sha256: str
    relation: str = "add"
    target_claim_id: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.proposal_id, str) or not PROPOSAL_ID_PATTERN.fullmatch(
            self.proposal_id
        ):
            raise SchemaError("proposal_id does not match the schema")
        _require_non_empty(self.text, "claim text")
        if self.fact_status not in FACT_STATUSES:
            raise SchemaError("fact_status does not match the schema")
        if not isinstance(self.source_intake_id, str) or not ID_PATTERN.fullmatch(
            self.source_intake_id
        ):
            raise SchemaError("source_intake_id does not match the schema")
        if (
            not isinstance(self.source_content_sha256, str)
            or not SHA256_PATTERN.fullmatch(self.source_content_sha256)
        ):
            raise SchemaError("source_content_sha256 must be a SHA-256 digest")
        if self.relation != "add" or self.target_claim_id is not None:
            raise SchemaError(
                "Capture Mode supports additions only; review owns later relations"
            )

    def to_dict(self) -> dict[str, Any]:
        return {
            "proposal_id": self.proposal_id,
            "text": self.text,
            "fact_status": self.fact_status,
            "source_intake_id": self.source_intake_id,
            "source_content_sha256": self.source_content_sha256,
            "relation": self.relation,
            "target_claim_id": self.target_claim_id,
        }

    @classmethod
    def from_dict(cls, value: Any) -> ProposedClaim:
        expected = {
            "proposal_id",
            "text",
            "fact_status",
            "source_intake_id",
            "source_content_sha256",
            "relation",
            "target_claim_id",
        }
        if not isinstance(value, dict) or set(value) != expected:
            raise SchemaError("proposed claim fields do not match the schema")
        return cls(**value)


@dataclass(frozen=True, slots=True)
class StagedSource:
    """Safe provenance view; intentionally omits the private source identifier."""

    source_type: str
    observed_at: str

    def __post_init__(self) -> None:
        _require_non_empty(self.source_type, "source_type")
        _require_rfc3339(self.observed_at, "observed_at")

    def to_dict(self) -> dict[str, str]:
        return {
            "source_type": self.source_type,
            "observed_at": self.observed_at,
        }

    @classmethod
    def from_dict(cls, value: Any) -> StagedSource:
        if not isinstance(value, dict) or set(value) != {
            "source_type",
            "observed_at",
        }:
            raise SchemaError("staged source fields do not match the schema")
        return cls(**value)


@dataclass(frozen=True, slots=True)
class StagedPackUpdate:
    update_id: str
    intake_id: str
    content_sha256: str
    source: StagedSource
    match: PackMatch
    proposed_claims: tuple[ProposedClaim, ...]
    proposal_sha256: str
    redactions: tuple[str, ...]
    compiler_version: int = 1
    schema_version: int = SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.schema_version != SCHEMA_VERSION:
            raise SchemaError(f"unsupported schema_version: {self.schema_version}")
        if not isinstance(self.update_id, str) or not UPDATE_ID_PATTERN.fullmatch(
            self.update_id
        ):
            raise SchemaError("update_id does not match the schema")
        if not isinstance(self.intake_id, str) or not ID_PATTERN.fullmatch(
            self.intake_id
        ):
            raise SchemaError("intake_id does not match the schema")
        if (
            not isinstance(self.content_sha256, str)
            or not SHA256_PATTERN.fullmatch(self.content_sha256)
        ):
            raise SchemaError("content_sha256 must be a SHA-256 digest")
        if not isinstance(self.source, StagedSource):
            raise SchemaError("source must be a StagedSource")
        if not isinstance(self.match, PackMatch):
            raise SchemaError("match must be a PackMatch")
        if not isinstance(self.proposed_claims, tuple):
            raise SchemaError("proposed_claims must be a tuple")
        if any(
            claim.source_intake_id != self.intake_id
            or claim.source_content_sha256 != self.content_sha256
            for claim in self.proposed_claims
        ):
            raise SchemaError("every proposed claim must reference this intake")
        if self.compiler_version != 1:
            raise SchemaError("unsupported compiler_version")
        if not SHA256_PATTERN.fullmatch(self.proposal_sha256):
            raise SchemaError("proposal_sha256 must be a SHA-256 digest")
        if self.update_id != f"update-{self.proposal_sha256[:20]}":
            raise SchemaError("update_id must be derived from proposal_sha256")
        if not isinstance(self.redactions, tuple) or not self.redactions:
            raise SchemaError("redactions must describe omitted private material")
        for redaction in self.redactions:
            _require_non_empty(redaction, "redaction")
        if self.proposal_sha256 != self.calculate_proposal_sha256(
            compiler_version=self.compiler_version,
            intake_id=self.intake_id,
            content_sha256=self.content_sha256,
            source=self.source,
            match=self.match,
            proposed_claims=self.proposed_claims,
            redactions=self.redactions,
        ):
            raise SchemaError("proposal_sha256 does not match staged content")

    @staticmethod
    def calculate_proposal_sha256(
        *,
        compiler_version: int,
        intake_id: str,
        content_sha256: str,
        source: StagedSource,
        match: PackMatch,
        proposed_claims: tuple[ProposedClaim, ...],
        redactions: tuple[str, ...],
    ) -> str:
        payload = {
            "schema_version": SCHEMA_VERSION,
            "compiler_version": compiler_version,
            "intake_id": intake_id,
            "content_sha256": content_sha256,
            "source": source.to_dict(),
            "match": match.to_dict(),
            "proposed_claims": [claim.to_dict() for claim in proposed_claims],
            "redactions": list(redactions),
        }
        canonical = json.dumps(
            payload,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        ).encode("utf-8")
        return sha256(canonical).hexdigest()

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "compiler_version": self.compiler_version,
            "update_id": self.update_id,
            "intake_id": self.intake_id,
            "content_sha256": self.content_sha256,
            "source": self.source.to_dict(),
            "match": self.match.to_dict(),
            "proposed_claims": [claim.to_dict() for claim in self.proposed_claims],
            "proposal_sha256": self.proposal_sha256,
            "redactions": list(self.redactions),
        }

    @classmethod
    def from_dict(cls, value: Any) -> StagedPackUpdate:
        expected = {
            "schema_version",
            "compiler_version",
            "update_id",
            "intake_id",
            "content_sha256",
            "source",
            "match",
            "proposed_claims",
            "proposal_sha256",
            "redactions",
        }
        if not isinstance(value, dict) or set(value) != expected:
            raise SchemaError("staged update fields do not match the schema")
        if not isinstance(value["proposed_claims"], list) or not isinstance(
            value["redactions"], list
        ):
            raise SchemaError("staged update collections must be lists")
        return cls(
            schema_version=value["schema_version"],
            compiler_version=value["compiler_version"],
            update_id=value["update_id"],
            intake_id=value["intake_id"],
            content_sha256=value["content_sha256"],
            source=StagedSource.from_dict(value["source"]),
            match=PackMatch.from_dict(value["match"]),
            proposed_claims=tuple(
                ProposedClaim.from_dict(claim)
                for claim in value["proposed_claims"]
            ),
            proposal_sha256=value["proposal_sha256"],
            redactions=tuple(value["redactions"]),
        )
