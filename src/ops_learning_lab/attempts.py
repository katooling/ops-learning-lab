"""Strict canonical records for one evidence-centered Learner Attempt."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from hashlib import sha256
import json
import re
from typing import Any

from .activity import ActivityResult
from .domain import PACK_ID_PATTERN, SHA256_PATTERN, SchemaError


ATTEMPT_SCHEMA_VERSION = 1
ATTEMPT_ID_PATTERN = re.compile(r"^attempt-[0-9a-f]{20}$")
LESSON_ID_PATTERN = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
LOOP_STEPS = ("map", "predict", "try", "prove", "explain", "complete")
EVIDENCE_VERDICTS = frozenset({"supports", "rejects"})
ACTIVITY_ACTIONS = frozenset({"run-pipeline", "reset-scenario"})


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


def _identifier(value: Any, field: str) -> str:
    if not isinstance(value, str) or not LESSON_ID_PATTERN.fullmatch(value):
        raise SchemaError(f"{field} must be lowercase kebab-case")
    return value


def _confidence(value: Any, field: str) -> int:
    if (
        not isinstance(value, int)
        or isinstance(value, bool)
        or not 1 <= value <= 5
    ):
        raise SchemaError(f"{field} must be between 1 and 5")
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


@dataclass(frozen=True, slots=True)
class Prediction:
    choice_id: str
    confidence: int

    def __post_init__(self) -> None:
        _identifier(self.choice_id, "prediction choice_id")
        _confidence(self.confidence, "prediction confidence")

    def to_dict(self) -> dict[str, Any]:
        return {
            "choice_id": self.choice_id,
            "confidence": self.confidence,
        }

    @classmethod
    def from_dict(cls, value: Any) -> Prediction:
        if not isinstance(value, dict) or set(value) != {
            "choice_id",
            "confidence",
        }:
            raise SchemaError("prediction fields do not match the schema")
        return cls(**value)


@dataclass(frozen=True, slots=True)
class EvidenceDecision:
    card_id: str
    verdict: str

    def __post_init__(self) -> None:
        _identifier(self.card_id, "evidence card_id")
        if self.verdict not in EVIDENCE_VERDICTS:
            raise SchemaError("evidence verdict must be supports or rejects")

    def to_dict(self) -> dict[str, str]:
        return {"card_id": self.card_id, "verdict": self.verdict}

    @classmethod
    def from_dict(cls, value: Any) -> EvidenceDecision:
        if not isinstance(value, dict) or set(value) != {"card_id", "verdict"}:
            raise SchemaError("evidence decision fields do not match the schema")
        return cls(**value)


@dataclass(frozen=True, slots=True)
class Explanation:
    mechanism_choice_id: str
    text: str
    remaining_uncertainty: str
    confidence_after: int

    def __post_init__(self) -> None:
        _identifier(self.mechanism_choice_id, "mechanism_choice_id")
        _non_empty(self.text, "explanation text")
        _non_empty(self.remaining_uncertainty, "remaining_uncertainty")
        _confidence(self.confidence_after, "confidence_after")

    def to_dict(self) -> dict[str, Any]:
        return {
            "mechanism_choice_id": self.mechanism_choice_id,
            "text": self.text,
            "remaining_uncertainty": self.remaining_uncertainty,
            "confidence_after": self.confidence_after,
        }

    @classmethod
    def from_dict(cls, value: Any) -> Explanation:
        expected = {
            "mechanism_choice_id",
            "text",
            "remaining_uncertainty",
            "confidence_after",
        }
        if not isinstance(value, dict) or set(value) != expected:
            raise SchemaError("explanation fields do not match the schema")
        return cls(**value)


@dataclass(frozen=True, slots=True)
class RendererCheckpoint:
    """Only sanitized immutable input identity and deterministic renderer output."""

    scenario_id: str
    input_sha256: str
    seed: int
    effective_actions: tuple[str, ...]
    action_history: tuple[str, ...]
    result: ActivityResult

    def __post_init__(self) -> None:
        _identifier(self.scenario_id, "renderer scenario_id")
        if (
            not isinstance(self.input_sha256, str)
            or not SHA256_PATTERN.fullmatch(self.input_sha256)
        ):
            raise SchemaError("renderer input_sha256 must be a SHA-256 digest")
        if (
            not isinstance(self.seed, int)
            or isinstance(self.seed, bool)
            or self.seed < 1
        ):
            raise SchemaError("renderer seed must be a positive integer")
        if not isinstance(self.effective_actions, tuple) or not isinstance(
            self.action_history, tuple
        ):
            raise SchemaError("renderer actions must be tuples")
        if any(action not in ACTIVITY_ACTIONS for action in self.action_history):
            raise SchemaError("renderer action history does not match the schema")
        if self.effective_actions not in {(), ("run-pipeline",)}:
            raise SchemaError("renderer effective actions do not match the schema")
        effective: tuple[str, ...] = ()
        for action in self.action_history:
            if action == "reset-scenario":
                effective = ()
            elif effective:
                raise SchemaError("run-pipeline requires a scenario reset before retry")
            else:
                effective = ("run-pipeline",)
        if effective != self.effective_actions:
            raise SchemaError("renderer action history does not match effective actions")
        if not isinstance(self.result, ActivityResult):
            raise SchemaError("renderer result does not match the schema")
        if (
            self.result.scenario_id != self.scenario_id
            or self.result.input_sha256 != self.input_sha256
            or self.result.seed != self.seed
        ):
            raise SchemaError("renderer result does not match its checkpoint")
        expected_status = "ready" if not self.effective_actions else "complete"
        if self.result.status != expected_status:
            raise SchemaError("renderer result does not match effective actions")

    def to_dict(self) -> dict[str, Any]:
        return {
            "scenario_id": self.scenario_id,
            "input_sha256": self.input_sha256,
            "seed": self.seed,
            "effective_actions": list(self.effective_actions),
            "action_history": list(self.action_history),
            "result": self.result.to_dict(),
        }

    @classmethod
    def from_dict(cls, value: Any) -> RendererCheckpoint:
        expected = {
            "scenario_id",
            "input_sha256",
            "seed",
            "effective_actions",
            "action_history",
            "result",
        }
        if not isinstance(value, dict) or set(value) != expected:
            raise SchemaError("renderer checkpoint fields do not match the schema")
        if not isinstance(value["effective_actions"], list) or not isinstance(
            value["action_history"], list
        ):
            raise SchemaError("renderer checkpoint action fields must be lists")
        return cls(
            scenario_id=value["scenario_id"],
            input_sha256=value["input_sha256"],
            seed=value["seed"],
            effective_actions=tuple(value["effective_actions"]),
            action_history=tuple(value["action_history"]),
            result=ActivityResult.from_dict(value["result"]),
        )


@dataclass(frozen=True, slots=True)
class AttemptCheckpoint:
    attempt_id: str
    pack_id: str
    pack_version: int
    pack_sha256: str
    bundle_sha256: str
    lesson_id: str
    lesson_revision_sha256: str
    outcome_id: str
    outcome_revision_sha256: str
    started_at: str
    updated_at: str
    next_step: str
    prediction: Prediction | None
    renderer: RendererCheckpoint
    evidence: tuple[EvidenceDecision, ...]
    explanation: Explanation | None
    hints: tuple[str, ...]
    completed: bool
    checkpoint_sha256: str
    schema_version: int = ATTEMPT_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.schema_version != ATTEMPT_SCHEMA_VERSION:
            raise SchemaError("unsupported attempt schema_version")
        if (
            not isinstance(self.attempt_id, str)
            or not ATTEMPT_ID_PATTERN.fullmatch(self.attempt_id)
        ):
            raise SchemaError("attempt_id does not match the schema")
        if not isinstance(self.pack_id, str) or not PACK_ID_PATTERN.fullmatch(
            self.pack_id
        ):
            raise SchemaError("attempt pack_id does not match the schema")
        if (
            not isinstance(self.pack_version, int)
            or isinstance(self.pack_version, bool)
            or self.pack_version < 1
        ):
            raise SchemaError("attempt pack_version must be positive")
        for value, field in (
            (self.pack_sha256, "pack_sha256"),
            (self.bundle_sha256, "bundle_sha256"),
            (self.lesson_revision_sha256, "lesson_revision_sha256"),
            (self.outcome_revision_sha256, "outcome_revision_sha256"),
        ):
            if not isinstance(value, str) or not SHA256_PATTERN.fullmatch(value):
                raise SchemaError(f"attempt {field} must be a SHA-256 digest")
        _identifier(self.lesson_id, "lesson_id")
        _identifier(self.outcome_id, "outcome_id")
        started = _rfc3339(self.started_at, "started_at")
        updated = _rfc3339(self.updated_at, "updated_at")
        normalize = lambda value: datetime.fromisoformat(
            value[:-1] + "+00:00" if value.endswith("Z") else value
        )
        if normalize(updated) < normalize(started):
            raise SchemaError("updated_at cannot precede started_at")
        if self.next_step not in LOOP_STEPS:
            raise SchemaError("next_step does not match the schema")
        if self.prediction is not None and not isinstance(
            self.prediction, Prediction
        ):
            raise SchemaError("attempt prediction does not match the schema")
        if not isinstance(self.renderer, RendererCheckpoint):
            raise SchemaError("attempt renderer does not match the schema")
        if (
            not isinstance(self.evidence, tuple)
            or any(
                not isinstance(decision, EvidenceDecision)
                for decision in self.evidence
            )
        ):
            raise SchemaError("attempt evidence does not match the schema")
        if len({decision.card_id for decision in self.evidence}) != len(
            self.evidence
        ):
            raise SchemaError("each Evidence Card needs one learner decision")
        if self.explanation is not None and not isinstance(
            self.explanation, Explanation
        ):
            raise SchemaError("attempt explanation does not match the schema")
        if (
            not isinstance(self.hints, tuple)
            or any(not isinstance(hint, str) or not hint for hint in self.hints)
        ):
            raise SchemaError("attempt hints must be non-empty strings")
        if not isinstance(self.completed, bool):
            raise SchemaError("attempt completed must be a boolean")
        self._validate_step()
        if (
            not isinstance(self.checkpoint_sha256, str)
            or not SHA256_PATTERN.fullmatch(self.checkpoint_sha256)
            or self.checkpoint_sha256 != _canonical_sha256(self._content_dict())
        ):
            raise SchemaError("checkpoint_sha256 does not match attempt content")

    def _validate_step(self) -> None:
        index = LOOP_STEPS.index(self.next_step)
        if index < LOOP_STEPS.index("try") and self.prediction is not None:
            raise SchemaError("prediction cannot exist before the Try step")
        if index >= LOOP_STEPS.index("try") and self.prediction is None:
            raise SchemaError("Try and later steps require a prediction")
        if index < LOOP_STEPS.index("prove") and self.renderer.effective_actions:
            raise SchemaError("activity cannot run before the Prove step")
        if index >= LOOP_STEPS.index("prove") and not self.renderer.effective_actions:
            raise SchemaError("Prove and later steps require an activity result")
        if index < LOOP_STEPS.index("explain") and self.evidence:
            raise SchemaError("evidence cannot be decided before Explain")
        if index >= LOOP_STEPS.index("explain") and not self.evidence:
            raise SchemaError("Explain and later steps require evidence decisions")
        if self.next_step == "complete":
            if self.explanation is None or not self.completed:
                raise SchemaError("a complete attempt needs an explanation")
        elif self.explanation is not None or self.completed:
            raise SchemaError("only a complete attempt may contain an explanation")

    def _content_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "attempt_id": self.attempt_id,
            "pack_id": self.pack_id,
            "pack_version": self.pack_version,
            "pack_sha256": self.pack_sha256,
            "bundle_sha256": self.bundle_sha256,
            "lesson_id": self.lesson_id,
            "lesson_revision_sha256": self.lesson_revision_sha256,
            "outcome_id": self.outcome_id,
            "outcome_revision_sha256": self.outcome_revision_sha256,
            "started_at": self.started_at,
            "updated_at": self.updated_at,
            "next_step": self.next_step,
            "prediction": (
                self.prediction.to_dict()
                if self.prediction is not None
                else None
            ),
            "renderer": self.renderer.to_dict(),
            "evidence": [decision.to_dict() for decision in self.evidence],
            "explanation": (
                self.explanation.to_dict()
                if self.explanation is not None
                else None
            ),
            "hints": list(self.hints),
            "completed": self.completed,
        }

    def to_dict(self) -> dict[str, Any]:
        return {
            **self._content_dict(),
            "checkpoint_sha256": self.checkpoint_sha256,
        }

    def evolve(self, **changes: Any) -> AttemptCheckpoint:
        """Create the next content-addressed checkpoint without mutating history."""

        content = {
            "attempt_id": self.attempt_id,
            "pack_id": self.pack_id,
            "pack_version": self.pack_version,
            "pack_sha256": self.pack_sha256,
            "bundle_sha256": self.bundle_sha256,
            "lesson_id": self.lesson_id,
            "lesson_revision_sha256": self.lesson_revision_sha256,
            "outcome_id": self.outcome_id,
            "outcome_revision_sha256": self.outcome_revision_sha256,
            "started_at": self.started_at,
            "updated_at": self.updated_at,
            "next_step": self.next_step,
            "prediction": self.prediction,
            "renderer": self.renderer,
            "evidence": self.evidence,
            "explanation": self.explanation,
            "hints": self.hints,
            "completed": self.completed,
        }
        unknown = set(changes).difference(content)
        if unknown:
            raise SchemaError("attempt evolution fields do not match the schema")
        return self.build(**{**content, **changes})

    @classmethod
    def build(cls, **content: Any) -> AttemptCheckpoint:
        versioned = {"schema_version": ATTEMPT_SCHEMA_VERSION, **content}
        canonical = {
            **versioned,
            "prediction": (
                versioned["prediction"].to_dict()
                if versioned["prediction"] is not None
                else None
            ),
            "renderer": versioned["renderer"].to_dict(),
            "evidence": [
                decision.to_dict() for decision in versioned["evidence"]
            ],
            "explanation": (
                versioned["explanation"].to_dict()
                if versioned["explanation"] is not None
                else None
            ),
            "hints": list(versioned["hints"]),
        }
        return cls(
            **versioned,
            checkpoint_sha256=_canonical_sha256(canonical),
        )

    @classmethod
    def from_dict(cls, value: Any) -> AttemptCheckpoint:
        expected = {
            "schema_version",
            "attempt_id",
            "pack_id",
            "pack_version",
            "pack_sha256",
            "bundle_sha256",
            "lesson_id",
            "lesson_revision_sha256",
            "outcome_id",
            "outcome_revision_sha256",
            "started_at",
            "updated_at",
            "next_step",
            "prediction",
            "renderer",
            "evidence",
            "explanation",
            "hints",
            "completed",
            "checkpoint_sha256",
        }
        if not isinstance(value, dict) or set(value) != expected:
            raise SchemaError("attempt checkpoint fields do not match the schema")
        if not isinstance(value["evidence"], list) or not isinstance(
            value["hints"], list
        ):
            raise SchemaError("attempt checkpoint collections must be lists")
        return cls(
            schema_version=value["schema_version"],
            attempt_id=value["attempt_id"],
            pack_id=value["pack_id"],
            pack_version=value["pack_version"],
            pack_sha256=value["pack_sha256"],
            bundle_sha256=value["bundle_sha256"],
            lesson_id=value["lesson_id"],
            lesson_revision_sha256=value["lesson_revision_sha256"],
            outcome_id=value["outcome_id"],
            outcome_revision_sha256=value["outcome_revision_sha256"],
            started_at=value["started_at"],
            updated_at=value["updated_at"],
            next_step=value["next_step"],
            prediction=(
                Prediction.from_dict(value["prediction"])
                if value["prediction"] is not None
                else None
            ),
            renderer=RendererCheckpoint.from_dict(value["renderer"]),
            evidence=tuple(
                EvidenceDecision.from_dict(item)
                for item in value["evidence"]
            ),
            explanation=(
                Explanation.from_dict(value["explanation"])
                if value["explanation"] is not None
                else None
            ),
            hints=tuple(value["hints"]),
            completed=value["completed"],
            checkpoint_sha256=value["checkpoint_sha256"],
        )
