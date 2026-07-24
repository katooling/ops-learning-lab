"""Strict immutable schemas shared by Promotion and accepted-pack readers."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from hashlib import sha256
import json
import re
from typing import Any

from .domain import (
    FACT_STATUSES,
    PACK_ID_PATTERN,
    PROPOSAL_ID_PATTERN,
    SCHEMA_VERSION,
    SHA256_PATTERN,
    SchemaError,
)


CLAIM_ID_PATTERN = re.compile(r"^claim-[0-9a-f]{20}$")
PROMOTION_ID_PATTERN = re.compile(r"^promotion-[0-9a-f]{20}$")
DECISION_ACTIONS = frozenset({"accept", "reject"})
HISTORY_ACTIONS = frozenset({"add", "supersede", "contradict"})
REJECTION_REASONS = frozenset(
    {"not-relevant", "unsupported", "private", "duplicate"}
)
UNSAFE_ACCEPTED_TEXT = (
    re.compile(r"(?:^|[/\\])private(?:[/\\]|$)", re.IGNORECASE),
    re.compile(r"(?:^|[/\\])raw\.bin(?:$|[/\\])", re.IGNORECASE),
    re.compile(r"\bintake-[0-9a-f]{20}\b"),
    re.compile(r"\b[0-9a-f]{64}\b"),
)


class PromotionError(RuntimeError):
    """Raised when a review cannot safely become accepted pack content."""


class StalePromotionError(PromotionError):
    """Raised when the staged update or accepted pack changed after review."""


def _non_empty(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise SchemaError(f"{field} must be a non-empty string")
    return value


def _rfc3339(value: Any, field: str) -> str:
    text = _non_empty(value, field)
    normalized = text[:-1] + "+00:00" if text.endswith("Z") else text
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise SchemaError(f"{field} must be an RFC 3339 timestamp") from exc
    if parsed.tzinfo is None:
        raise SchemaError(f"{field} must include a timezone")
    return text


def _canonical_sha256(value: dict[str, Any]) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return sha256(encoded).hexdigest()


@dataclass(frozen=True, slots=True)
class PromotionDecision:
    """One explicit learner decision for one immutable proposal."""

    proposal_id: str
    action: str
    sanitized_text: str | None
    fact_status: str | None
    history_action: str | None
    target_claim_id: str | None
    sensitivity_reviewed: bool | None
    rejection_reason: str | None

    def __post_init__(self) -> None:
        if (
            not isinstance(self.proposal_id, str)
            or not PROPOSAL_ID_PATTERN.fullmatch(self.proposal_id)
        ):
            raise SchemaError("decision proposal_id does not match the schema")
        if not isinstance(self.action, str) or self.action not in DECISION_ACTIONS:
            raise SchemaError("decision action must be accept or reject")
        if self.action == "reject":
            if any(
                value is not None
                for value in (
                    self.sanitized_text,
                    self.fact_status,
                    self.history_action,
                    self.target_claim_id,
                    self.sensitivity_reviewed,
                )
            ):
                raise SchemaError("a rejected proposal cannot contain accepted fields")
            if (
                not isinstance(self.rejection_reason, str)
                or self.rejection_reason not in REJECTION_REASONS
            ):
                raise SchemaError("a rejected proposal needs a structured reason")
            return

        if self.rejection_reason is not None:
            raise SchemaError("an accepted proposal cannot have a rejection reason")
        if self.sensitivity_reviewed is not True:
            raise SchemaError("accepted text needs an explicit sensitivity review")
        text = _non_empty(self.sanitized_text, "sanitized_text")
        if text != text.strip():
            raise SchemaError("sanitized_text cannot have surrounding whitespace")
        if any(pattern.search(text) for pattern in UNSAFE_ACCEPTED_TEXT):
            raise SchemaError("sanitized_text contains private-only metadata")
        if (
            not isinstance(self.fact_status, str)
            or self.fact_status not in FACT_STATUSES
        ):
            raise SchemaError("accepted fact_status does not match the schema")
        if (
            not isinstance(self.history_action, str)
            or self.history_action not in HISTORY_ACTIONS
        ):
            raise SchemaError("an accepted proposal needs a history decision")
        if self.history_action == "add":
            if self.target_claim_id is not None:
                raise SchemaError("an added claim cannot target an accepted claim")
        elif (
            not isinstance(self.target_claim_id, str)
            or not CLAIM_ID_PATTERN.fullmatch(self.target_claim_id)
        ):
            raise SchemaError(
                "supersede and contradict decisions need a target_claim_id"
            )

    def to_dict(self) -> dict[str, Any]:
        return {
            "proposal_id": self.proposal_id,
            "action": self.action,
            "sanitized_text": self.sanitized_text,
            "fact_status": self.fact_status,
            "history_action": self.history_action,
            "target_claim_id": self.target_claim_id,
            "sensitivity_reviewed": self.sensitivity_reviewed,
            "rejection_reason": self.rejection_reason,
        }

    @classmethod
    def from_dict(cls, value: Any) -> PromotionDecision:
        expected = {
            "proposal_id",
            "action",
            "sanitized_text",
            "fact_status",
            "history_action",
            "target_claim_id",
            "sensitivity_reviewed",
            "rejection_reason",
        }
        if not isinstance(value, dict) or set(value) != expected:
            raise SchemaError("promotion decision fields do not match the schema")
        return cls(**value)


@dataclass(frozen=True, slots=True)
class PromotionPlan:
    """Immutable learner input, bound to one staged update and pack base."""

    update_id: str
    proposal_sha256: str
    target_pack_id: str
    target_pack_title: str
    expected_base_version: int | None
    expected_base_sha256: str | None
    decisions: tuple[PromotionDecision, ...]
    schema_version: int = SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.schema_version != SCHEMA_VERSION:
            raise SchemaError("unsupported promotion plan schema_version")
        if not isinstance(self.update_id, str) or not re.fullmatch(
            r"update-[0-9a-f]{20}", self.update_id
        ):
            raise SchemaError("promotion update_id does not match the schema")
        if not isinstance(
            self.proposal_sha256, str
        ) or not SHA256_PATTERN.fullmatch(self.proposal_sha256):
            raise SchemaError("promotion proposal_sha256 must be a SHA-256 digest")
        if not isinstance(
            self.target_pack_id, str
        ) or not PACK_ID_PATTERN.fullmatch(self.target_pack_id):
            raise SchemaError("target_pack_id must be lowercase kebab-case")
        _non_empty(self.target_pack_title, "target_pack_title")
        base_absent = (
            self.expected_base_version is None
            and self.expected_base_sha256 is None
        )
        base_present = (
            isinstance(self.expected_base_version, int)
            and not isinstance(self.expected_base_version, bool)
            and self.expected_base_version > 0
            and isinstance(self.expected_base_sha256, str)
            and SHA256_PATTERN.fullmatch(self.expected_base_sha256) is not None
        )
        if not (base_absent or base_present):
            raise SchemaError("expected pack version and digest must travel together")
        if not isinstance(self.decisions, tuple):
            raise SchemaError("promotion decisions must be a tuple")
        if any(
            not isinstance(decision, PromotionDecision)
            for decision in self.decisions
        ):
            raise SchemaError("promotion decisions do not match the schema")
        proposal_ids = tuple(decision.proposal_id for decision in self.decisions)
        if len(set(proposal_ids)) != len(proposal_ids):
            raise SchemaError("each proposal may have only one decision")

    @property
    def promotion_sha256(self) -> str:
        return _canonical_sha256(self.to_dict())

    @property
    def promotion_id(self) -> str:
        return f"promotion-{self.promotion_sha256[:20]}"

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "update_id": self.update_id,
            "proposal_sha256": self.proposal_sha256,
            "target_pack_id": self.target_pack_id,
            "target_pack_title": self.target_pack_title,
            "expected_base_version": self.expected_base_version,
            "expected_base_sha256": self.expected_base_sha256,
            "decisions": [decision.to_dict() for decision in self.decisions],
        }

    @classmethod
    def from_dict(cls, value: Any) -> PromotionPlan:
        expected = {
            "schema_version",
            "update_id",
            "proposal_sha256",
            "target_pack_id",
            "target_pack_title",
            "expected_base_version",
            "expected_base_sha256",
            "decisions",
        }
        if not isinstance(value, dict) or set(value) != expected:
            raise SchemaError("promotion plan fields do not match the schema")
        if not isinstance(value["decisions"], list):
            raise SchemaError("promotion decisions must be a list")
        return cls(
            schema_version=value["schema_version"],
            update_id=value["update_id"],
            proposal_sha256=value["proposal_sha256"],
            target_pack_id=value["target_pack_id"],
            target_pack_title=value["target_pack_title"],
            expected_base_version=value["expected_base_version"],
            expected_base_sha256=value["expected_base_sha256"],
            decisions=tuple(
                PromotionDecision.from_dict(item) for item in value["decisions"]
            ),
        )


@dataclass(frozen=True, slots=True)
class AcceptedProvenance:
    """Deliberately narrow source projection safe for an accepted pack."""

    source_type: str
    observed_at: str
    staged_update_id: str
    proposal_id: str

    def __post_init__(self) -> None:
        _non_empty(self.source_type, "accepted source_type")
        _rfc3339(self.observed_at, "accepted observed_at")
        if not isinstance(self.staged_update_id, str) or not re.fullmatch(
            r"update-[0-9a-f]{20}", self.staged_update_id
        ):
            raise SchemaError("accepted staged_update_id does not match the schema")
        if not isinstance(
            self.proposal_id, str
        ) or not PROPOSAL_ID_PATTERN.fullmatch(self.proposal_id):
            raise SchemaError("accepted proposal_id does not match the schema")

    def to_dict(self) -> dict[str, str]:
        return {
            "source_type": self.source_type,
            "observed_at": self.observed_at,
            "staged_update_id": self.staged_update_id,
            "proposal_id": self.proposal_id,
        }

    @classmethod
    def from_dict(cls, value: Any) -> AcceptedProvenance:
        if not isinstance(value, dict) or set(value) != {
            "source_type",
            "observed_at",
            "staged_update_id",
            "proposal_id",
        }:
            raise SchemaError("accepted provenance fields do not match the schema")
        return cls(**value)


@dataclass(frozen=True, slots=True)
class AcceptedClaim:
    claim_id: str
    text: str
    fact_status: str
    history_action: str
    target_claim_id: str | None
    provenance: AcceptedProvenance

    def __post_init__(self) -> None:
        if not isinstance(self.claim_id, str) or not CLAIM_ID_PATTERN.fullmatch(
            self.claim_id
        ):
            raise SchemaError("claim_id does not match the schema")
        _non_empty(self.text, "accepted claim text")
        if (
            not isinstance(self.fact_status, str)
            or self.fact_status not in FACT_STATUSES
        ):
            raise SchemaError("accepted fact_status does not match the schema")
        if (
            not isinstance(self.history_action, str)
            or self.history_action not in HISTORY_ACTIONS
        ):
            raise SchemaError("accepted history_action does not match the schema")
        if self.history_action == "add":
            if self.target_claim_id is not None:
                raise SchemaError("an added accepted claim cannot have a target")
        elif (
            not isinstance(self.target_claim_id, str)
            or not CLAIM_ID_PATTERN.fullmatch(self.target_claim_id)
        ):
            raise SchemaError("accepted history target does not match the schema")
        if not isinstance(self.provenance, AcceptedProvenance):
            raise SchemaError("accepted provenance does not match the schema")

    def to_dict(self) -> dict[str, Any]:
        return {
            "claim_id": self.claim_id,
            "text": self.text,
            "fact_status": self.fact_status,
            "history_action": self.history_action,
            "target_claim_id": self.target_claim_id,
            "provenance": self.provenance.to_dict(),
        }

    @classmethod
    def from_dict(cls, value: Any) -> AcceptedClaim:
        expected = {
            "claim_id",
            "text",
            "fact_status",
            "history_action",
            "target_claim_id",
            "provenance",
        }
        if not isinstance(value, dict) or set(value) != expected:
            raise SchemaError("accepted claim fields do not match the schema")
        return cls(
            claim_id=value["claim_id"],
            text=value["text"],
            fact_status=value["fact_status"],
            history_action=value["history_action"],
            target_claim_id=value["target_claim_id"],
            provenance=AcceptedProvenance.from_dict(value["provenance"]),
        )


@dataclass(frozen=True, slots=True)
class PromotionRecord:
    promotion_id: str
    promotion_sha256: str
    update_id: str
    proposal_sha256: str
    applied_at: str
    base_version: int | None
    base_sha256: str | None
    decisions: tuple[PromotionDecision, ...]

    def __post_init__(self) -> None:
        if not isinstance(
            self.promotion_id, str
        ) or not PROMOTION_ID_PATTERN.fullmatch(self.promotion_id):
            raise SchemaError("promotion_id does not match the schema")
        if not isinstance(
            self.promotion_sha256, str
        ) or not SHA256_PATTERN.fullmatch(self.promotion_sha256):
            raise SchemaError("promotion_sha256 does not match the schema")
        if self.promotion_id != f"promotion-{self.promotion_sha256[:20]}":
            raise SchemaError("promotion_id must derive from promotion_sha256")
        if not isinstance(self.update_id, str) or not re.fullmatch(
            r"update-[0-9a-f]{20}", self.update_id
        ):
            raise SchemaError("promotion update_id does not match the schema")
        if not isinstance(
            self.proposal_sha256, str
        ) or not SHA256_PATTERN.fullmatch(self.proposal_sha256):
            raise SchemaError("promotion proposal digest does not match the schema")
        _rfc3339(self.applied_at, "promotion applied_at")
        base_absent = self.base_version is None and self.base_sha256 is None
        base_present = (
            isinstance(self.base_version, int)
            and not isinstance(self.base_version, bool)
            and self.base_version > 0
            and isinstance(self.base_sha256, str)
            and SHA256_PATTERN.fullmatch(self.base_sha256) is not None
        )
        if not (base_absent or base_present):
            raise SchemaError("promotion base identity does not match the schema")
        if (
            not isinstance(self.decisions, tuple)
            or not self.decisions
            or any(
                not isinstance(decision, PromotionDecision)
                for decision in self.decisions
            )
        ):
            raise SchemaError("promotion record must keep every decision")

    def to_dict(self) -> dict[str, Any]:
        return {
            "promotion_id": self.promotion_id,
            "promotion_sha256": self.promotion_sha256,
            "update_id": self.update_id,
            "proposal_sha256": self.proposal_sha256,
            "applied_at": self.applied_at,
            "base_version": self.base_version,
            "base_sha256": self.base_sha256,
            "decisions": [decision.to_dict() for decision in self.decisions],
        }

    @classmethod
    def from_dict(cls, value: Any) -> PromotionRecord:
        expected = {
            "promotion_id",
            "promotion_sha256",
            "update_id",
            "proposal_sha256",
            "applied_at",
            "base_version",
            "base_sha256",
            "decisions",
        }
        if not isinstance(value, dict) or set(value) != expected:
            raise SchemaError("promotion record fields do not match the schema")
        if not isinstance(value["decisions"], list):
            raise SchemaError("promotion record decisions must be a list")
        return cls(
            promotion_id=value["promotion_id"],
            promotion_sha256=value["promotion_sha256"],
            update_id=value["update_id"],
            proposal_sha256=value["proposal_sha256"],
            applied_at=value["applied_at"],
            base_version=value["base_version"],
            base_sha256=value["base_sha256"],
            decisions=tuple(
                PromotionDecision.from_dict(item) for item in value["decisions"]
            ),
        )


@dataclass(frozen=True, slots=True)
class LearningPack:
    pack_id: str
    title: str
    version: int
    claims: tuple[AcceptedClaim, ...]
    promotions: tuple[PromotionRecord, ...]
    content_sha256: str
    schema_version: int = SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.schema_version != SCHEMA_VERSION:
            raise SchemaError("unsupported Learning Pack schema_version")
        if not isinstance(self.pack_id, str) or not PACK_ID_PATTERN.fullmatch(
            self.pack_id
        ):
            raise SchemaError("pack_id does not match the schema")
        _non_empty(self.title, "pack title")
        if (
            not isinstance(self.version, int)
            or isinstance(self.version, bool)
            or self.version < 1
        ):
            raise SchemaError("pack version must be a positive integer")
        if not isinstance(self.claims, tuple) or not isinstance(
            self.promotions, tuple
        ):
            raise SchemaError("pack collections must be tuples")
        if any(not isinstance(claim, AcceptedClaim) for claim in self.claims):
            raise SchemaError("accepted claims do not match the schema")
        if any(
            not isinstance(record, PromotionRecord) for record in self.promotions
        ):
            raise SchemaError("Promotion records do not match the schema")
        claim_ids = tuple(claim.claim_id for claim in self.claims)
        if len(set(claim_ids)) != len(claim_ids):
            raise SchemaError("accepted claim identifiers must be unique")
        promotion_ids = tuple(record.promotion_id for record in self.promotions)
        if len(set(promotion_ids)) != len(promotion_ids):
            raise SchemaError("promotion record identifiers must be unique")
        if len(self.promotions) != self.version:
            raise SchemaError("each pack version must have one Promotion record")
        known_claims: set[str] = set()
        for claim in self.claims:
            if claim.target_claim_id is not None and claim.target_claim_id not in known_claims:
                raise SchemaError("accepted history target must precede its new claim")
            known_claims.add(claim.claim_id)
        expected = _canonical_sha256(self._content_dict())
        if (
            not isinstance(self.content_sha256, str)
            or not SHA256_PATTERN.fullmatch(self.content_sha256)
            or self.content_sha256 != expected
        ):
            raise SchemaError("pack content_sha256 does not match accepted content")

    def _content_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "pack_id": self.pack_id,
            "title": self.title,
            "version": self.version,
            "claims": [claim.to_dict() for claim in self.claims],
            "promotions": [record.to_dict() for record in self.promotions],
        }

    def to_dict(self) -> dict[str, Any]:
        return {**self._content_dict(), "content_sha256": self.content_sha256}

    @classmethod
    def build(
        cls,
        *,
        pack_id: str,
        title: str,
        version: int,
        claims: tuple[AcceptedClaim, ...],
        promotions: tuple[PromotionRecord, ...],
    ) -> LearningPack:
        content = {
            "schema_version": SCHEMA_VERSION,
            "pack_id": pack_id,
            "title": title,
            "version": version,
            "claims": [claim.to_dict() for claim in claims],
            "promotions": [record.to_dict() for record in promotions],
        }
        return cls(
            pack_id=pack_id,
            title=title,
            version=version,
            claims=claims,
            promotions=promotions,
            content_sha256=_canonical_sha256(content),
        )

    @classmethod
    def from_dict(cls, value: Any) -> LearningPack:
        expected = {
            "schema_version",
            "pack_id",
            "title",
            "version",
            "claims",
            "promotions",
            "content_sha256",
        }
        if not isinstance(value, dict) or set(value) != expected:
            raise SchemaError("Learning Pack fields do not match the schema")
        if not isinstance(value["claims"], list) or not isinstance(
            value["promotions"], list
        ):
            raise SchemaError("Learning Pack collections must be lists")
        return cls(
            schema_version=value["schema_version"],
            pack_id=value["pack_id"],
            title=value["title"],
            version=value["version"],
            claims=tuple(AcceptedClaim.from_dict(item) for item in value["claims"]),
            promotions=tuple(
                PromotionRecord.from_dict(item) for item in value["promotions"]
            ),
            content_sha256=value["content_sha256"],
        )


@dataclass(frozen=True, slots=True)
class AcceptedPackSnapshot:
    """Read-only accepted content, deliberately excluding decision history."""

    pack_id: str
    title: str
    version: int
    content_sha256: str
    claims: tuple[AcceptedClaim, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.pack_id, str) or not PACK_ID_PATTERN.fullmatch(
            self.pack_id
        ):
            raise SchemaError("snapshot pack_id does not match the schema")
        _non_empty(self.title, "snapshot title")
        if (
            not isinstance(self.version, int)
            or isinstance(self.version, bool)
            or self.version < 1
        ):
            raise SchemaError("snapshot version must be a positive integer")
        if not isinstance(
            self.content_sha256, str
        ) or not SHA256_PATTERN.fullmatch(self.content_sha256):
            raise SchemaError("snapshot content_sha256 does not match the schema")
        if not isinstance(self.claims, tuple):
            raise SchemaError("snapshot claims must be a tuple")
        if any(not isinstance(claim, AcceptedClaim) for claim in self.claims):
            raise SchemaError("snapshot claims do not match the schema")

    @classmethod
    def from_pack(cls, pack: LearningPack) -> AcceptedPackSnapshot:
        return cls(
            pack_id=pack.pack_id,
            title=pack.title,
            version=pack.version,
            content_sha256=pack.content_sha256,
            claims=pack.claims,
        )
