"""Application seam for one ordered evidence-centered Learning Loop."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import secrets
from threading import RLock
from typing import Callable, Iterable

from .activity import render_scenario
from .attempts import (
    AttemptCheckpoint,
    EvidenceDecision,
    Explanation,
    Prediction,
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
from .learning_bundle import LearningPackBundle, LessonBlueprint
from .lesson_content import build_codex_etl_bundle


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


class LearningService:
    """Own ordering, canonical attempts, evaluation, and derived mastery."""

    def __init__(
        self,
        packs,
        bundles: BundleRepository,
        attempts: InMemoryAttemptStore,
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

    def open_lesson(self, pack_id: str, lesson_id: str) -> LearningView:
        """Read current accepted input without persisting a bundle or attempt."""

        bundle = self._current_bundle(pack_id)
        lesson = _lesson(bundle, lesson_id)
        return LearningView(
            bundle=bundle,
            lesson=lesson,
            attempt=None,
            evaluation=None,
            record=None,
            mastery=derive_mastery(bundle, self.attempts.list()),
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
        bundle = self._current_bundle(pack_id)
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
        self.attempts.save(attempt, expected_checkpoint_sha256=None)
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
            prediction=Prediction(choice_id, confidence),
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
        saved = self.attempts.save(
            next_checkpoint,
            expected_checkpoint_sha256=previous.checkpoint_sha256,
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
            raise LearningError("Learner Attempt bundle snapshot is missing")
        lesson = _lesson(bundle, attempt.lesson_id)
        evaluation = (
            evaluate_attempt(bundle, attempt) if attempt.completed else None
        )
        record = (
            LearnerAttemptRecord.build(attempt, evaluation)
            if attempt.completed
            else None
        )
        mastery = derive_mastery(bundle, self.attempts.list())
        return LearningView(
            bundle=bundle,
            lesson=lesson,
            attempt=attempt,
            evaluation=evaluation,
            record=record,
            mastery=mastery,
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
