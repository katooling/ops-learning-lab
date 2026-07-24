"""Explicit, read-only Codex intake adapter."""

from __future__ import annotations

from dataclasses import dataclass
import json
from typing import Any, Protocol

from .compiler import compile_update, propose_pack_match, validate_capture_text
from .domain import SchemaError, SourceReference
from .learner_state import LearnerHistory, LearnerStateError
from .staging import PackUpdateRepository
from .storage import LearningHome, StorageError


IMPORT_SCHEMA_VERSION = 1
MAX_IMPORT_BYTES = 1_048_576


class CodexImportError(RuntimeError):
    """Raised when a bounded import cannot be completed safely."""


def _exact_fields(value: Any, expected: set[str], label: str) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != expected:
        raise SchemaError(f"{label} fields do not match the schema")
    return value


def _non_empty_text(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise SchemaError(f"{label} must be a non-empty string")
    return value


@dataclass(frozen=True, slots=True)
class PastedTextSource:
    source_id: str
    observed_at: str
    text: str

    def __post_init__(self) -> None:
        _non_empty_text(self.source_id, "source_id")
        _non_empty_text(self.observed_at, "observed_at")
        _non_empty_text(self.text, "text")

    @classmethod
    def from_dict(cls, value: Any) -> PastedTextSource:
        source = _exact_fields(
            value,
            {"kind", "source_id", "observed_at", "text"},
            "pasted_text source",
        )
        if source["kind"] != "pasted_text":
            raise SchemaError("source kind does not match pasted_text")
        return cls(
            source_id=_non_empty_text(source["source_id"], "source_id"),
            observed_at=_non_empty_text(source["observed_at"], "observed_at"),
            text=_non_empty_text(source["text"], "text"),
        )


@dataclass(frozen=True, slots=True)
class TurnSelection:
    turn_ids: tuple[str, ...] = ()
    start_turn_id: str | None = None
    end_turn_id: str | None = None

    def __post_init__(self) -> None:
        ids_selected = bool(self.turn_ids)
        range_selected = (
            self.start_turn_id is not None and self.end_turn_id is not None
        )
        if ids_selected == range_selected:
            raise SchemaError(
                "task import requires either explicit turn_ids or one turn range"
            )
        if ids_selected:
            if len(set(self.turn_ids)) != len(self.turn_ids):
                raise SchemaError("turn_ids must be unique")
            for turn_id in self.turn_ids:
                _non_empty_text(turn_id, "turn_id")
        else:
            _non_empty_text(self.start_turn_id, "start_turn_id")
            _non_empty_text(self.end_turn_id, "end_turn_id")

    def scope(self) -> str:
        if self.turn_ids:
            value: dict[str, object] = {"turn_ids": list(self.turn_ids)}
        else:
            value = {
                "end_turn_id": self.end_turn_id,
                "start_turn_id": self.start_turn_id,
            }
        return json.dumps(value, sort_keys=True, separators=(",", ":"))


@dataclass(frozen=True, slots=True)
class TaskTurnsSource:
    task_id: str
    selection: TurnSelection

    def __post_init__(self) -> None:
        _non_empty_text(self.task_id, "task_id")
        if not isinstance(self.selection, TurnSelection):
            raise SchemaError("selection must be a TurnSelection")

    @classmethod
    def from_dict(cls, value: Any) -> TaskTurnsSource:
        if not isinstance(value, dict):
            raise SchemaError("task source must be an object")
        kind = value.get("kind")
        if kind == "task_turns":
            source = _exact_fields(
                value,
                {"kind", "task_id", "turn_ids"},
                "task_turns source",
            )
            turn_ids = source["turn_ids"]
            if not isinstance(turn_ids, list):
                raise SchemaError("turn_ids must be a list")
            return cls(
                task_id=_non_empty_text(source["task_id"], "task_id"),
                selection=TurnSelection(turn_ids=tuple(turn_ids)),
            )
        if kind == "task_turn_range":
            source = _exact_fields(
                value,
                {"kind", "task_id", "start_turn_id", "end_turn_id"},
                "task_turn_range source",
            )
            return cls(
                task_id=_non_empty_text(source["task_id"], "task_id"),
                selection=TurnSelection(
                    start_turn_id=source["start_turn_id"],
                    end_turn_id=source["end_turn_id"],
                ),
            )
        raise SchemaError("source kind is not supported")


@dataclass(frozen=True, slots=True)
class InlineTaskExtractSource:
    """A caller-supplied, explicitly bounded Codex task extract."""

    task_id: str
    selection: TurnSelection
    observed_at: str
    text: str

    def __post_init__(self) -> None:
        _non_empty_text(self.task_id, "task_id")
        if not isinstance(self.selection, TurnSelection):
            raise SchemaError("selection must be a TurnSelection")
        _non_empty_text(self.observed_at, "observed_at")
        _non_empty_text(self.text, "text")

    @classmethod
    def from_dict(cls, value: Any) -> InlineTaskExtractSource:
        if not isinstance(value, dict):
            raise SchemaError("task extract source must be an object")
        kind = value.get("kind")
        common = {"kind", "task_id", "observed_at", "text"}
        if kind == "task_turns_extract":
            source = _exact_fields(
                value,
                common | {"turn_ids"},
                "task_turns_extract source",
            )
            turn_ids = source["turn_ids"]
            if not isinstance(turn_ids, list):
                raise SchemaError("turn_ids must be a list")
            selection = TurnSelection(turn_ids=tuple(turn_ids))
        elif kind == "task_turn_range_extract":
            source = _exact_fields(
                value,
                common | {"start_turn_id", "end_turn_id"},
                "task_turn_range_extract source",
            )
            selection = TurnSelection(
                start_turn_id=source["start_turn_id"],
                end_turn_id=source["end_turn_id"],
            )
        else:
            raise SchemaError("source kind is not supported")
        return cls(
            task_id=source["task_id"],
            selection=selection,
            observed_at=source["observed_at"],
            text=source["text"],
        )


ImportSource = PastedTextSource | TaskTurnsSource | InlineTaskExtractSource


@dataclass(frozen=True, slots=True)
class CodexImportRequest:
    mode: str
    source: ImportSource
    selected_pack_id: str | None = None
    schema_version: int = IMPORT_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if (
            not isinstance(self.schema_version, int)
            or isinstance(self.schema_version, bool)
            or self.schema_version != IMPORT_SCHEMA_VERSION
        ):
            raise SchemaError("unsupported Codex import schema_version")
        if self.mode not in {"capture", "learn"}:
            raise SchemaError("mode must be capture or learn")
        if not isinstance(
            self.source,
            (PastedTextSource, TaskTurnsSource, InlineTaskExtractSource),
        ):
            raise SchemaError("source kind is not supported")
        if self.selected_pack_id is not None:
            _non_empty_text(self.selected_pack_id, "selected_pack_id")

    @classmethod
    def from_dict(cls, value: Any) -> CodexImportRequest:
        if not isinstance(value, dict) or set(value) not in {
            frozenset({"schema_version", "mode", "source"}),
            frozenset(
                {"schema_version", "mode", "source", "selected_pack_id"}
            ),
        }:
            raise SchemaError("Codex import request fields do not match the schema")
        request = value
        if (
            not isinstance(request["schema_version"], int)
            or isinstance(request["schema_version"], bool)
            or request["schema_version"] != IMPORT_SCHEMA_VERSION
        ):
            raise SchemaError("unsupported Codex import schema_version")
        if request["mode"] not in {"capture", "learn"}:
            raise SchemaError("mode must be capture or learn")
        source = request["source"]
        if not isinstance(source, dict):
            raise SchemaError("source must be an object")
        if source.get("kind") == "pasted_text":
            parsed_source: ImportSource = PastedTextSource.from_dict(source)
        elif source.get("kind") in {"task_turns", "task_turn_range"}:
            parsed_source = TaskTurnsSource.from_dict(source)
        elif source.get("kind") in {
            "task_turns_extract",
            "task_turn_range_extract",
        }:
            parsed_source = InlineTaskExtractSource.from_dict(source)
        else:
            raise SchemaError("source kind is not supported")
        selected_pack_id = request.get("selected_pack_id")
        if selected_pack_id is not None:
            _non_empty_text(selected_pack_id, "selected_pack_id")
        return cls(
            mode=request["mode"],
            source=parsed_source,
            selected_pack_id=selected_pack_id,
        )


@dataclass(frozen=True, slots=True)
class ConversationExtract:
    task_id: str
    selection: TurnSelection
    observed_at: str
    content: bytes

    def __post_init__(self) -> None:
        _non_empty_text(self.task_id, "task_id")
        if not isinstance(self.selection, TurnSelection):
            raise SchemaError("selection must be a TurnSelection")
        _non_empty_text(self.observed_at, "observed_at")
        if not isinstance(self.content, bytes) or not self.content:
            raise SchemaError("conversation extract content must be non-empty bytes")
        if len(self.content) > MAX_IMPORT_BYTES:
            raise SchemaError("conversation extract exceeds the import safety limit")


class ConversationReadPort(Protocol):
    """The only task capability visible to the adapter."""

    def read_selected(
        self,
        task_id: str,
        selection: TurnSelection,
    ) -> ConversationExtract: ...


@dataclass(frozen=True, slots=True)
class LearningDestination:
    path: str
    disposition: str
    attempt_id: str | None

    def __post_init__(self) -> None:
        if not self.path.startswith("/"):
            raise SchemaError("learning path must be a Product Shell path")
        if self.disposition not in {"opened", "resumed"}:
            raise SchemaError("learning disposition must be opened or resumed")
        if self.disposition == "resumed" and self.attempt_id is None:
            raise SchemaError("a resumed lesson requires an attempt_id")


class LearningLaunchPort(Protocol):
    """Resume-capable boundary owned by the Product Shell integration."""

    def open_or_resume(self, pack_id: str) -> LearningDestination: ...


class AttemptHistoryReadPort(Protocol):
    """Read-only durable attempt projection used only for route selection."""

    def history(self) -> LearnerHistory: ...


class ProductShellLearningPort:
    """Choose one existing Product Shell lesson or durable active attempt."""

    def __init__(
        self,
        learning,
        attempts: AttemptHistoryReadPort,
    ) -> None:
        self.learning = learning
        self.attempts = attempts

    def open_or_resume(self, pack_id: str) -> LearningDestination:
        try:
            lessons = self.learning.available_lessons(pack_id)
            history = self.attempts.history()
        except (
            LearnerStateError,
            SchemaError,
            StorageError,
            ValueError,
        ) as exc:
            raise CodexImportError(
                "Product Shell could not inspect the accepted lesson or history"
            ) from exc
        if len(lessons) != 1:
            raise CodexImportError(
                "exactly one Product Shell lesson must be available for Learn Mode"
            )
        lesson = lessons[0]
        active = tuple(
            entry
            for entry in history.attempts
            if (
                entry.status == "active"
                and entry.attempt_kind == "learning"
                and entry.checkpoint.pack_id == pack_id
                and entry.checkpoint.lesson_id == lesson.lesson_id
            )
        )
        if len(active) > 1:
            raise CodexImportError(
                "multiple active Learner Attempts match this pack and lesson"
            )
        if active:
            attempt_id = active[0].checkpoint.attempt_id
            return LearningDestination(
                path=f"/attempts/{attempt_id}",
                disposition="resumed",
                attempt_id=attempt_id,
            )
        return LearningDestination(
            path=f"/learn/{pack_id}/{lesson.lesson_id}",
            disposition="opened",
            attempt_id=None,
        )


class CodexImportService:
    """Capture explicit input; it never reaches into a Codex task by itself."""

    def __init__(
        self,
        home: LearningHome,
        *,
        conversation_port: ConversationReadPort | None = None,
        learning_port: LearningLaunchPort | None = None,
    ) -> None:
        self.home = home
        self.updates = PackUpdateRepository.open(home.root)
        self.conversation_port = conversation_port
        self.learning_port = learning_port

    def run(self, request: CodexImportRequest) -> dict[str, object]:
        content, source = self._retrieve(request.source)
        try:
            capture_text = content.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise SchemaError("Codex import accepts UTF-8 text only") from exc
        validate_capture_text(capture_text)
        match = propose_pack_match(capture_text)
        selected_pack_id = self._selected_pack(request, match)

        manifest = self.home.capture(content, source)
        update = self.updates.stage(
            compile_update(
                content,
                manifest,
                selected_pack_id=request.selected_pack_id,
            )
        )
        unresolved = update.match.kind == "ambiguous" and selected_pack_id is None
        result: dict[str, object] = {
            "status": "learner_choice_required" if unresolved else "staged",
            "mode": request.mode,
            "intake_id": manifest.intake_id,
            "content_sha256": manifest.content_sha256,
            "update_id": update.update_id,
            "match_kind": update.match.kind,
            "proposed_pack_id": update.match.proposed_pack_id,
            "selected_pack_id": selected_pack_id,
            "candidate_pack_ids": [
                candidate.pack_id for candidate in update.match.candidates
            ],
            "review_path": f"/updates/{update.update_id}",
            "summary": (
                f"Staged {len(update.proposed_claims)} claim proposal(s); "
                "accepted Learning Packs are unchanged."
            ),
            "lesson_started": False,
        }
        if request.mode == "capture":
            return result
        if selected_pack_id is None:
            if unresolved:
                return result
            result.update(
                {
                    "status": "learning_incomplete",
                    "learning_error": (
                        "No existing Learning Pack matched this intake; review "
                        "the proposed new Learning Pack before starting a lesson."
                    ),
                }
            )
            return result
        if self.learning_port is None:
            result.update(
                {
                    "status": "learning_incomplete",
                    "learning_error": (
                        "Product Shell learning integration is not configured; "
                        "the Intake Bundle and staged Pack Update remain available."
                    ),
                }
            )
            return result
        try:
            destination = self.learning_port.open_or_resume(selected_pack_id)
        except CodexImportError as exc:
            result.update(
                {
                    "status": "learning_incomplete",
                    "learning_error": (
                        f"{exc}; the Intake Bundle and staged Pack Update "
                        "remain available."
                    ),
                }
            )
            return result
        result.update(
            {
                "status": "learning_ready",
                "learning_path": destination.path,
                "learning_disposition": destination.disposition,
                "attempt_id": destination.attempt_id,
            }
        )
        return result

    def _retrieve(
        self,
        source: ImportSource,
    ) -> tuple[bytes, SourceReference]:
        if isinstance(source, PastedTextSource):
            content = source.text.encode("utf-8")
            if len(content) > MAX_IMPORT_BYTES:
                raise SchemaError("pasted text exceeds the import safety limit")
            return (
                content,
                SourceReference(
                    source_type="codex-pasted-text",
                    source_id=source.source_id,
                    observed_at=source.observed_at,
                    retrieval_scope="exact-pasted-text",
                ),
            )
        if isinstance(source, InlineTaskExtractSource):
            content = source.text.encode("utf-8")
            if len(content) > MAX_IMPORT_BYTES:
                raise SchemaError("task extract exceeds the import safety limit")
            return (
                content,
                SourceReference(
                    source_type="codex-task",
                    source_id=source.task_id,
                    observed_at=source.observed_at,
                    retrieval_scope=source.selection.scope(),
                ),
            )
        if self.conversation_port is None:
            raise CodexImportError(
                "targeted Codex task reads are not configured in this runtime; "
                "no intake was written"
            )
        extract = self.conversation_port.read_selected(
            source.task_id,
            source.selection,
        )
        if (
            extract.task_id != source.task_id
            or extract.selection != source.selection
        ):
            raise CodexImportError(
                "conversation port returned a different task retrieval scope"
            )
        return (
            extract.content,
            SourceReference(
                source_type="codex-task",
                source_id=extract.task_id,
                observed_at=extract.observed_at,
                retrieval_scope=extract.selection.scope(),
            ),
        )

    @staticmethod
    def _selected_pack(request: CodexImportRequest, match) -> str | None:
        candidate_ids = tuple(candidate.pack_id for candidate in match.candidates)
        if request.selected_pack_id is not None:
            if request.selected_pack_id not in candidate_ids:
                raise SchemaError(
                    "selected_pack_id is not one of the proposed Pack Matches"
                )
            return request.selected_pack_id
        return match.proposed_pack_id
