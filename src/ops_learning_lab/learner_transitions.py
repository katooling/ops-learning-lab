"""Pure invariants for append-time and replay-time Learner Attempt transitions."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from .attempts import AttemptCheckpoint
from .learner_errors import LearnerStateError


def require_start(
    checkpoint: AttemptCheckpoint,
    occurred_at: str,
) -> None:
    if (
        checkpoint.started_at != checkpoint.updated_at
        or occurred_at != checkpoint.started_at
        or checkpoint.next_step != "map"
        or checkpoint.prediction is not None
        or checkpoint.renderer.effective_actions
        or checkpoint.renderer.action_history
        or checkpoint.evidence
        or checkpoint.explanation is not None
        or checkpoint.hints
        or checkpoint.completed
    ):
        raise LearnerStateError(
            "Learner Attempt start does not match the initial state"
        )


def require_checkpoint_transition(
    previous: AttemptCheckpoint,
    checkpoint: AttemptCheckpoint,
    occurred_at: str,
) -> None:
    _require_same_identity(previous, checkpoint)
    _require_time_progression(previous, checkpoint, occurred_at)
    transition = (previous.next_step, checkpoint.next_step)
    if transition not in {
        ("map", "predict"),
        ("predict", "try"),
        ("try", "prove"),
        ("prove", "explain"),
        ("prove", "try"),
        ("explain", "try"),
    }:
        raise LearnerStateError(
            "Learner Attempt checkpoint transition is invalid"
        )

    stable = (
        previous.renderer.scenario_id == checkpoint.renderer.scenario_id
        and previous.renderer.input_sha256 == checkpoint.renderer.input_sha256
        and previous.renderer.seed == checkpoint.renderer.seed
        and previous.hints == checkpoint.hints
    )
    if not stable:
        raise LearnerStateError(
            "Learner Attempt checkpoint changed immutable activity state"
        )

    if transition == ("map", "predict"):
        valid = (
            checkpoint.prediction is None
            and checkpoint.renderer == previous.renderer
            and checkpoint.evidence == previous.evidence
        )
    elif transition == ("predict", "try"):
        valid = (
            checkpoint.prediction is not None
            and checkpoint.renderer == previous.renderer
            and checkpoint.evidence == previous.evidence
        )
    elif transition == ("try", "prove"):
        valid = (
            checkpoint.prediction == previous.prediction
            and checkpoint.renderer.action_history
            == (*previous.renderer.action_history, "run-pipeline")
            and checkpoint.evidence == previous.evidence
        )
    elif transition == ("prove", "explain"):
        valid = (
            checkpoint.prediction == previous.prediction
            and checkpoint.renderer == previous.renderer
            and bool(checkpoint.evidence)
        )
    else:
        valid = (
            checkpoint.prediction == previous.prediction
            and checkpoint.renderer.action_history
            == (*previous.renderer.action_history, "reset-scenario")
            and not checkpoint.renderer.effective_actions
            and not checkpoint.evidence
        )
    if not valid:
        raise LearnerStateError(
            "Learner Attempt checkpoint mutation is invalid"
        )


def require_completion_transition(
    previous: AttemptCheckpoint,
    checkpoint: AttemptCheckpoint,
    occurred_at: str,
) -> None:
    _require_same_identity(previous, checkpoint)
    _require_time_progression(previous, checkpoint, occurred_at)
    if (
        previous.next_step != "explain"
        or checkpoint.next_step != "complete"
        or checkpoint.prediction != previous.prediction
        or checkpoint.renderer != previous.renderer
        or checkpoint.evidence != previous.evidence
        or checkpoint.hints != previous.hints
        or checkpoint.explanation is None
        or not checkpoint.completed
    ):
        raise LearnerStateError(
            "Learner Attempt completion does not bind the active checkpoint"
        )


def require_restart_transition(
    previous: AttemptCheckpoint,
    checkpoint: AttemptCheckpoint,
    occurred_at: str,
) -> None:
    if checkpoint.attempt_id == previous.attempt_id:
        raise LearnerStateError(
            "whole-attempt restart requires a new Learner Attempt ID"
        )
    if _identity(checkpoint, include_attempt=False) != _identity(
        previous,
        include_attempt=False,
    ):
        raise LearnerStateError(
            "whole-attempt restart changed immutable Learner Attempt context"
        )
    if (
        _parse_time(checkpoint.started_at) < _parse_time(previous.updated_at)
        or checkpoint.started_at != checkpoint.updated_at
        or occurred_at != checkpoint.started_at
        or checkpoint.next_step != "map"
        or checkpoint.prediction is not None
        or checkpoint.renderer.scenario_id != previous.renderer.scenario_id
        or checkpoint.renderer.input_sha256
        != previous.renderer.input_sha256
        or checkpoint.renderer.seed != previous.renderer.seed
        or checkpoint.renderer.effective_actions
        or checkpoint.renderer.action_history
        or checkpoint.evidence
        or checkpoint.explanation is not None
        or checkpoint.hints
        or checkpoint.completed
    ):
        raise LearnerStateError(
            "whole-attempt restart does not match the initial state"
        )


def _require_same_identity(
    previous: AttemptCheckpoint,
    checkpoint: AttemptCheckpoint,
) -> None:
    if _identity(previous) != _identity(checkpoint):
        raise LearnerStateError(
            "Learner Attempt checkpoint changed immutable identity"
        )


def _identity(
    checkpoint: AttemptCheckpoint,
    *,
    include_attempt: bool = True,
) -> tuple[Any, ...]:
    values: tuple[Any, ...] = (
        checkpoint.pack_id,
        checkpoint.pack_version,
        checkpoint.pack_sha256,
        checkpoint.bundle_sha256,
        checkpoint.lesson_id,
        checkpoint.lesson_revision_sha256,
        checkpoint.outcome_id,
        checkpoint.outcome_revision_sha256,
    )
    if include_attempt:
        return (checkpoint.attempt_id, checkpoint.started_at, *values)
    return values


def _require_time_progression(
    previous: AttemptCheckpoint,
    checkpoint: AttemptCheckpoint,
    occurred_at: str,
) -> None:
    if (
        _parse_time(checkpoint.updated_at) < _parse_time(previous.updated_at)
        or occurred_at != checkpoint.updated_at
    ):
        raise LearnerStateError(
            "Learner Attempt checkpoint time cannot move backwards"
        )


def _parse_time(value: str) -> datetime:
    normalized = value[:-1] + "+00:00" if value.endswith("Z") else value
    parsed = datetime.fromisoformat(normalized)
    return parsed.astimezone(timezone.utc)
