"""Application seam for one ordered evidence-centered Learning Loop."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import secrets
from threading import RLock
from typing import Any, Callable, Iterable, Protocol

from .activity import render_scenario
from .attempts import (
    AttemptCheckpoint,
    EvidenceDecision,
    Explanation,
    PredictionResponse,
    RendererCheckpoint,
)
from .bundle_repository import BundleRepository
from .domain import SchemaError
from .learning import (
    AttemptEvaluation,
    LearnerAttemptRecord,
    MasteryProjection,
    derive_mastery,
    evaluate_attempt,
)
from .learner_state import (
    AttemptHistoryEntry,
    LearnerHistory,
    LearnerStateError,
)
from .learning_bundle import LearningPackBundle, LessonBlueprint
from .lesson_content import build_codex_etl_bundle
from .review_projection import (
    LearningStateProjection,
    ReviewProjection,
    project_learning_state,
)


class LearningError(RuntimeError):
    """Raised when a learner action violates the ordered Learning Loop."""


@dataclass(frozen=True, slots=True)
class LearningView:
    bundle: LearningPackBundle
    lesson: LessonBlueprint
    attempt: AttemptCheckpoint | None
    evaluation: AttemptEvaluation | None
    record: LearnerAttemptRecord | None
    mastery: MasteryProjection
    review: ReviewProjection
    history: tuple[AttemptHistoryEntry, ...]


class AttemptStore(Protocol):
    def get(self, attempt_id: str) -> AttemptCheckpoint | None: ...

    def list(self) -> tuple[AttemptCheckpoint, ...]: ...

    def save(
        self,
        checkpoint: AttemptCheckpoint,
        *,
        expected_checkpoint_sha256: str | None,
        **context: Any,
    ) -> AttemptCheckpoint: ...

    def complete(
        self,
        record: LearnerAttemptRecord,
        *,
        expected_checkpoint_sha256: str,
        **context: Any,
    ) -> AttemptCheckpoint: ...

    def restart(
        self,
        previous_attempt_id: str,
        checkpoint: AttemptCheckpoint,
        *,
        expected_checkpoint_sha256: str,
        **context: Any,
    ) -> AttemptCheckpoint: ...


class InMemoryAttemptStore:
    """Issue-4 session adapter; Wave 4 replaces it with durable event history."""

    def __init__(self) -> None:
        self._attempts: dict[str, AttemptCheckpoint] = {}
        self._lock = RLock()

    def get(self, attempt_id: str) -> AttemptCheckpoint | None:
        with self._lock:
            return self._attempts.get(attempt_id)

    def list(self) -> tuple[AttemptCheckpoint, ...]:
        with self._lock:
            return tuple(
                self._attempts[key] for key in sorted(self._attempts)
            )

    def save(
        self,
        checkpoint: AttemptCheckpoint,
        *,
        expected_checkpoint_sha256: str | None,
        **_: object,
    ) -> AttemptCheckpoint:
        with self._lock:
            existing = self._attempts.get(checkpoint.attempt_id)
            if existing == checkpoint:
                return checkpoint
            if existing is None:
                if expected_checkpoint_sha256 is not None:
                    raise LearningError("Learner Attempt checkpoint is stale")
            else:
                if existing.completed:
                    raise LearningError("completed Learner Attempt is immutable")
                if expected_checkpoint_sha256 != existing.checkpoint_sha256:
                    raise LearningError("Learner Attempt checkpoint is stale")
            self._attempts[checkpoint.attempt_id] = checkpoint
            return checkpoint

    def complete(
        self,
        record: LearnerAttemptRecord,
        *,
        expected_checkpoint_sha256: str,
        **_: object,
    ) -> AttemptCheckpoint:
        return self.save(
            record.checkpoint,
            expected_checkpoint_sha256=expected_checkpoint_sha256,
        )

    def restart(
        self,
        previous_attempt_id: str,
        checkpoint: AttemptCheckpoint,
        *,
        expected_checkpoint_sha256: str,
        **_: object,
    ) -> AttemptCheckpoint:
        with self._lock:
            previous = self._attempts.get(previous_attempt_id)
            if previous is None or previous.completed:
                raise LearningError("Learner Attempt is not active")
            if previous.checkpoint_sha256 != expected_checkpoint_sha256:
                raise LearningError("Learner Attempt checkpoint is stale")
            if checkpoint.attempt_id in self._attempts:
                raise LearningError("replacement Learner Attempt already exists")
            self._attempts[checkpoint.attempt_id] = checkpoint
            return checkpoint


class LearningService:
    """Own ordering, canonical attempts, evaluation, and derived mastery."""

    def __init__(
        self,
        packs,
        bundles: BundleRepository,
        attempts: AttemptStore,
        *,
        clock: Callable[[], str] | None = None,
        attempt_id_factory: Callable[[], str] | None = None,
    ) -> None:
        self.packs = packs
        self.bundles = bundles
        self.attempts = attempts
        self.clock = clock or _utc_now
        self.attempt_id_factory = attempt_id_factory or (
            lambda: f"attempt-{secrets.token_hex(10)}"
        )
        self._review_lock = RLock()

    def open_lesson(self, pack_id: str, lesson_id: str) -> LearningView:
        """Read current accepted input without persisting a bundle or attempt."""

        bundle = self._current_bundle(pack_id)
        lesson = _lesson(bundle, lesson_id)
        projection, history = self._state(bundle)
        review = self._review_for_route(
            pack_id,
            lesson_id,
            history,
            projection.review,
        )
        return LearningView(
            bundle=bundle,
            lesson=lesson,
            attempt=None,
            evaluation=None,
            record=None,
            mastery=projection.mastery,
            review=review,
            history=history,
        )

    def available_lessons(
        self,
        pack_id: str,
    ) -> tuple[LessonBlueprint, ...]:
        """Discover optional lesson content without treating absence as corruption."""

        snapshot = self.packs.snapshot(pack_id)
        if snapshot is None:
            return ()
        try:
            return build_codex_etl_bundle(snapshot).lessons
        except SchemaError:
            return ()

    def start(self, pack_id: str, lesson_id: str) -> LearningView:
        return self._start(pack_id, lesson_id, "learning", None)

    def start_review(
        self,
        pack_id: str,
        lesson_id: str,
        demonstration_attempt_id: str,
        bundle_sha256: str,
    ) -> LearningView:
        with self._review_lock:
            history = self._durable_history()
            demonstration = history.get(demonstration_attempt_id)
            if (
                demonstration is None
                or demonstration.checkpoint.pack_id != pack_id
                or demonstration.checkpoint.lesson_id != lesson_id
                or demonstration.checkpoint.bundle_sha256 != bundle_sha256
            ):
                raise LearningError(
                    "Review demonstration does not match this lesson"
                )
            bundle = self.bundles.snapshot(bundle_sha256)
            if bundle is None:
                raise LearnerStateError(
                    "learner history references a missing review bundle snapshot"
                )
            lesson = _lesson(bundle, lesson_id)
            projection = project_learning_state(
                bundle,
                history,
                self.clock(),
            )
            if projection.review.status != "due":
                raise LearningError("Review is not due yet")
            source = projection.review.demonstrated_by_attempt_id
            if source != demonstration_attempt_id:
                raise LearningError(
                    "Review demonstration is not the due artifact"
                )
            return self._start_from_bundle(
                bundle,
                lesson.lesson_id,
                "review",
                source,
            )

    def _start(
        self,
        pack_id: str,
        lesson_id: str,
        attempt_kind: str,
        review_of_attempt_id: str | None,
    ) -> LearningView:
        bundle = self._current_bundle(pack_id)
        return self._start_from_bundle(
            bundle,
            lesson_id,
            attempt_kind,
            review_of_attempt_id,
        )

    def _start_from_bundle(
        self,
        bundle: LearningPackBundle,
        lesson_id: str,
        attempt_kind: str,
        review_of_attempt_id: str | None,
    ) -> LearningView:
        lesson = _lesson(bundle, lesson_id)
        self.bundles.save(bundle)
        now = self.clock()
        result = render_scenario(
            lesson.activity.scenario_id,
            lesson.activity.seed,
            lesson.activity.input_revision_sha256,
            (),
        )
        attempt = AttemptCheckpoint.build(
            attempt_id=self.attempt_id_factory(),
            pack_id=bundle.pack_id,
            pack_version=bundle.pack_version,
            pack_sha256=bundle.accepted_snapshot_sha256,
            bundle_sha256=bundle.bundle_sha256,
            lesson_id=lesson.lesson_id,
            lesson_revision_sha256=lesson.lesson_revision_sha256,
            outcome_id=lesson.outcome.outcome_id,
            outcome_revision_sha256=lesson.outcome.outcome_revision_sha256,
            started_at=now,
            updated_at=now,
            next_step="map",
            prediction=None,
            renderer=RendererCheckpoint(
                scenario_id=lesson.activity.scenario_id,
                input_sha256=lesson.activity.input_revision_sha256,
                seed=lesson.activity.seed,
                effective_actions=(),
                action_history=(),
                result=result,
            ),
            evidence=(),
            explanation=None,
            hints=(),
            completed=False,
        )
        self.attempts.save(
            attempt,
            expected_checkpoint_sha256=None,
            attempt_kind=attempt_kind,
            review_of_attempt_id=review_of_attempt_id,
            occurred_at=now,
        )
        return self._view(attempt)

    def view(self, attempt_id: str) -> LearningView:
        attempt = self.attempts.get(attempt_id)
        if attempt is None:
            raise LearningError("Learner Attempt was not found")
        return self._view(attempt)

    def advance_map(self, attempt_id: str) -> LearningView:
        previous = self._require_step(attempt_id, "map", "Map")
        return self._save(previous, previous.evolve(
            updated_at=self.clock(),
            next_step="predict",
        ))

    def predict(
        self,
        attempt_id: str,
        choice_id: str,
        confidence: int,
    ) -> LearningView:
        previous = self._require_step(attempt_id, "predict", "Predict")
        lesson = self._view(previous).lesson
        if choice_id not in {
            choice.choice_id for choice in lesson.prediction.choices
        }:
            raise LearningError("prediction choice does not belong to this lesson")
        return self._save(previous, previous.evolve(
            updated_at=self.clock(),
            next_step="try",
            prediction=PredictionResponse(choice_id, confidence),
        ))

    def run_scenario(self, attempt_id: str) -> LearningView:
        previous = self._require_step(attempt_id, "try", "Try")
        lesson = self._view(previous).lesson
        history = (*previous.renderer.action_history, "run-pipeline")
        result = render_scenario(
            lesson.activity.scenario_id,
            lesson.activity.seed,
            lesson.activity.input_revision_sha256,
            ("run-pipeline",),
        )
        renderer = RendererCheckpoint(
            scenario_id=lesson.activity.scenario_id,
            input_sha256=lesson.activity.input_revision_sha256,
            seed=lesson.activity.seed,
            effective_actions=("run-pipeline",),
            action_history=history,
            result=result,
        )
        return self._save(previous, previous.evolve(
            updated_at=self.clock(),
            next_step="prove",
            renderer=renderer,
        ))

    def reset_scenario(self, attempt_id: str) -> LearningView:
        previous = self.view(attempt_id).attempt
        if previous is None or previous.next_step not in {"prove", "explain"}:
            raise LearningError("Scenario reset is available after Try")
        lesson = self._view(previous).lesson
        history = (*previous.renderer.action_history, "reset-scenario")
        result = render_scenario(
            lesson.activity.scenario_id,
            lesson.activity.seed,
            lesson.activity.input_revision_sha256,
            (),
        )
        renderer = RendererCheckpoint(
            scenario_id=lesson.activity.scenario_id,
            input_sha256=lesson.activity.input_revision_sha256,
            seed=lesson.activity.seed,
            effective_actions=(),
            action_history=history,
            result=result,
        )
        return self._save(previous, previous.evolve(
            updated_at=self.clock(),
            next_step="try",
            renderer=renderer,
            evidence=(),
            explanation=None,
            completed=False,
        ))

    def restart_attempt(self, attempt_id: str) -> LearningView:
        previous = self.view(attempt_id).attempt
        if previous is None or previous.completed:
            raise LearningError("only an active Learner Attempt can restart")
        bundle = self.bundles.snapshot(previous.bundle_sha256)
        if bundle is None:
            raise LearningError("Learner Attempt bundle snapshot is missing")
        lesson = _lesson(bundle, previous.lesson_id)
        now = self.clock()
        replacement = AttemptCheckpoint.build(
            attempt_id=self.attempt_id_factory(),
            pack_id=previous.pack_id,
            pack_version=previous.pack_version,
            pack_sha256=previous.pack_sha256,
            bundle_sha256=previous.bundle_sha256,
            lesson_id=previous.lesson_id,
            lesson_revision_sha256=previous.lesson_revision_sha256,
            outcome_id=previous.outcome_id,
            outcome_revision_sha256=previous.outcome_revision_sha256,
            started_at=now,
            updated_at=now,
            next_step="map",
            prediction=None,
            renderer=RendererCheckpoint(
                scenario_id=lesson.activity.scenario_id,
                input_sha256=lesson.activity.input_revision_sha256,
                seed=lesson.activity.seed,
                effective_actions=(),
                action_history=(),
                result=render_scenario(
                    lesson.activity.scenario_id,
                    lesson.activity.seed,
                    lesson.activity.input_revision_sha256,
                    (),
                ),
            ),
            evidence=(),
            explanation=None,
            hints=(),
            completed=False,
        )
        saved = self.attempts.restart(
            attempt_id,
            replacement,
            expected_checkpoint_sha256=previous.checkpoint_sha256,
            occurred_at=now,
        )
        return self._view(saved)

    def prove(
        self,
        attempt_id: str,
        decisions: tuple[EvidenceDecision, ...],
    ) -> LearningView:
        previous = self._require_step(attempt_id, "prove", "Prove")
        lesson = self._view(previous).lesson
        if {decision.card_id for decision in decisions} != {
            card.evidence_id for card in lesson.evidence.cards
        }:
            raise LearningError("every Evidence Card needs one decision")
        return self._save(previous, previous.evolve(
            updated_at=self.clock(),
            next_step="explain",
            evidence=decisions,
        ))

    def explain(
        self,
        attempt_id: str,
        *,
        mechanism_choice_id: str,
        text: str,
        remaining_uncertainty: str,
        confidence_after: int,
    ) -> LearningView:
        previous = self._require_step(attempt_id, "explain", "Explain")
        lesson = self._view(previous).lesson
        if mechanism_choice_id not in {
            choice.choice_id
            for choice in lesson.explanation.qualification.choices
        }:
            raise LearningError("explanation choice does not belong to this lesson")
        completed = previous.evolve(
            updated_at=self.clock(),
            next_step="complete",
            explanation=Explanation(
                mechanism_choice_id,
                text.strip(),
                remaining_uncertainty.strip(),
                confidence_after,
            ),
            completed=True,
        )
        return self._save(previous, completed)

    def _save(
        self,
        previous: AttemptCheckpoint,
        next_checkpoint: AttemptCheckpoint,
    ) -> LearningView:
        if next_checkpoint.completed:
            bundle = self.bundles.snapshot(next_checkpoint.bundle_sha256)
            if bundle is None:
                raise LearningError("Learner Attempt bundle snapshot is missing")
            record = LearnerAttemptRecord.build(
                next_checkpoint,
                evaluate_attempt(bundle, next_checkpoint),
            )
            saved = self.attempts.complete(
                record,
                expected_checkpoint_sha256=previous.checkpoint_sha256,
                occurred_at=next_checkpoint.updated_at,
            )
        else:
            saved = self.attempts.save(
                next_checkpoint,
                expected_checkpoint_sha256=previous.checkpoint_sha256,
                occurred_at=next_checkpoint.updated_at,
            )
        return self._view(saved)

    def _require_step(
        self,
        attempt_id: str,
        expected: str,
        label: str,
    ) -> AttemptCheckpoint:
        view = self.view(attempt_id)
        if view.attempt is None or view.attempt.next_step != expected:
            pending = (
                view.attempt.next_step.title()
                if view.attempt is not None
                else "current step"
            )
            raise LearningError(f"Complete {pending} before {label}")
        return view.attempt

    def _view(self, attempt: AttemptCheckpoint) -> LearningView:
        bundle = self.bundles.snapshot(attempt.bundle_sha256)
        if bundle is None:
            raise LearnerStateError(
                "learner history references a missing attempt bundle snapshot"
            )
        lesson = _lesson(bundle, attempt.lesson_id)
        evaluation = (
            evaluate_attempt(bundle, attempt) if attempt.completed else None
        )
        record = (
            LearnerAttemptRecord.build(attempt, evaluation)
            if attempt.completed
            else None
        )
        projection, history = self._state(bundle)
        return LearningView(
            bundle=bundle,
            lesson=lesson,
            attempt=attempt,
            evaluation=evaluation,
            record=record,
            mastery=projection.mastery,
            review=projection.review,
            history=history,
        )

    def _state(
        self,
        bundle: LearningPackBundle,
    ) -> tuple[LearningStateProjection, tuple[AttemptHistoryEntry, ...]]:
        history_method = getattr(self.attempts, "history", None)
        if callable(history_method):
            history = history_method()
            return project_learning_state(
                bundle,
                history,
                self.clock(),
            ), history.attempts
        return (
            LearningStateProjection(
                derive_mastery(bundle, self.attempts.list()),
                ReviewProjection("not-scheduled", None, None, None),
            ),
            (),
        )

    def _durable_history(self) -> LearnerHistory:
        history_method = getattr(self.attempts, "history", None)
        if not callable(history_method):
            raise LearningError("durable learner history is unavailable")
        return history_method()

    def _review_for_route(
        self,
        pack_id: str,
        lesson_id: str,
        history_entries: tuple[AttemptHistoryEntry, ...],
        current: ReviewProjection,
    ) -> ReviewProjection:
        history_method = getattr(self.attempts, "history", None)
        if not callable(history_method):
            return current
        history = LearnerHistory((), history_entries)
        seen: set[str] = set()
        projections: list[ReviewProjection] = []
        now = self.clock()
        for entry in history_entries:
            checkpoint = entry.checkpoint
            if (
                checkpoint.pack_id != pack_id
                or checkpoint.lesson_id != lesson_id
                or checkpoint.bundle_sha256 in seen
            ):
                continue
            seen.add(checkpoint.bundle_sha256)
            bundle = self.bundles.snapshot(checkpoint.bundle_sha256)
            if bundle is None:
                raise LearnerStateError(
                    "learner history references a missing review bundle snapshot"
                )
            projection = project_learning_state(bundle, history, now).review
            if projection.status != "not-scheduled":
                projections.append(projection)
        if not projections:
            return current
        priority = {
            "in-progress": 0,
            "due": 1,
            "retry-scheduled": 2,
            "scheduled": 3,
            "retained": 4,
        }
        return min(
            projections,
            key=lambda review: (
                priority[review.status],
                review.due_at or "",
                review.demonstrated_by_attempt_id or "",
            ),
        )

    def _current_bundle(self, pack_id: str) -> LearningPackBundle:
        snapshot = self.packs.snapshot(pack_id)
        if snapshot is None:
            raise LearningError("accepted Learning Pack was not found")
        try:
            return build_codex_etl_bundle(snapshot)
        except SchemaError as exc:
            raise LearningError(
                "accepted Learning Pack has no supported lesson"
            ) from exc


def _lesson(bundle: LearningPackBundle, lesson_id: str) -> LessonBlueprint:
    matches = tuple(
        lesson for lesson in bundle.lessons if lesson.lesson_id == lesson_id
    )
    if len(matches) != 1:
        raise LearningError("lesson was not found in the accepted bundle")
    return matches[0]


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
