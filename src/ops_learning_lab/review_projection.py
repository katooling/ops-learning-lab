"""Pure review scheduling and retained-mastery projection."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from .attempts import AttemptCheckpoint
from .learner_errors import LearnerStateError
from .learner_state import LearnerHistory
from .learning import MasteryProjection, derive_mastery
from .learning_bundle import LearningPackBundle


@dataclass(frozen=True, slots=True)
class ReviewProjection:
    status: str
    due_at: str | None
    demonstrated_by_attempt_id: str | None
    latest_review_attempt_id: str | None

    def __post_init__(self) -> None:
        if self.status not in {
            "not-scheduled",
            "scheduled",
            "due",
            "in-progress",
            "retry-scheduled",
            "retained",
        }:
            raise LearnerStateError("review status is invalid")
        if self.status == "not-scheduled":
            if any(
                value is not None
                for value in (
                    self.due_at,
                    self.demonstrated_by_attempt_id,
                    self.latest_review_attempt_id,
                )
            ):
                raise LearnerStateError("unscheduled review cannot name artifacts")
        elif self.status == "retained":
            if (
                self.due_at is not None
                or self.demonstrated_by_attempt_id is None
                or self.latest_review_attempt_id is None
            ):
                raise LearnerStateError("retained review artifacts are incomplete")
        elif self.due_at is None or self.demonstrated_by_attempt_id is None:
            raise LearnerStateError("scheduled review artifacts are incomplete")


@dataclass(frozen=True, slots=True)
class LearningStateProjection:
    mastery: MasteryProjection
    review: ReviewProjection


def project_learning_state(
    bundle: LearningPackBundle,
    history: LearnerHistory,
    now: str,
) -> LearningStateProjection:
    """Derive mastery and review timing only from immutable event history."""

    current_time = _parse_time(now)
    matching = tuple(
        entry
        for entry in history.attempts
        if _matches_bundle(entry.checkpoint, bundle)
    )
    baseline = derive_mastery(
        bundle,
        tuple(entry.checkpoint for entry in matching),
    )
    demonstrations = tuple(
        entry
        for entry in matching
        if entry.status == "completed"
        and entry.attempt_kind == "learning"
        and entry.completed_record is not None
        and entry.completed_record.evaluation is not None
        and entry.completed_record.evaluation.qualifies
    )
    if not demonstrations:
        return LearningStateProjection(
            baseline,
            ReviewProjection("not-scheduled", None, None, None),
        )

    demonstration = demonstrations[0]
    assert demonstration.completed_at is not None
    due = _parse_time(demonstration.completed_at) + timedelta(days=7)
    reviews = tuple(
        entry
        for entry in matching
        if entry.attempt_kind == "review"
        and entry.review_of_attempt_id
        == demonstration.checkpoint.attempt_id
    )
    latest_review_id: str | None = None
    retry = False
    for review in reviews:
        latest_review_id = review.checkpoint.attempt_id
        if review.status == "active":
            return LearningStateProjection(
                MasteryProjection(
                    "demonstrated",
                    demonstration.checkpoint.attempt_id,
                ),
                ReviewProjection(
                    "in-progress",
                    _format_time(due),
                    demonstration.checkpoint.attempt_id,
                    latest_review_id,
                ),
            )
        if (
            review.status == "completed"
            and review.completed_record is not None
            and review.completed_record.evaluation is not None
        ):
            if (
                review.completed_record.evaluation.qualifies
                and _parse_time(review.checkpoint.started_at) >= due
            ):
                return LearningStateProjection(
                    MasteryProjection(
                        "retained",
                        review.checkpoint.attempt_id,
                    ),
                    ReviewProjection(
                        "retained",
                        None,
                        demonstration.checkpoint.attempt_id,
                        review.checkpoint.attempt_id,
                    ),
                )
            assert review.completed_at is not None
            due = _parse_time(review.completed_at) + timedelta(days=1)
            retry = True

    if current_time >= due:
        status = "due"
    else:
        status = "retry-scheduled" if retry else "scheduled"
    return LearningStateProjection(
        MasteryProjection(
            "demonstrated",
            demonstration.checkpoint.attempt_id,
        ),
        ReviewProjection(
            status,
            _format_time(due),
            demonstration.checkpoint.attempt_id,
            latest_review_id,
        ),
    )


def _matches_bundle(
    checkpoint: AttemptCheckpoint,
    bundle: LearningPackBundle,
) -> bool:
    lesson = bundle.lessons[0]
    return (
        checkpoint.pack_id == bundle.pack_id
        and checkpoint.pack_version == bundle.pack_version
        and checkpoint.pack_sha256 == bundle.accepted_snapshot_sha256
        and checkpoint.bundle_sha256 == bundle.bundle_sha256
        and checkpoint.lesson_id == lesson.lesson_id
        and checkpoint.lesson_revision_sha256
        == lesson.lesson_revision_sha256
        and checkpoint.outcome_id == lesson.outcome.outcome_id
        and checkpoint.outcome_revision_sha256
        == lesson.outcome.outcome_revision_sha256
    )


def _parse_time(value: str) -> datetime:
    normalized = value[:-1] + "+00:00" if value.endswith("Z") else value
    try:
        parsed = datetime.fromisoformat(normalized)
    except (TypeError, ValueError) as exc:
        raise LearnerStateError("projection time is invalid") from exc
    if parsed.tzinfo is None:
        raise LearnerStateError("projection time needs a timezone")
    return parsed.astimezone(timezone.utc)


def _format_time(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
