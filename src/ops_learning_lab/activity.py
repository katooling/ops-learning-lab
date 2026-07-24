"""Pure deterministic Activity Renderer for the first synthetic ETL lesson."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from hashlib import sha256
import json
import re
from typing import Any

from .domain import SHA256_PATTERN, SchemaError


ACTIVITY_SCHEMA_VERSION = 1
ACTIVITY_ID_PATTERN = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
ACTIVITY_STATUSES = frozenset({"ready", "complete", "blocked"})


def _canonical_sha256(value: dict[str, Any]) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return sha256(encoded).hexdigest()


def _non_empty(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise SchemaError(f"{field} must be a non-empty string")
    return value


def _non_negative_int(value: Any, field: str) -> int:
    if (
        not isinstance(value, int)
        or isinstance(value, bool)
        or value < 0
    ):
        raise SchemaError(f"{field} must be a non-negative integer")
    return value


@dataclass(frozen=True, slots=True)
class SyntheticUsageRecord:
    event_id: str
    learner_alias: str
    model: str
    credits: int

    def __post_init__(self) -> None:
        _non_empty(self.event_id, "event_id")
        _non_empty(self.learner_alias, "learner_alias")
        _non_empty(self.model, "model")
        _non_negative_int(self.credits, "credits")

    def to_dict(self) -> dict[str, Any]:
        return {
            "event_id": self.event_id,
            "learner_alias": self.learner_alias,
            "model": self.model,
            "credits": self.credits,
        }


@dataclass(frozen=True, slots=True)
class EtlActivityInput:
    scenario_id: str
    seed: int
    records: tuple[SyntheticUsageRecord, ...]
    cost_cents_per_credit: int
    stop_on_validation_failure: bool
    schema_version: int = ACTIVITY_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.schema_version != ACTIVITY_SCHEMA_VERSION:
            raise SchemaError("unsupported activity schema_version")
        if (
            not isinstance(self.scenario_id, str)
            or not ACTIVITY_ID_PATTERN.fullmatch(self.scenario_id)
        ):
            raise SchemaError("scenario_id must be lowercase kebab-case")
        if (
            not isinstance(self.seed, int)
            or isinstance(self.seed, bool)
            or self.seed < 1
        ):
            raise SchemaError("activity seed must be a positive integer")
        if (
            not isinstance(self.records, tuple)
            or not self.records
            or any(
                not isinstance(record, SyntheticUsageRecord)
                for record in self.records
            )
        ):
            raise SchemaError("activity records must be a non-empty tuple")
        if len({record.event_id for record in self.records}) == len(self.records):
            raise SchemaError("the synthetic activity must contain one duplicate key")
        _non_negative_int(self.cost_cents_per_credit, "cost_cents_per_credit")
        if not isinstance(self.stop_on_validation_failure, bool):
            raise SchemaError("stop_on_validation_failure must be a boolean")

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "scenario_id": self.scenario_id,
            "seed": self.seed,
            "records": [record.to_dict() for record in self.records],
            "cost_cents_per_credit": self.cost_cents_per_credit,
            "stop_on_validation_failure": self.stop_on_validation_failure,
        }

    @property
    def input_sha256(self) -> str:
        return _canonical_sha256(self.to_dict())


@dataclass(frozen=True, slots=True)
class ActivityResult:
    scenario_id: str
    input_sha256: str
    seed: int
    status: str
    source_rows: int
    raw_rows: int
    normalized_rows: int
    duplicate_excess_rows: int
    validation_passed: bool | None
    processing_stopped: bool
    job_completed: bool
    downstream_cost_cents: int
    unique_cost_cents: int
    state_sha256: str
    schema_version: int = ACTIVITY_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.schema_version != ACTIVITY_SCHEMA_VERSION:
            raise SchemaError("unsupported activity result schema_version")
        if (
            not isinstance(self.scenario_id, str)
            or not ACTIVITY_ID_PATTERN.fullmatch(self.scenario_id)
        ):
            raise SchemaError("result scenario_id does not match the schema")
        if (
            not isinstance(self.input_sha256, str)
            or not SHA256_PATTERN.fullmatch(self.input_sha256)
        ):
            raise SchemaError("result input_sha256 must be a SHA-256 digest")
        if (
            not isinstance(self.seed, int)
            or isinstance(self.seed, bool)
            or self.seed < 1
        ):
            raise SchemaError("result seed must be a positive integer")
        if self.status not in ACTIVITY_STATUSES:
            raise SchemaError("result status does not match the schema")
        for field in (
            "source_rows",
            "raw_rows",
            "normalized_rows",
            "duplicate_excess_rows",
            "downstream_cost_cents",
            "unique_cost_cents",
        ):
            _non_negative_int(getattr(self, field), field)
        if self.validation_passed is not None and not isinstance(
            self.validation_passed, bool
        ):
            raise SchemaError("validation_passed must be a boolean or null")
        if not isinstance(self.processing_stopped, bool) or not isinstance(
            self.job_completed, bool
        ):
            raise SchemaError("activity outcome flags must be booleans")
        if (
            not isinstance(self.state_sha256, str)
            or not SHA256_PATTERN.fullmatch(self.state_sha256)
            or self.state_sha256 != _canonical_sha256(self._state_dict())
        ):
            raise SchemaError("state_sha256 does not match the activity state")

    def _state_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "scenario_id": self.scenario_id,
            "input_sha256": self.input_sha256,
            "seed": self.seed,
            "status": self.status,
            "source_rows": self.source_rows,
            "raw_rows": self.raw_rows,
            "normalized_rows": self.normalized_rows,
            "duplicate_excess_rows": self.duplicate_excess_rows,
            "validation_passed": self.validation_passed,
            "processing_stopped": self.processing_stopped,
            "job_completed": self.job_completed,
            "downstream_cost_cents": self.downstream_cost_cents,
            "unique_cost_cents": self.unique_cost_cents,
        }

    def to_dict(self) -> dict[str, Any]:
        return {**self._state_dict(), "state_sha256": self.state_sha256}

    @classmethod
    def build(cls, **state: Any) -> ActivityResult:
        state_with_version = {
            "schema_version": ACTIVITY_SCHEMA_VERSION,
            **state,
        }
        return cls(
            **state_with_version,
            state_sha256=_canonical_sha256(state_with_version),
        )

    @classmethod
    def from_dict(cls, value: Any) -> ActivityResult:
        expected = {
            "schema_version",
            "scenario_id",
            "input_sha256",
            "seed",
            "status",
            "source_rows",
            "raw_rows",
            "normalized_rows",
            "duplicate_excess_rows",
            "validation_passed",
            "processing_stopped",
            "job_completed",
            "downstream_cost_cents",
            "unique_cost_cents",
            "state_sha256",
        }
        if not isinstance(value, dict) or set(value) != expected:
            raise SchemaError("activity result fields do not match the schema")
        return cls(**value)


CODEX_ETL_ACTIVITY = EtlActivityInput(
    scenario_id="codex-etl-nonblocking-uniqueness",
    seed=7,
    records=(
        SyntheticUsageRecord(
            event_id="event-001",
            learner_alias="learner-a",
            model="model-alpha",
            credits=2,
        ),
        SyntheticUsageRecord(
            event_id="event-001",
            learner_alias="learner-a",
            model="model-alpha",
            credits=2,
        ),
        SyntheticUsageRecord(
            event_id="event-002",
            learner_alias="learner-b",
            model="model-beta",
            credits=3,
        ),
    ),
    cost_cents_per_credit=1,
    stop_on_validation_failure=False,
)


def render_activity(
    spec: EtlActivityInput,
    actions: tuple[str, ...],
) -> ActivityResult:
    """Return current deterministic state; no storage capability crosses this seam."""

    if actions not in {(), ("run-pipeline",)}:
        raise SchemaError("activity actions do not match the supported sequence")

    common = {
        "scenario_id": spec.scenario_id,
        "input_sha256": spec.input_sha256,
        "seed": spec.seed,
        "source_rows": len(spec.records),
    }
    if not actions:
        return ActivityResult.build(
            **common,
            status="ready",
            raw_rows=0,
            normalized_rows=0,
            duplicate_excess_rows=0,
            validation_passed=None,
            processing_stopped=False,
            job_completed=False,
            downstream_cost_cents=0,
            unique_cost_cents=0,
        )

    counts = Counter(record.event_id for record in spec.records)
    duplicate_excess = sum(count - 1 for count in counts.values())
    validation_passed = duplicate_excess == 0
    processing_stopped = (
        not validation_passed and spec.stop_on_validation_failure
    )
    downstream_cost = 0
    if not processing_stopped:
        downstream_cost = sum(
            record.credits * spec.cost_cents_per_credit
            for record in spec.records
        )
    unique_records = {
        record.event_id: record
        for record in reversed(spec.records)
    }
    unique_cost = sum(
        record.credits * spec.cost_cents_per_credit
        for record in unique_records.values()
    )
    return ActivityResult.build(
        **common,
        status="blocked" if processing_stopped else "complete",
        raw_rows=len(spec.records),
        normalized_rows=len(spec.records),
        duplicate_excess_rows=duplicate_excess,
        validation_passed=validation_passed,
        processing_stopped=processing_stopped,
        job_completed=not processing_stopped,
        downstream_cost_cents=downstream_cost,
        unique_cost_cents=unique_cost,
    )


def render_scenario(
    scenario_id: str,
    seed: int,
    input_revision_sha256: str,
    actions: tuple[str, ...],
) -> ActivityResult:
    """Resolve only the public built-in scenario and fail closed otherwise."""

    if scenario_id != CODEX_ETL_ACTIVITY.scenario_id:
        raise SchemaError("unknown synthetic scenario")
    if seed != CODEX_ETL_ACTIVITY.seed:
        raise SchemaError("scenario seed does not match the built-in revision")
    if input_revision_sha256 != CODEX_ETL_ACTIVITY.input_sha256:
        raise SchemaError("scenario input revision does not match the built-in input")
    return render_activity(CODEX_ETL_ACTIVITY, actions)
