"""Deterministic evaluation and derived Mastery State."""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import json
from typing import Any, Iterable

from .activity import render_scenario
from .attempts import AttemptCheckpoint
from .domain import SHA256_PATTERN, SchemaError
from .learning_bundle import LearningPackBundle, LessonBlueprint


EVALUATION_SCHEMA_VERSION = 1
ATTEMPT_RECORD_SCHEMA_VERSION = 1
MASTERY_STATES = frozenset(
    {"captured", "introduced", "demonstrated", "retained"}
)


def _canonical_sha256(value: dict[str, Any]) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return sha256(encoded).hexdigest()


@dataclass(frozen=True, slots=True)
class AttemptEvaluation:
    attempt_id: str
    checkpoint_sha256: str
    outcome_id: str
    outcome_revision_sha256: str
    qualifies: bool
    mastery_state: str
    feedback: tuple[str, ...]
    evaluation_sha256: str
    schema_version: int = EVALUATION_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.schema_version != EVALUATION_SCHEMA_VERSION:
            raise SchemaError("unsupported evaluation schema_version")
        if not isinstance(self.qualifies, bool):
            raise SchemaError("evaluation qualifies must be a boolean")
        if self.mastery_state not in {"introduced", "demonstrated"}:
            raise SchemaError("evaluation mastery_state does not match the schema")
        if (self.mastery_state == "demonstrated") != self.qualifies:
            raise SchemaError("evaluation mastery_state contradicts qualification")
        if (
            not isinstance(self.feedback, tuple)
            or any(
                not isinstance(reason, str) or not reason
                for reason in self.feedback
            )
            or len(set(self.feedback)) != len(self.feedback)
        ):
            raise SchemaError("evaluation feedback must be unique reason codes")
        if self.qualifies and self.feedback:
            raise SchemaError("a qualifying evaluation cannot contain feedback")
        for digest, field in (
            (self.checkpoint_sha256, "checkpoint_sha256"),
            (self.outcome_revision_sha256, "outcome_revision_sha256"),
            (self.evaluation_sha256, "evaluation_sha256"),
        ):
            if not isinstance(digest, str) or not SHA256_PATTERN.fullmatch(digest):
                raise SchemaError(f"evaluation {field} must be a SHA-256 digest")
        if self.evaluation_sha256 != _canonical_sha256(self._content_dict()):
            raise SchemaError("evaluation_sha256 does not match evaluation content")

    def _content_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "attempt_id": self.attempt_id,
            "checkpoint_sha256": self.checkpoint_sha256,
            "outcome_id": self.outcome_id,
            "outcome_revision_sha256": self.outcome_revision_sha256,
            "qualifies": self.qualifies,
            "mastery_state": self.mastery_state,
            "feedback": list(self.feedback),
        }

    def to_dict(self) -> dict[str, Any]:
        return {
            **self._content_dict(),
            "evaluation_sha256": self.evaluation_sha256,
        }

    @classmethod
    def from_dict(cls, value: Any) -> AttemptEvaluation:
        expected = {
            "schema_version",
            "attempt_id",
            "checkpoint_sha256",
            "outcome_id",
            "outcome_revision_sha256",
            "qualifies",
            "mastery_state",
            "feedback",
            "evaluation_sha256",
        }
        if not isinstance(value, dict) or set(value) != expected:
            raise SchemaError("evaluation fields do not match the schema")
        if not isinstance(value["feedback"], list):
            raise SchemaError("evaluation feedback must be a list")
        return cls(
            schema_version=value["schema_version"],
            attempt_id=value["attempt_id"],
            checkpoint_sha256=value["checkpoint_sha256"],
            outcome_id=value["outcome_id"],
            outcome_revision_sha256=value["outcome_revision_sha256"],
            qualifies=value["qualifies"],
            mastery_state=value["mastery_state"],
            feedback=tuple(value["feedback"]),
            evaluation_sha256=value["evaluation_sha256"],
        )

    @classmethod
    def build(
        cls,
        attempt: AttemptCheckpoint,
        feedback: tuple[str, ...],
    ) -> AttemptEvaluation:
        content = {
            "schema_version": EVALUATION_SCHEMA_VERSION,
            "attempt_id": attempt.attempt_id,
            "checkpoint_sha256": attempt.checkpoint_sha256,
            "outcome_id": attempt.outcome_id,
            "outcome_revision_sha256": attempt.outcome_revision_sha256,
            "qualifies": not feedback,
            "mastery_state": "demonstrated" if not feedback else "introduced",
            "feedback": list(feedback),
        }
        return cls(
            attempt_id=attempt.attempt_id,
            checkpoint_sha256=attempt.checkpoint_sha256,
            outcome_id=attempt.outcome_id,
            outcome_revision_sha256=attempt.outcome_revision_sha256,
            qualifies=not feedback,
            mastery_state=content["mastery_state"],
            feedback=feedback,
            evaluation_sha256=_canonical_sha256(content),
        )


@dataclass(frozen=True, slots=True)
class LearnerAttemptRecord:
    """Serializable handoff: one checkpoint plus its terminal evaluation."""

    checkpoint: AttemptCheckpoint
    evaluation: AttemptEvaluation | None
    record_sha256: str
    schema_version: int = ATTEMPT_RECORD_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.schema_version != ATTEMPT_RECORD_SCHEMA_VERSION:
            raise SchemaError("unsupported Learner Attempt record schema_version")
        if not isinstance(self.checkpoint, AttemptCheckpoint):
            raise SchemaError("Learner Attempt record checkpoint is invalid")
        if self.checkpoint.completed:
            if not isinstance(self.evaluation, AttemptEvaluation):
                raise SchemaError(
                    "completed Learner Attempt record requires an evaluation"
                )
            if (
                self.evaluation.attempt_id != self.checkpoint.attempt_id
                or self.evaluation.checkpoint_sha256
                != self.checkpoint.checkpoint_sha256
                or self.evaluation.outcome_id != self.checkpoint.outcome_id
                or self.evaluation.outcome_revision_sha256
                != self.checkpoint.outcome_revision_sha256
            ):
                raise SchemaError(
                    "Learner Attempt evaluation does not match its checkpoint"
                )
        elif self.evaluation is not None:
            raise SchemaError(
                "incomplete Learner Attempt record cannot contain an evaluation"
            )
        if (
            not isinstance(self.record_sha256, str)
            or not SHA256_PATTERN.fullmatch(self.record_sha256)
            or self.record_sha256 != _canonical_sha256(self._content_dict())
        ):
            raise SchemaError(
                "record_sha256 does not match Learner Attempt record content"
            )

    def _content_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "checkpoint": self.checkpoint.to_dict(),
            "evaluation": (
                self.evaluation.to_dict()
                if self.evaluation is not None
                else None
            ),
        }

    def to_dict(self) -> dict[str, Any]:
        return {**self._content_dict(), "record_sha256": self.record_sha256}

    @classmethod
    def build(
        cls,
        checkpoint: AttemptCheckpoint,
        evaluation: AttemptEvaluation | None,
    ) -> LearnerAttemptRecord:
        content = {
            "schema_version": ATTEMPT_RECORD_SCHEMA_VERSION,
            "checkpoint": checkpoint.to_dict(),
            "evaluation": (
                evaluation.to_dict() if evaluation is not None else None
            ),
        }
        return cls(
            checkpoint=checkpoint,
            evaluation=evaluation,
            record_sha256=_canonical_sha256(content),
        )

    @classmethod
    def from_dict(cls, value: Any) -> LearnerAttemptRecord:
        expected = {
            "schema_version",
            "checkpoint",
            "evaluation",
            "record_sha256",
        }
        if not isinstance(value, dict) or set(value) != expected:
            raise SchemaError(
                "Learner Attempt record fields do not match the schema"
            )
        return cls(
            schema_version=value["schema_version"],
            checkpoint=AttemptCheckpoint.from_dict(value["checkpoint"]),
            evaluation=(
                AttemptEvaluation.from_dict(value["evaluation"])
                if value["evaluation"] is not None
                else None
            ),
            record_sha256=value["record_sha256"],
        )


@dataclass(frozen=True, slots=True)
class MasteryProjection:
    state: str
    earned_by_attempt_id: str | None

    def __post_init__(self) -> None:
        if self.state not in MASTERY_STATES:
            raise SchemaError("Mastery State does not match the schema")
        if self.state == "captured":
            if self.earned_by_attempt_id is not None:
                raise SchemaError("Captured mastery cannot name an attempt")
        elif not isinstance(self.earned_by_attempt_id, str):
            raise SchemaError("earned mastery must name its Learner Attempt")


def evaluate_attempt(
    bundle: LearningPackBundle,
    attempt: AttemptCheckpoint,
) -> AttemptEvaluation:
    """Evaluate through the accepted bundle; renderer output has no authority."""

    if not isinstance(bundle, LearningPackBundle):
        raise SchemaError("attempt evaluation requires a Learning Pack Bundle")
    if not isinstance(attempt, AttemptCheckpoint):
        raise SchemaError("attempt evaluation requires an Attempt Checkpoint")
    lesson = _lesson(bundle, attempt.lesson_id)
    feedback: list[str] = []

    identity_matches = (
        attempt.pack_id == bundle.pack_id
        and attempt.pack_version == bundle.pack_version
        and attempt.pack_sha256 == bundle.accepted_snapshot_sha256
        and attempt.bundle_sha256 == bundle.bundle_sha256
        and attempt.lesson_revision_sha256 == lesson.lesson_revision_sha256
        and attempt.outcome_id == lesson.outcome.outcome_id
        and attempt.outcome_revision_sha256
        == lesson.outcome.outcome_revision_sha256
    )
    if not identity_matches:
        feedback.append("identity-mismatch")
    if attempt.next_step != "complete" or not attempt.completed:
        feedback.append("attempt-incomplete")
    if (
        attempt.prediction is None
        or attempt.prediction.choice_id != lesson.prediction.expected_choice_id
    ):
        feedback.append("prediction-incorrect")

    try:
        expected_result = render_scenario(
            lesson.activity.scenario_id,
            lesson.activity.seed,
            lesson.activity.input_revision_sha256,
            attempt.renderer.effective_actions,
        )
    except SchemaError:
        expected_result = None
    if (
        attempt.renderer.scenario_id != lesson.activity.scenario_id
        or attempt.renderer.input_sha256
        != lesson.activity.input_revision_sha256
        or attempt.renderer.seed != lesson.activity.seed
        or attempt.renderer.result != expected_result
    ):
        feedback.append("renderer-result-mismatch")

    expected_evidence = {
        evidence_id: "supports"
        for evidence_id in lesson.evidence.required_support
    }
    expected_evidence.update(
        {
            evidence_id: "rejects"
            for evidence_id in lesson.evidence.required_reject
        }
    )
    actual_evidence = {
        decision.card_id: decision.verdict
        for decision in attempt.evidence
    }
    if actual_evidence != expected_evidence:
        feedback.append("evidence-insufficient")

    explanation = attempt.explanation
    if (
        explanation is None
        or explanation.mechanism_choice_id
        != lesson.explanation.qualification.expected_choice_id
    ):
        feedback.append("explanation-choice-incorrect")
    if (
        explanation is None
        or len(explanation.text.strip())
        < lesson.explanation.minimum_characters
    ):
        feedback.append("explanation-too-short")

    return AttemptEvaluation.build(attempt, tuple(feedback))


def derive_mastery(
    bundle: LearningPackBundle,
    attempts: Iterable[AttemptCheckpoint],
) -> MasteryProjection:
    """Project Mastery State from immutable checkpoints; no second authority."""

    lesson = bundle.lessons[0]
    relevant = sorted(
        (
            attempt
            for attempt in attempts
            if _matches_lesson(bundle, lesson, attempt)
        ),
        key=lambda attempt: (attempt.started_at, attempt.attempt_id),
    )
    for attempt in relevant:
        if attempt.completed and evaluate_attempt(bundle, attempt).qualifies:
            return MasteryProjection("demonstrated", attempt.attempt_id)
    for attempt in relevant:
        if attempt.next_step != "map":
            return MasteryProjection("introduced", attempt.attempt_id)
    return MasteryProjection("captured", None)


def _lesson(bundle: LearningPackBundle, lesson_id: str) -> LessonBlueprint:
    matches = tuple(
        lesson for lesson in bundle.lessons if lesson.lesson_id == lesson_id
    )
    if len(matches) != 1:
        raise SchemaError("attempt lesson does not exist in the accepted bundle")
    return matches[0]


def _matches_lesson(
    bundle: LearningPackBundle,
    lesson: LessonBlueprint,
    attempt: AttemptCheckpoint,
) -> bool:
    return (
        attempt.pack_id == bundle.pack_id
        and attempt.pack_version == bundle.pack_version
        and attempt.pack_sha256 == bundle.accepted_snapshot_sha256
        and attempt.bundle_sha256 == bundle.bundle_sha256
        and attempt.lesson_id == lesson.lesson_id
        and attempt.lesson_revision_sha256 == lesson.lesson_revision_sha256
        and attempt.outcome_id == lesson.outcome.outcome_id
        and attempt.outcome_revision_sha256
        == lesson.outcome.outcome_revision_sha256
    )
