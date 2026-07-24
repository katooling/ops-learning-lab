"""Append-only private learner history and its deterministic projections."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import fcntl
from hashlib import sha256
import json
import os
from pathlib import Path
import re
from threading import RLock
from typing import Any

from ._bound_directory import _BoundDirectory
from .attempts import AttemptCheckpoint
from .domain import SHA256_PATTERN, SchemaError
from .json_contract import JsonContractError, decode_json_object
from .learner_errors import LearnerStateError
from .learner_transitions import (
    require_checkpoint_transition,
    require_completion_transition,
    require_restart_transition,
    require_start,
)
from .learning import LearnerAttemptRecord
from .storage import LEARNER_EVENT_DIRECTORY, StorageError


EVENT_SCHEMA_VERSION = 1
EVENT_TYPES = frozenset(
    {
        "attempt_started",
        "checkpoint_saved",
        "attempt_reset_and_restarted",
        "attempt_completed",
    }
)
ATTEMPT_KINDS = frozenset({"learning", "review"})
EVENT_FILE_PATTERN = re.compile(
    r"^(?P<sequence>[0-9]{20})-event-(?P<short>[0-9a-f]{20})\.json$"
)
EVENT_TEMP_FILE_PATTERN = re.compile(
    r"^\.[0-9]{20}-event-[0-9a-f]{20}\.json\.[0-9a-f]{16}\.tmp$"
)
EVENT_LOCK_FILE = ".events.lock"
COMMAND_ID_PATTERN = re.compile(r"^command-[0-9a-f]{20}$")


def _canonical_bytes(value: dict[str, Any]) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def _canonical_sha256(value: dict[str, Any]) -> str:
    return sha256(_canonical_bytes(value)).hexdigest()


def _rfc3339(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value:
        raise SchemaError(f"{field} must be an RFC 3339 timestamp")
    normalized = value[:-1] + "+00:00" if value.endswith("Z") else value
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise SchemaError(f"{field} must be an RFC 3339 timestamp") from exc
    if parsed.tzinfo is None:
        raise SchemaError(f"{field} must include a timezone")
    return value


@dataclass(frozen=True, slots=True)
class LearnerStateEvent:
    sequence: int
    event_type: str
    occurred_at: str
    command_id: str
    previous_event_sha256: str | None
    payload: dict[str, Any]
    event_sha256: str
    schema_version: int = EVENT_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.schema_version != EVENT_SCHEMA_VERSION:
            raise SchemaError("unsupported learner event schema_version")
        if (
            not isinstance(self.sequence, int)
            or isinstance(self.sequence, bool)
            or self.sequence < 1
        ):
            raise SchemaError("learner event sequence must be positive")
        if self.event_type not in EVENT_TYPES:
            raise SchemaError("learner event type does not match the schema")
        _rfc3339(self.occurred_at, "learner event occurred_at")
        if (
            not isinstance(self.command_id, str)
            or not COMMAND_ID_PATTERN.fullmatch(self.command_id)
        ):
            raise SchemaError("learner event command_id does not match the schema")
        if self.previous_event_sha256 is not None and (
            not isinstance(self.previous_event_sha256, str)
            or not SHA256_PATTERN.fullmatch(self.previous_event_sha256)
        ):
            raise SchemaError("previous learner event digest is invalid")
        if not isinstance(self.payload, dict):
            raise SchemaError("learner event payload must be an object")
        self._validate_payload()
        if (
            not isinstance(self.event_sha256, str)
            or not SHA256_PATTERN.fullmatch(self.event_sha256)
            or self.event_sha256 != _canonical_sha256(self._content_dict())
        ):
            raise SchemaError("learner event digest does not match its content")

    def _validate_payload(self) -> None:
        common = {"attempt_kind", "review_of_attempt_id"}
        if self.event_type == "attempt_started":
            expected = common | {"checkpoint"}
        elif self.event_type == "checkpoint_saved":
            expected = {"previous_checkpoint_sha256", "checkpoint"}
        elif self.event_type == "attempt_completed":
            expected = common | {"previous_checkpoint_sha256", "record"}
        else:
            expected = common | {
                "previous_attempt_id",
                "previous_checkpoint_sha256",
                "checkpoint",
            }
        if set(self.payload) != expected:
            raise SchemaError("learner event payload fields do not match the schema")

        if self.event_type != "checkpoint_saved":
            kind = self.payload["attempt_kind"]
            review_of = self.payload["review_of_attempt_id"]
            if kind not in ATTEMPT_KINDS:
                raise SchemaError("attempt kind does not match the schema")
            if kind == "learning" and review_of is not None:
                raise SchemaError("learning attempt cannot reference a review source")
            if kind == "review" and (
                not isinstance(review_of, str) or not review_of
            ):
                raise SchemaError("review attempt must reference a demonstration")

        if "previous_checkpoint_sha256" in self.payload:
            digest = self.payload["previous_checkpoint_sha256"]
            if (
                not isinstance(digest, str)
                or not SHA256_PATTERN.fullmatch(digest)
            ):
                raise SchemaError("previous checkpoint digest is invalid")
        if "checkpoint" in self.payload:
            checkpoint = AttemptCheckpoint.from_dict(self.payload["checkpoint"])
            if checkpoint.completed:
                raise SchemaError("checkpoint event cannot contain a completed attempt")
        if "record" in self.payload:
            record = LearnerAttemptRecord.from_dict(self.payload["record"])
            if not record.checkpoint.completed:
                raise SchemaError("attempt completion requires a terminal record")
        if "previous_attempt_id" in self.payload and (
            not isinstance(self.payload["previous_attempt_id"], str)
            or not self.payload["previous_attempt_id"]
        ):
            raise SchemaError("reset event must reference the previous attempt")

    def _content_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "sequence": self.sequence,
            "event_type": self.event_type,
            "occurred_at": self.occurred_at,
            "command_id": self.command_id,
            "previous_event_sha256": self.previous_event_sha256,
            "payload": self.payload,
        }

    def to_dict(self) -> dict[str, Any]:
        return {**self._content_dict(), "event_sha256": self.event_sha256}

    @classmethod
    def build(
        cls,
        *,
        sequence: int,
        event_type: str,
        occurred_at: str,
        command_id: str,
        previous_event_sha256: str | None,
        payload: dict[str, Any],
    ) -> LearnerStateEvent:
        content = {
            "schema_version": EVENT_SCHEMA_VERSION,
            "sequence": sequence,
            "event_type": event_type,
            "occurred_at": occurred_at,
            "command_id": command_id,
            "previous_event_sha256": previous_event_sha256,
            "payload": payload,
        }
        return cls(**content, event_sha256=_canonical_sha256(content))

    @classmethod
    def from_dict(cls, value: Any) -> LearnerStateEvent:
        expected = {
            "schema_version",
            "sequence",
            "event_type",
            "occurred_at",
            "command_id",
            "previous_event_sha256",
            "payload",
            "event_sha256",
        }
        if not isinstance(value, dict) or set(value) != expected:
            raise SchemaError("learner event fields do not match the schema")
        return cls(**value)


@dataclass(frozen=True, slots=True)
class AttemptHistoryEntry:
    checkpoint: AttemptCheckpoint
    status: str
    attempt_kind: str
    review_of_attempt_id: str | None
    completed_record: LearnerAttemptRecord | None
    completed_at: str | None
    reset_by_attempt_id: str | None

    def __post_init__(self) -> None:
        if self.status not in {"active", "completed", "reset"}:
            raise LearnerStateError("attempt history status is invalid")
        if self.attempt_kind not in ATTEMPT_KINDS:
            raise LearnerStateError("attempt history kind is invalid")
        if self.attempt_kind == "learning" and self.review_of_attempt_id is not None:
            raise LearnerStateError("learning history cannot name a review source")
        if self.attempt_kind == "review" and not self.review_of_attempt_id:
            raise LearnerStateError("review history must name a demonstration")
        if self.status == "completed":
            if (
                not self.checkpoint.completed
                or self.completed_record is None
                or self.completed_record.checkpoint != self.checkpoint
                or self.completed_at is None
                or self.reset_by_attempt_id is not None
            ):
                raise LearnerStateError("completed attempt history is incomplete")
        elif (
            self.checkpoint.completed
            or self.completed_record is not None
            or self.completed_at is not None
        ):
            raise LearnerStateError("active or reset history cannot be completed")
        if (self.status == "reset") != (self.reset_by_attempt_id is not None):
            raise LearnerStateError("reset attempt history is incomplete")


@dataclass(frozen=True, slots=True)
class LearnerHistory:
    events: tuple[LearnerStateEvent, ...]
    attempts: tuple[AttemptHistoryEntry, ...]

    def get(self, attempt_id: str) -> AttemptHistoryEntry | None:
        return next(
            (
                entry
                for entry in self.attempts
                if entry.checkpoint.attempt_id == attempt_id
            ),
            None,
        )


def _parse_time(value: str) -> datetime:
    _rfc3339(value, "projection time")
    parsed = datetime.fromisoformat(
        value[:-1] + "+00:00" if value.endswith("Z") else value
    )
    return parsed.astimezone(timezone.utc)


class EventAttemptStore:
    """One private append-only event stream with fail-closed replay."""

    def __init__(
        self,
        events: _BoundDirectory,
        lock_descriptor: int,
    ) -> None:
        self._events = events
        self._lock_descriptor = lock_descriptor
        self._lock = RLock()

    @classmethod
    def open(cls, home: str | Path) -> EventAttemptStore:
        root = Path(home).expanduser().resolve()
        path = root / LEARNER_EVENT_DIRECTORY
        events: _BoundDirectory | None = None
        lock_descriptor = -1
        try:
            events = _BoundDirectory.open(
                path,
                "learner event directory",
                private=True,
            )
            lock_descriptor = events.duplicate_for_lock(
                "learner event directory lock",
            )
            try:
                fcntl.flock(
                    lock_descriptor,
                    fcntl.LOCK_EX | fcntl.LOCK_NB,
                )
            except BlockingIOError as exc:
                raise LearnerStateError(
                    "learner event history is already open"
                ) from exc
            return cls(events, lock_descriptor)
        except BaseException as exc:
            if lock_descriptor >= 0:
                os.close(lock_descriptor)
            if events is not None:
                events.close()
            if isinstance(exc, LearnerStateError):
                raise
            if not isinstance(exc, StorageError):
                raise LearnerStateError(
                    "cannot lock learner event history"
                ) from exc
            raise LearnerStateError(str(exc)) from exc

    def close(self) -> None:
        with self._lock:
            if self._lock_descriptor >= 0:
                fcntl.flock(self._lock_descriptor, fcntl.LOCK_UN)
                os.close(self._lock_descriptor)
                self._lock_descriptor = -1
            self._events.close()

    def get(self, attempt_id: str) -> AttemptCheckpoint | None:
        entry = self.history().get(attempt_id)
        return entry.checkpoint if entry is not None else None

    def list(self) -> tuple[AttemptCheckpoint, ...]:
        return tuple(entry.checkpoint for entry in self.history().attempts)

    def history(self) -> LearnerHistory:
        with self._lock:
            events = self._read_events()
            return self._replay(events)

    def save(
        self,
        checkpoint: AttemptCheckpoint,
        *,
        expected_checkpoint_sha256: str | None,
        command_id: str | None = None,
        attempt_kind: str = "learning",
        review_of_attempt_id: str | None = None,
        occurred_at: str | None = None,
    ) -> AttemptCheckpoint:
        command_id = command_id or _command_id(
            "save", checkpoint.checkpoint_sha256
        )
        occurred_at = occurred_at or checkpoint.updated_at
        with self._lock:
            history = self.history()
            existing_command = self._command_event(
                history.events,
                command_id,
            )
            existing = history.get(checkpoint.attempt_id)
            if existing_command is not None:
                recovered = self._checkpoint_from_event(existing_command)
                if recovered == checkpoint:
                    return checkpoint
                raise LearnerStateError(
                    "command_id was already used for different learner state"
                )

            if existing is None:
                if expected_checkpoint_sha256 is not None:
                    raise LearnerStateError("Learner Attempt checkpoint is stale")
                if checkpoint.completed:
                    raise LearnerStateError("new Learner Attempt must start incomplete")
                if attempt_kind == "review":
                    self._require_review_source(
                        history.get(review_of_attempt_id or ""),
                        checkpoint,
                    )
                    if any(
                        entry.attempt_kind == "review"
                        and entry.review_of_attempt_id
                        == review_of_attempt_id
                        and entry.status == "active"
                        for entry in history.attempts
                    ):
                        raise LearnerStateError(
                            "demonstration already has an active review"
                        )
                event_type = "attempt_started"
                require_start(checkpoint, occurred_at)
                payload = {
                    "attempt_kind": attempt_kind,
                    "review_of_attempt_id": review_of_attempt_id,
                    "checkpoint": checkpoint.to_dict(),
                }
            else:
                if existing.status != "active":
                    raise LearnerStateError(
                        "completed or reset Learner Attempt is immutable"
                    )
                if (
                    expected_checkpoint_sha256
                    != existing.checkpoint.checkpoint_sha256
                ):
                    raise LearnerStateError("Learner Attempt checkpoint is stale")
                if checkpoint.attempt_id != existing.checkpoint.attempt_id:
                    raise LearnerStateError("checkpoint changed Learner Attempt identity")
                if checkpoint.completed:
                    raise LearnerStateError(
                        "terminal checkpoint requires its evaluated record"
                    )
                require_checkpoint_transition(
                    existing.checkpoint,
                    checkpoint,
                    occurred_at,
                )
                event_type = "checkpoint_saved"
                payload = {
                    "previous_checkpoint_sha256": (
                        existing.checkpoint.checkpoint_sha256
                    ),
                    "checkpoint": checkpoint.to_dict(),
                }
            self._append(
                history.events,
                event_type=event_type,
                occurred_at=occurred_at,
                command_id=command_id,
                payload=payload,
            )
            return checkpoint

    def complete(
        self,
        record: LearnerAttemptRecord,
        *,
        expected_checkpoint_sha256: str,
        command_id: str | None = None,
        occurred_at: str | None = None,
    ) -> AttemptCheckpoint:
        checkpoint = record.checkpoint
        command_id = command_id or _command_id(
            "complete", record.record_sha256
        )
        occurred_at = occurred_at or checkpoint.updated_at
        with self._lock:
            history = self.history()
            existing_command = self._command_event(history.events, command_id)
            if existing_command is not None:
                recovered = self._checkpoint_from_event(existing_command)
                if recovered == checkpoint:
                    return checkpoint
                raise LearnerStateError(
                    "command_id was already used for different learner state"
                )
            existing = history.get(checkpoint.attempt_id)
            if existing is None or existing.status != "active":
                raise LearnerStateError("Learner Attempt is not active")
            if (
                existing.checkpoint.checkpoint_sha256
                != expected_checkpoint_sha256
            ):
                raise LearnerStateError("Learner Attempt checkpoint is stale")
            kind, review_of = (
                existing.attempt_kind,
                existing.review_of_attempt_id,
            )
            require_completion_transition(
                existing.checkpoint,
                checkpoint,
                occurred_at,
            )
            self._append(
                history.events,
                event_type="attempt_completed",
                occurred_at=occurred_at,
                command_id=command_id,
                payload={
                    "attempt_kind": kind,
                    "review_of_attempt_id": review_of,
                    "previous_checkpoint_sha256": expected_checkpoint_sha256,
                    "record": record.to_dict(),
                },
            )
            return checkpoint

    def restart(
        self,
        previous_attempt_id: str,
        checkpoint: AttemptCheckpoint,
        *,
        expected_checkpoint_sha256: str,
        command_id: str | None = None,
        occurred_at: str | None = None,
    ) -> AttemptCheckpoint:
        command_id = command_id or _command_id(
            "restart",
            expected_checkpoint_sha256,
            checkpoint.checkpoint_sha256,
        )
        occurred_at = occurred_at or checkpoint.started_at
        with self._lock:
            history = self.history()
            existing_command = self._command_event(history.events, command_id)
            if existing_command is not None:
                recovered = self._checkpoint_from_event(existing_command)
                if recovered == checkpoint:
                    return checkpoint
                raise LearnerStateError(
                    "command_id was already used for different learner state"
                )
            previous = history.get(previous_attempt_id)
            if previous is None or previous.status != "active":
                raise LearnerStateError("Learner Attempt is not active")
            if previous.checkpoint.checkpoint_sha256 != expected_checkpoint_sha256:
                raise LearnerStateError("Learner Attempt checkpoint is stale")
            if history.get(checkpoint.attempt_id) is not None:
                raise LearnerStateError("replacement Learner Attempt already exists")
            require_restart_transition(
                previous.checkpoint,
                checkpoint,
                occurred_at,
            )
            self._append(
                history.events,
                event_type="attempt_reset_and_restarted",
                occurred_at=occurred_at,
                command_id=command_id,
                payload={
                    "attempt_kind": previous.attempt_kind,
                    "review_of_attempt_id": previous.review_of_attempt_id,
                    "previous_attempt_id": previous_attempt_id,
                    "previous_checkpoint_sha256": expected_checkpoint_sha256,
                    "checkpoint": checkpoint.to_dict(),
                },
            )
            return checkpoint

    def _append(
        self,
        events: tuple[LearnerStateEvent, ...],
        *,
        event_type: str,
        occurred_at: str,
        command_id: str,
        payload: dict[str, Any],
    ) -> LearnerStateEvent:
        if events and _parse_time(occurred_at) < _parse_time(
            events[-1].occurred_at
        ):
            raise LearnerStateError(
                "learner event time cannot move backwards"
            )
        event = LearnerStateEvent.build(
            sequence=len(events) + 1,
            event_type=event_type,
            occurred_at=occurred_at,
            command_id=command_id,
            previous_event_sha256=(
                events[-1].event_sha256 if events else None
            ),
            payload=payload,
        )
        name = (
            f"{event.sequence:020d}-event-"
            f"{event.event_sha256[:20]}.json"
        )
        encoded = json.dumps(
            event.to_dict(),
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        ).encode("utf-8") + b"\n"
        outcome = self._events.atomic_create(name, encoded, 0o600)
        visible = self._events.read_regular(name, "learner event")
        if visible != encoded:
            raise LearnerStateError(
                "learner event commit visibility is uncertain"
            )
        if not outcome.directory_synced:
            # The exact event is visible and idempotently recoverable. The
            # host did not expose directory fsync support, so a power-loss
            # durability guarantee is unavailable.
            return event
        return event

    def _read_events(self) -> tuple[LearnerStateEvent, ...]:
        try:
            names = []
            for name in self._events.list_names():
                if name == EVENT_LOCK_FILE or EVENT_TEMP_FILE_PATTERN.fullmatch(
                    name
                ):
                    continue
                names.append(name)
            names.sort()
        except StorageError as exc:
            raise LearnerStateError("cannot list learner event history") from exc
        events: list[LearnerStateEvent] = []
        for expected_sequence, name in enumerate(names, start=1):
            match = EVENT_FILE_PATTERN.fullmatch(name)
            if match is None:
                raise LearnerStateError(
                    "learner event history contains an unexpected file"
                )
            if int(match.group("sequence")) != expected_sequence:
                raise LearnerStateError("learner event sequence has a gap")
            encoded = self._events.read_regular(name, "learner event")
            if encoded is None:
                raise LearnerStateError("learner event disappeared during replay")
            try:
                event = LearnerStateEvent.from_dict(
                    decode_json_object(encoded, "learner event")
                )
            except (JsonContractError, SchemaError) as exc:
                raise LearnerStateError("learner event history is corrupt") from exc
            if event.sequence != expected_sequence:
                raise LearnerStateError("learner event sequence is corrupt")
            if match.group("short") != event.event_sha256[:20]:
                raise LearnerStateError("learner event filename is corrupt")
            expected_previous = events[-1].event_sha256 if events else None
            if event.previous_event_sha256 != expected_previous:
                raise LearnerStateError("learner event hash chain is corrupt")
            if events and _parse_time(event.occurred_at) < _parse_time(
                events[-1].occurred_at
            ):
                raise LearnerStateError("learner event chronology is corrupt")
            if any(
                prior.command_id == event.command_id
                for prior in events
            ):
                raise LearnerStateError("learner command_id is duplicated")
            events.append(event)
        return tuple(events)

    @staticmethod
    def _replay(
        events: tuple[LearnerStateEvent, ...],
    ) -> LearnerHistory:
        entries: dict[str, AttemptHistoryEntry] = {}
        order: list[str] = []
        for event in events:
            payload = event.payload
            if event.event_type == "attempt_started":
                checkpoint = AttemptCheckpoint.from_dict(payload["checkpoint"])
                require_start(
                    checkpoint,
                    event.occurred_at,
                )
                if checkpoint.attempt_id in entries:
                    raise LearnerStateError("Learner Attempt started more than once")
                if payload["attempt_kind"] == "review":
                    EventAttemptStore._require_review_source(
                        entries.get(payload["review_of_attempt_id"]),
                        checkpoint,
                    )
                    if any(
                        entry.status == "active"
                        and entry.attempt_kind == "review"
                        and entry.review_of_attempt_id
                        == payload["review_of_attempt_id"]
                        for entry in entries.values()
                    ):
                        raise LearnerStateError(
                            "demonstration already has an active review"
                        )
                entries[checkpoint.attempt_id] = AttemptHistoryEntry(
                    checkpoint,
                    "active",
                    payload["attempt_kind"],
                    payload["review_of_attempt_id"],
                    None,
                    None,
                    None,
                )
                order.append(checkpoint.attempt_id)
            elif event.event_type == "checkpoint_saved":
                checkpoint = AttemptCheckpoint.from_dict(payload["checkpoint"])
                existing = entries.get(checkpoint.attempt_id)
                EventAttemptStore._require_active_previous(existing, payload)
                assert existing is not None
                require_checkpoint_transition(
                    existing.checkpoint,
                    checkpoint,
                    event.occurred_at,
                )
                entries[checkpoint.attempt_id] = AttemptHistoryEntry(
                    checkpoint,
                    "active",
                    existing.attempt_kind,
                    existing.review_of_attempt_id,
                    None,
                    None,
                    None,
                )
            elif event.event_type == "attempt_completed":
                record = LearnerAttemptRecord.from_dict(payload["record"])
                checkpoint = record.checkpoint
                existing = entries.get(checkpoint.attempt_id)
                EventAttemptStore._require_active_previous(existing, payload)
                assert existing is not None
                require_completion_transition(
                    existing.checkpoint,
                    checkpoint,
                    event.occurred_at,
                )
                if (
                    payload["attempt_kind"] != existing.attempt_kind
                    or payload["review_of_attempt_id"]
                    != existing.review_of_attempt_id
                ):
                    raise LearnerStateError(
                        "completed Learner Attempt context changed"
                    )
                entries[checkpoint.attempt_id] = AttemptHistoryEntry(
                    checkpoint,
                    "completed",
                    existing.attempt_kind,
                    existing.review_of_attempt_id,
                    record,
                    event.occurred_at,
                    None,
                )
            else:
                checkpoint = AttemptCheckpoint.from_dict(payload["checkpoint"])
                previous_id = payload["previous_attempt_id"]
                previous = entries.get(previous_id)
                EventAttemptStore._require_active_previous(previous, payload)
                if checkpoint.attempt_id in entries:
                    raise LearnerStateError(
                        "replacement Learner Attempt already exists"
                    )
                assert previous is not None
                require_restart_transition(
                    previous.checkpoint,
                    checkpoint,
                    event.occurred_at,
                )
                if (
                    payload["attempt_kind"] != previous.attempt_kind
                    or payload["review_of_attempt_id"]
                    != previous.review_of_attempt_id
                ):
                    raise LearnerStateError("reset Learner Attempt context changed")
                entries[previous_id] = AttemptHistoryEntry(
                    previous.checkpoint,
                    "reset",
                    previous.attempt_kind,
                    previous.review_of_attempt_id,
                    None,
                    None,
                    checkpoint.attempt_id,
                )
                entries[checkpoint.attempt_id] = AttemptHistoryEntry(
                    checkpoint,
                    "active",
                    previous.attempt_kind,
                    previous.review_of_attempt_id,
                    None,
                    None,
                    None,
                )
                order.append(checkpoint.attempt_id)
        return LearnerHistory(
            events,
            tuple(entries[attempt_id] for attempt_id in order),
        )

    @staticmethod
    def _require_active_previous(
        existing: AttemptHistoryEntry | None,
        payload: dict[str, Any],
    ) -> None:
        if existing is None or existing.status != "active":
            raise LearnerStateError("learner event does not follow an active attempt")
        if (
            payload["previous_checkpoint_sha256"]
            != existing.checkpoint.checkpoint_sha256
        ):
            raise LearnerStateError("learner checkpoint chain is corrupt")

    @staticmethod
    def _require_demonstration_source(
        source: AttemptHistoryEntry | None,
    ) -> None:
        if (
            source is None
            or source.status != "completed"
            or source.attempt_kind != "learning"
            or source.completed_record is None
            or source.completed_record.evaluation is None
            or not source.completed_record.evaluation.qualifies
        ):
            raise LearnerStateError(
                "review does not reference a demonstrated attempt"
            )

    @staticmethod
    def _require_review_source(
        source: AttemptHistoryEntry | None,
        review: AttemptCheckpoint,
    ) -> None:
        EventAttemptStore._require_demonstration_source(source)
        assert source is not None
        demonstration = source.checkpoint
        if (
            review.pack_id,
            review.pack_version,
            review.pack_sha256,
            review.bundle_sha256,
            review.lesson_id,
            review.lesson_revision_sha256,
            review.outcome_id,
            review.outcome_revision_sha256,
            review.renderer.scenario_id,
            review.renderer.input_sha256,
            review.renderer.seed,
        ) != (
            demonstration.pack_id,
            demonstration.pack_version,
            demonstration.pack_sha256,
            demonstration.bundle_sha256,
            demonstration.lesson_id,
            demonstration.lesson_revision_sha256,
            demonstration.outcome_id,
            demonstration.outcome_revision_sha256,
            demonstration.renderer.scenario_id,
            demonstration.renderer.input_sha256,
            demonstration.renderer.seed,
        ):
            raise LearnerStateError(
                "review changed the demonstrated learning context"
            )

    @staticmethod
    def _command_event(
        events: tuple[LearnerStateEvent, ...],
        command_id: str,
    ) -> LearnerStateEvent | None:
        return next(
            (event for event in events if event.command_id == command_id),
            None,
        )

    @staticmethod
    def _checkpoint_from_event(
        event: LearnerStateEvent,
    ) -> AttemptCheckpoint:
        if "checkpoint" in event.payload:
            return AttemptCheckpoint.from_dict(event.payload["checkpoint"])
        return LearnerAttemptRecord.from_dict(
            event.payload["record"]
        ).checkpoint


def _command_id(*parts: str) -> str:
    encoded = "\0".join(parts).encode("utf-8")
    return f"command-{sha256(encoded).hexdigest()[:20]}"
