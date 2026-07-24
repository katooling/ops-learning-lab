"""Strict sanitized contract shared by lessons and standalone export."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from hashlib import sha256
import json
import re
from typing import Any, Callable, TypeVar

from .domain import PACK_ID_PATTERN, SHA256_PATTERN, SchemaError
from .promotion_models import (
    AcceptedClaim,
    AcceptedPackSnapshot,
    CLAIM_ID_PATTERN,
    _validate_publishable_text,
)


BUNDLE_SCHEMA_VERSION = 1
IDENTIFIER_PATTERN = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
CAPABILITY_PATTERN = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*/v[1-9][0-9]*$")
REQUIRED_RENDERER_CAPABILITIES = frozenset(
    {
        "deterministic-reset/v1",
        "evidence-producing-result/v1",
        "keyboard-operable/v1",
    }
)
PUBLISHABLE_SENSITIVITY = frozenset({"public-synthetic", "sanitized"})


def _identifier(value: Any, field: str) -> str:
    if not isinstance(value, str) or not IDENTIFIER_PATTERN.fullmatch(value):
        raise SchemaError(f"{field} must be a lowercase kebab-case identifier")
    return value


def _text(value: Any, field: str) -> str:
    text = _validate_publishable_text(value, field)
    if text != text.strip():
        raise SchemaError(f"{field} cannot have surrounding whitespace")
    return text


def _positive_int(value: Any, field: str) -> int:
    if (
        not isinstance(value, int)
        or isinstance(value, bool)
        or value < 1
    ):
        raise SchemaError(f"{field} must be a positive integer")
    return value


def _rfc3339(value: Any, field: str) -> str:
    text = _text(value, field)
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
class Concept:
    concept_id: str
    title: str
    summary: str

    def __post_init__(self) -> None:
        _identifier(self.concept_id, "concept_id")
        _text(self.title, "concept title")
        _text(self.summary, "concept summary")

    def to_dict(self) -> dict[str, str]:
        return {
            "concept_id": self.concept_id,
            "title": self.title,
            "summary": self.summary,
        }

    @classmethod
    def from_dict(cls, value: Any) -> Concept:
        return cls(**_exact_object(value, {"concept_id", "title", "summary"}, "concept"))


@dataclass(frozen=True, slots=True)
class MapStage:
    stage_id: str
    title: str
    description: str

    def __post_init__(self) -> None:
        _identifier(self.stage_id, "map stage_id")
        _text(self.title, "map stage title")
        _text(self.description, "map stage description")

    def to_dict(self) -> dict[str, str]:
        return {
            "stage_id": self.stage_id,
            "title": self.title,
            "description": self.description,
        }

    @classmethod
    def from_dict(cls, value: Any) -> MapStage:
        return cls(
            **_exact_object(
                value,
                {"stage_id", "title", "description"},
                "map stage",
            )
        )


@dataclass(frozen=True, slots=True)
class ScenarioAction:
    action_id: str
    label: str
    description: str

    def __post_init__(self) -> None:
        _identifier(self.action_id, "scenario action_id")
        _text(self.label, "scenario action label")
        _text(self.description, "scenario action description")

    def to_dict(self) -> dict[str, str]:
        return {
            "action_id": self.action_id,
            "label": self.label,
            "description": self.description,
        }

    @classmethod
    def from_dict(cls, value: Any) -> ScenarioAction:
        return cls(
            **_exact_object(
                value,
                {"action_id", "label", "description"},
                "scenario action",
            )
        )


@dataclass(frozen=True, slots=True)
class ActivitySpec:
    scenario_id: str
    instructions: str
    seed: int
    input_revision_sha256: str
    actions: tuple[ScenarioAction, ...]
    renderer_capabilities: tuple[str, ...]

    def __post_init__(self) -> None:
        _identifier(self.scenario_id, "scenario_id")
        _text(self.instructions, "activity instructions")
        _positive_int(self.seed, "activity seed")
        if (
            not isinstance(self.input_revision_sha256, str)
            or not SHA256_PATTERN.fullmatch(self.input_revision_sha256)
        ):
            raise SchemaError("activity input revision does not match the schema")
        _typed_non_empty_tuple(self.actions, ScenarioAction, "scenario actions")
        action_ids = tuple(action.action_id for action in self.actions)
        _unique(action_ids, "scenario action identifiers")
        if (
            not isinstance(self.renderer_capabilities, tuple)
            or not self.renderer_capabilities
            or any(
                not isinstance(capability, str)
                or not CAPABILITY_PATTERN.fullmatch(capability)
                for capability in self.renderer_capabilities
            )
        ):
            raise SchemaError("renderer capabilities do not match the schema")
        if tuple(sorted(set(self.renderer_capabilities))) != self.renderer_capabilities:
            raise SchemaError("renderer capabilities must be sorted and unique")
        if not REQUIRED_RENDERER_CAPABILITIES.issubset(self.renderer_capabilities):
            raise SchemaError("activity omits a required renderer capability")

    def to_dict(self) -> dict[str, Any]:
        return {
            "scenario_id": self.scenario_id,
            "instructions": self.instructions,
            "seed": self.seed,
            "input_revision_sha256": self.input_revision_sha256,
            "actions": [action.to_dict() for action in self.actions],
            "renderer_capabilities": list(self.renderer_capabilities),
        }

    @classmethod
    def from_dict(cls, value: Any) -> ActivitySpec:
        fields = _exact_object(
            value,
            {
                "scenario_id",
                "instructions",
                "seed",
                "input_revision_sha256",
                "actions",
                "renderer_capabilities",
            },
            "activity",
        )
        return cls(
            scenario_id=fields["scenario_id"],
            instructions=fields["instructions"],
            seed=fields["seed"],
            input_revision_sha256=fields["input_revision_sha256"],
            actions=_objects(fields["actions"], ScenarioAction.from_dict, "actions"),
            renderer_capabilities=_strings(
                fields["renderer_capabilities"],
                "renderer capabilities",
            ),
        )


@dataclass(frozen=True, slots=True)
class Choice:
    choice_id: str
    text: str

    def __post_init__(self) -> None:
        _identifier(self.choice_id, "choice_id")
        _text(self.text, "choice text")

    def to_dict(self) -> dict[str, str]:
        return {"choice_id": self.choice_id, "text": self.text}

    @classmethod
    def from_dict(cls, value: Any) -> Choice:
        return cls(**_exact_object(value, {"choice_id", "text"}, "choice"))


@dataclass(frozen=True, slots=True)
class Prediction:
    prompt: str
    choices: tuple[Choice, ...]
    expected_choice_id: str

    def __post_init__(self) -> None:
        _text(self.prompt, "prediction prompt")
        _typed_non_empty_tuple(self.choices, Choice, "prediction choices")
        choice_ids = tuple(choice.choice_id for choice in self.choices)
        _unique(choice_ids, "prediction choice identifiers")
        if self.expected_choice_id not in choice_ids:
            raise SchemaError("expected prediction choice is missing")

    def to_dict(self) -> dict[str, Any]:
        return {
            "prompt": self.prompt,
            "choices": [choice.to_dict() for choice in self.choices],
            "expected_choice_id": self.expected_choice_id,
        }

    @classmethod
    def from_dict(cls, value: Any) -> Prediction:
        fields = _exact_object(
            value,
            {"prompt", "choices", "expected_choice_id"},
            "prediction",
        )
        return cls(
            prompt=fields["prompt"],
            choices=_objects(fields["choices"], Choice.from_dict, "prediction choices"),
            expected_choice_id=fields["expected_choice_id"],
        )


@dataclass(frozen=True, slots=True)
class EvidenceCard:
    evidence_id: str
    title: str
    proves: str
    does_not_prove: str
    source: str
    scope: str
    sensitivity: str
    observed_at: str

    def __post_init__(self) -> None:
        _identifier(self.evidence_id, "evidence_id")
        _text(self.title, "evidence title")
        _text(self.proves, "evidence proves")
        _text(self.does_not_prove, "evidence does_not_prove")
        _text(self.source, "evidence source")
        _text(self.scope, "evidence scope")
        if self.sensitivity not in PUBLISHABLE_SENSITIVITY:
            raise SchemaError("evidence sensitivity is not publishable")
        _rfc3339(self.observed_at, "evidence observed_at")

    def to_dict(self) -> dict[str, str]:
        return {
            "evidence_id": self.evidence_id,
            "title": self.title,
            "proves": self.proves,
            "does_not_prove": self.does_not_prove,
            "source": self.source,
            "scope": self.scope,
            "sensitivity": self.sensitivity,
            "observed_at": self.observed_at,
        }

    @classmethod
    def from_dict(cls, value: Any) -> EvidenceCard:
        return cls(
            **_exact_object(
                value,
                {
                    "evidence_id",
                    "title",
                    "proves",
                    "does_not_prove",
                    "source",
                    "scope",
                    "sensitivity",
                    "observed_at",
                },
                "evidence card",
            )
        )


@dataclass(frozen=True, slots=True)
class EvidenceExercise:
    claim: str
    cards: tuple[EvidenceCard, ...]
    required_support: tuple[str, ...]
    required_reject: tuple[str, ...]

    def __post_init__(self) -> None:
        _text(self.claim, "evidence claim")
        _typed_non_empty_tuple(self.cards, EvidenceCard, "evidence cards")
        evidence_ids = tuple(card.evidence_id for card in self.cards)
        _unique(evidence_ids, "evidence identifiers")
        _non_empty_unique_strings(self.required_support, "required support")
        _non_empty_unique_strings(self.required_reject, "required reject")
        known = set(evidence_ids)
        if not set(self.required_support).issubset(known):
            raise SchemaError("required support references missing evidence")
        if not set(self.required_reject).issubset(known):
            raise SchemaError("required reject references missing evidence")
        if set(self.required_support).intersection(self.required_reject):
            raise SchemaError("evidence cannot be both required and rejected")
        if set(self.required_support).union(self.required_reject) != known:
            raise SchemaError("every evidence card needs an explicit verdict")

    def to_dict(self) -> dict[str, Any]:
        return {
            "claim": self.claim,
            "cards": [card.to_dict() for card in self.cards],
            "required_support": list(self.required_support),
            "required_reject": list(self.required_reject),
        }

    @classmethod
    def from_dict(cls, value: Any) -> EvidenceExercise:
        fields = _exact_object(
            value,
            {"claim", "cards", "required_support", "required_reject"},
            "evidence exercise",
        )
        return cls(
            claim=fields["claim"],
            cards=_objects(fields["cards"], EvidenceCard.from_dict, "evidence cards"),
            required_support=_strings(fields["required_support"], "required support"),
            required_reject=_strings(fields["required_reject"], "required reject"),
        )


@dataclass(frozen=True, slots=True)
class ExplanationPrompt:
    prompt: str
    minimum_characters: int
    qualification: Prediction

    def __post_init__(self) -> None:
        _text(self.prompt, "explanation prompt")
        _positive_int(self.minimum_characters, "minimum_characters")
        if not isinstance(self.qualification, Prediction):
            raise SchemaError("explanation qualification does not match the schema")

    def to_dict(self) -> dict[str, Any]:
        return {
            "prompt": self.prompt,
            "minimum_characters": self.minimum_characters,
            "qualification": self.qualification.to_dict(),
        }

    @classmethod
    def from_dict(cls, value: Any) -> ExplanationPrompt:
        fields = _exact_object(
            value,
            {"prompt", "minimum_characters", "qualification"},
            "explanation prompt",
        )
        return cls(
            prompt=fields["prompt"],
            minimum_characters=fields["minimum_characters"],
            qualification=Prediction.from_dict(fields["qualification"]),
        )


@dataclass(frozen=True, slots=True)
class LearningOutcome:
    outcome_id: str
    statement: str
    outcome_revision_sha256: str

    def __post_init__(self) -> None:
        _identifier(self.outcome_id, "outcome_id")
        _text(self.statement, "outcome statement")
        expected = _canonical_sha256(
            {"outcome_id": self.outcome_id, "statement": self.statement}
        )
        if self.outcome_revision_sha256 != expected:
            raise SchemaError("outcome revision digest does not match its content")

    def to_dict(self) -> dict[str, str]:
        return {
            "outcome_id": self.outcome_id,
            "statement": self.statement,
            "outcome_revision_sha256": self.outcome_revision_sha256,
        }

    @classmethod
    def build(cls, outcome_id: str, statement: str) -> LearningOutcome:
        digest = _canonical_sha256(
            {"outcome_id": outcome_id, "statement": statement}
        )
        return cls(outcome_id, statement, digest)

    @classmethod
    def from_dict(cls, value: Any) -> LearningOutcome:
        return cls(
            **_exact_object(
                value,
                {"outcome_id", "statement", "outcome_revision_sha256"},
                "learning outcome",
            )
        )


@dataclass(frozen=True, slots=True)
class LessonBlueprint:
    lesson_id: str
    title: str
    concept_id: str
    claim_id: str
    outcome: LearningOutcome
    map_stages: tuple[MapStage, ...]
    prediction: Prediction
    activity: ActivitySpec
    evidence: EvidenceExercise
    explanation: ExplanationPrompt
    lesson_revision_sha256: str

    def __post_init__(self) -> None:
        _identifier(self.lesson_id, "lesson_id")
        _text(self.title, "lesson title")
        _identifier(self.concept_id, "lesson concept_id")
        if not isinstance(self.claim_id, str) or not CLAIM_ID_PATTERN.fullmatch(
            self.claim_id
        ):
            raise SchemaError("lesson claim_id does not match the schema")
        if not isinstance(self.outcome, LearningOutcome):
            raise SchemaError("lesson outcome does not match the schema")
        _typed_non_empty_tuple(self.map_stages, MapStage, "map stages")
        _unique(
            tuple(stage.stage_id for stage in self.map_stages),
            "map stage identifiers",
        )
        if not isinstance(self.prediction, Prediction):
            raise SchemaError("lesson prediction does not match the schema")
        if not isinstance(self.activity, ActivitySpec):
            raise SchemaError("lesson activity does not match the schema")
        if not isinstance(self.evidence, EvidenceExercise):
            raise SchemaError("lesson evidence does not match the schema")
        if not isinstance(self.explanation, ExplanationPrompt):
            raise SchemaError("lesson explanation does not match the schema")
        if self.lesson_revision_sha256 != _canonical_sha256(self._content_dict()):
            raise SchemaError("lesson revision digest does not match its content")

    def _content_dict(self) -> dict[str, Any]:
        return {
            "lesson_id": self.lesson_id,
            "title": self.title,
            "concept_id": self.concept_id,
            "claim_id": self.claim_id,
            "outcome": self.outcome.to_dict(),
            "map_stages": [stage.to_dict() for stage in self.map_stages],
            "prediction": self.prediction.to_dict(),
            "activity": self.activity.to_dict(),
            "evidence": self.evidence.to_dict(),
            "explanation": self.explanation.to_dict(),
        }

    def to_dict(self) -> dict[str, Any]:
        return {
            **self._content_dict(),
            "lesson_revision_sha256": self.lesson_revision_sha256,
        }

    @classmethod
    def build(
        cls,
        *,
        lesson_id: str,
        title: str,
        concept_id: str,
        claim_id: str,
        outcome: LearningOutcome,
        map_stages: tuple[MapStage, ...],
        prediction: Prediction,
        activity: ActivitySpec,
        evidence: EvidenceExercise,
        explanation: ExplanationPrompt,
    ) -> LessonBlueprint:
        content = {
            "lesson_id": lesson_id,
            "title": title,
            "concept_id": concept_id,
            "claim_id": claim_id,
            "outcome": outcome.to_dict(),
            "map_stages": [stage.to_dict() for stage in map_stages],
            "prediction": prediction.to_dict(),
            "activity": activity.to_dict(),
            "evidence": evidence.to_dict(),
            "explanation": explanation.to_dict(),
        }
        return cls(
            lesson_id=lesson_id,
            title=title,
            concept_id=concept_id,
            claim_id=claim_id,
            outcome=outcome,
            map_stages=map_stages,
            prediction=prediction,
            activity=activity,
            evidence=evidence,
            explanation=explanation,
            lesson_revision_sha256=_canonical_sha256(content),
        )

    @classmethod
    def from_dict(cls, value: Any) -> LessonBlueprint:
        fields = _exact_object(
            value,
            {
                "lesson_id",
                "title",
                "concept_id",
                "claim_id",
                "outcome",
                "map_stages",
                "prediction",
                "activity",
                "evidence",
                "explanation",
                "lesson_revision_sha256",
            },
            "lesson",
        )
        return cls(
            lesson_id=fields["lesson_id"],
            title=fields["title"],
            concept_id=fields["concept_id"],
            claim_id=fields["claim_id"],
            outcome=LearningOutcome.from_dict(fields["outcome"]),
            map_stages=_objects(
                fields["map_stages"],
                MapStage.from_dict,
                "map stages",
            ),
            prediction=Prediction.from_dict(fields["prediction"]),
            activity=ActivitySpec.from_dict(fields["activity"]),
            evidence=EvidenceExercise.from_dict(fields["evidence"]),
            explanation=ExplanationPrompt.from_dict(fields["explanation"]),
            lesson_revision_sha256=fields["lesson_revision_sha256"],
        )


@dataclass(frozen=True, slots=True)
class LearningPackBundle:
    """Content-addressed sanitized input to teaching and export."""

    pack_id: str
    title: str
    pack_version: int
    accepted_snapshot_sha256: str
    concepts: tuple[Concept, ...]
    claims: tuple[AcceptedClaim, ...]
    lessons: tuple[LessonBlueprint, ...]
    bundle_sha256: str
    schema_version: int = BUNDLE_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.schema_version != BUNDLE_SCHEMA_VERSION:
            raise SchemaError("unsupported Learning Pack Bundle schema_version")
        if not isinstance(self.pack_id, str) or not PACK_ID_PATTERN.fullmatch(
            self.pack_id
        ):
            raise SchemaError("bundle pack_id does not match the schema")
        _text(self.title, "bundle title")
        _positive_int(self.pack_version, "bundle pack_version")
        if not isinstance(
            self.accepted_snapshot_sha256, str
        ) or not SHA256_PATTERN.fullmatch(self.accepted_snapshot_sha256):
            raise SchemaError("accepted snapshot digest does not match the schema")
        _typed_non_empty_tuple(self.concepts, Concept, "bundle concepts")
        _typed_non_empty_tuple(self.claims, AcceptedClaim, "bundle claims")
        _typed_non_empty_tuple(self.lessons, LessonBlueprint, "bundle lessons")
        _unique(
            tuple(concept.concept_id for concept in self.concepts),
            "concept identifiers",
        )
        _unique(
            tuple(claim.claim_id for claim in self.claims),
            "bundle claim identifiers",
        )
        _unique(
            tuple(lesson.lesson_id for lesson in self.lessons),
            "lesson identifiers",
        )
        concept_ids = {concept.concept_id for concept in self.concepts}
        claim_ids = {claim.claim_id for claim in self.claims}
        for lesson in self.lessons:
            if lesson.concept_id not in concept_ids:
                raise SchemaError("lesson references a missing concept")
            if lesson.claim_id not in claim_ids:
                raise SchemaError("lesson references a missing accepted claim")
        expected = _canonical_sha256(self._content_dict())
        if (
            not isinstance(self.bundle_sha256, str)
            or not SHA256_PATTERN.fullmatch(self.bundle_sha256)
            or self.bundle_sha256 != expected
        ):
            raise SchemaError("bundle_sha256 does not match bundle content")

    def _content_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "pack_id": self.pack_id,
            "title": self.title,
            "pack_version": self.pack_version,
            "accepted_snapshot_sha256": self.accepted_snapshot_sha256,
            "concepts": [concept.to_dict() for concept in self.concepts],
            "claims": [claim.to_dict() for claim in self.claims],
            "lessons": [lesson.to_dict() for lesson in self.lessons],
        }

    def to_dict(self) -> dict[str, Any]:
        return {**self._content_dict(), "bundle_sha256": self.bundle_sha256}

    @classmethod
    def build(
        cls,
        snapshot: AcceptedPackSnapshot,
        *,
        concepts: tuple[Concept, ...],
        lessons: tuple[LessonBlueprint, ...],
    ) -> LearningPackBundle:
        if not isinstance(snapshot, AcceptedPackSnapshot):
            raise SchemaError("bundle source must be an Accepted Pack snapshot")
        content = {
            "schema_version": BUNDLE_SCHEMA_VERSION,
            "pack_id": snapshot.pack_id,
            "title": snapshot.title,
            "pack_version": snapshot.version,
            "accepted_snapshot_sha256": snapshot.content_sha256,
            "concepts": [concept.to_dict() for concept in concepts],
            "claims": [claim.to_dict() for claim in snapshot.claims],
            "lessons": [lesson.to_dict() for lesson in lessons],
        }
        return cls(
            pack_id=snapshot.pack_id,
            title=snapshot.title,
            pack_version=snapshot.version,
            accepted_snapshot_sha256=snapshot.content_sha256,
            concepts=concepts,
            claims=snapshot.claims,
            lessons=lessons,
            bundle_sha256=_canonical_sha256(content),
        )

    @classmethod
    def from_dict(cls, value: Any) -> LearningPackBundle:
        fields = _exact_object(
            value,
            {
                "schema_version",
                "pack_id",
                "title",
                "pack_version",
                "accepted_snapshot_sha256",
                "concepts",
                "claims",
                "lessons",
                "bundle_sha256",
            },
            "Learning Pack Bundle",
        )
        return cls(
            schema_version=fields["schema_version"],
            pack_id=fields["pack_id"],
            title=fields["title"],
            pack_version=fields["pack_version"],
            accepted_snapshot_sha256=fields["accepted_snapshot_sha256"],
            concepts=_objects(fields["concepts"], Concept.from_dict, "concepts"),
            claims=_objects(fields["claims"], AcceptedClaim.from_dict, "claims"),
            lessons=_objects(
                fields["lessons"],
                LessonBlueprint.from_dict,
                "lessons",
            ),
            bundle_sha256=fields["bundle_sha256"],
        )


T = TypeVar("T")


def _exact_object(
    value: Any,
    expected: set[str],
    label: str,
) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != expected:
        raise SchemaError(f"{label} fields do not match the schema")
    return value


def _objects(
    value: Any,
    loader: Callable[[Any], T],
    label: str,
) -> tuple[T, ...]:
    if not isinstance(value, list):
        raise SchemaError(f"{label} must be a list")
    return tuple(loader(item) for item in value)


def _strings(value: Any, label: str) -> tuple[str, ...]:
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        raise SchemaError(f"{label} must be a list of strings")
    return tuple(value)


def _typed_non_empty_tuple(
    value: Any,
    expected_type: type[Any],
    label: str,
) -> None:
    if (
        not isinstance(value, tuple)
        or not value
        or any(not isinstance(item, expected_type) for item in value)
    ):
        raise SchemaError(f"{label} do not match the schema")


def _non_empty_unique_strings(value: Any, label: str) -> None:
    if (
        not isinstance(value, tuple)
        or not value
        or any(not isinstance(item, str) or not item for item in value)
    ):
        raise SchemaError(f"{label} must be non-empty strings")
    _unique(value, label)


def _unique(values: tuple[str, ...], label: str) -> None:
    if len(set(values)) != len(values):
        raise SchemaError(f"{label} must be unique")
