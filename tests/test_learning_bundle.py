from __future__ import annotations

from dataclasses import replace
import unittest

from ops_learning_lab.domain import SchemaError
from ops_learning_lab.learning_bundle import (
    ActivitySpec,
    Choice,
    Concept,
    EvidenceCard,
    EvidenceExercise,
    ExplanationPrompt,
    LearningPackBundle,
    LearningOutcome,
    LessonBlueprint,
    MapStage,
    Prediction,
    ScenarioAction,
)
from ops_learning_lab.promotion_models import (
    AcceptedClaim,
    AcceptedPackSnapshot,
    AcceptedProvenance,
)


def accepted_snapshot() -> AcceptedPackSnapshot:
    return AcceptedPackSnapshot(
        pack_id="synthetic-etl",
        title="Synthetic ETL evidence",
        version=1,
        content_sha256="a" * 64,
        claims=(
            AcceptedClaim(
                claim_id="claim-" + "1" * 20,
                text="A normalized cost must not be negative.",
                fact_status="current",
                history_action="add",
                target_claim_id=None,
                provenance=AcceptedProvenance(
                    source_type="synthetic-note",
                    observed_at="2026-07-24T12:00:00Z",
                    staged_update_id="update-" + "2" * 20,
                    proposal_id="proposal-" + "3" * 20,
                ),
            ),
        ),
    )


def lesson() -> LessonBlueprint:
    return LessonBlueprint.build(
        lesson_id="trace-one-record",
        title="Trace one safe record",
        concept_id="proof-scope",
        claim_id="claim-" + "1" * 20,
        outcome=LearningOutcome.build(
            "select-exact-evidence",
            "Select evidence that proves the exact operational claim.",
        ),
        map_stages=(
            MapStage(
                stage_id="source",
                title="Source",
                description="A synthetic response enters the pipeline.",
            ),
            MapStage(
                stage_id="normalized",
                title="Normalized data",
                description="The safe record receives a normalized cost.",
            ),
        ),
        prediction=Prediction(
            prompt="Which record should validation reject?",
            choices=(
                Choice("negative-cost", "The record with a negative cost."),
                Choice("fresh-record", "The fresh non-negative record."),
            ),
            expected_choice_id="negative-cost",
        ),
        activity=ActivitySpec(
            scenario_id="synthetic-cost-validation",
            instructions="Apply the validation rule to the two safe records.",
            seed=7,
            input_revision_sha256="4" * 64,
            actions=(
                ScenarioAction(
                    "validate",
                    "Validate records",
                    "Produce deterministic validation observations.",
                ),
                ScenarioAction(
                    "reset",
                    "Reset",
                    "Restore the same synthetic starting state.",
                ),
            ),
            renderer_capabilities=(
                "deterministic-reset/v1",
                "evidence-producing-result/v1",
                "keyboard-operable/v1",
            ),
        ),
        evidence=EvidenceExercise(
            claim="The invalid normalized record was rejected.",
            cards=(
                EvidenceCard(
                    "validation-result",
                    "Validation result",
                    "The selected normalized record failed its rule.",
                    "The downstream view refreshed.",
                    "synthetic-validation-output",
                    "one deterministic validation run",
                    "public-synthetic",
                    "2026-07-24T12:00:00Z",
                ),
                EvidenceCard(
                    "job-success",
                    "Job status",
                    "The job process completed.",
                    "Every business rule passed.",
                    "synthetic-job-browser",
                    "one process completion status",
                    "public-synthetic",
                    "2026-07-24T12:00:00Z",
                ),
            ),
            required_support=("validation-result",),
            required_reject=("job-success",),
        ),
        explanation=ExplanationPrompt(
            prompt="Explain why job success is not enough evidence.",
            minimum_characters=24,
            qualification=Prediction(
                prompt="What does a successful job prove?",
                choices=(
                    Choice("process-completed", "The process completed."),
                    Choice("all-rules-passed", "Every business rule passed."),
                ),
                expected_choice_id="process-completed",
            ),
        ),
    )


def bundle() -> LearningPackBundle:
    return LearningPackBundle.build(
        accepted_snapshot(),
        concepts=(
            Concept(
                concept_id="proof-scope",
                title="Evidence scope",
                summary="Match each operational claim to evidence of the same scope.",
            ),
        ),
        lessons=(lesson(),),
    )


class LearningPackBundleTests(unittest.TestCase):
    def test_bundle_is_strict_content_addressed_and_round_trips(self) -> None:
        first = bundle()
        second = LearningPackBundle.build(
            accepted_snapshot(),
            concepts=first.concepts,
            lessons=first.lessons,
        )

        self.assertEqual(first, second)
        self.assertEqual(
            LearningPackBundle.from_dict(first.to_dict()),
            first,
        )
        self.assertEqual(len(first.bundle_sha256), 64)

    def test_bundle_rejects_unknown_fields_and_digest_tampering(self) -> None:
        value = bundle().to_dict()
        value["private_metadata"] = "not allowed"
        with self.assertRaisesRegex(SchemaError, "fields do not match"):
            LearningPackBundle.from_dict(value)

        value = bundle().to_dict()
        value["lessons"][0]["outcome"]["statement"] = "Tampered outcome"
        with self.assertRaisesRegex(SchemaError, "outcome revision"):
            LearningPackBundle.from_dict(value)

        value = bundle().to_dict()
        value["lessons"][0]["title"] = "Tampered lesson"
        with self.assertRaisesRegex(SchemaError, "lesson revision"):
            LearningPackBundle.from_dict(value)

        value = bundle().to_dict()
        value["bundle_sha256"] = "f" * 64
        with self.assertRaisesRegex(SchemaError, "bundle_sha256"):
            LearningPackBundle.from_dict(value)

    def test_bundle_rejects_stale_lesson_references(self) -> None:
        original = lesson()
        bad_lesson = LessonBlueprint.build(
            lesson_id=original.lesson_id,
            title=original.title,
            concept_id=original.concept_id,
            claim_id="claim-" + "9" * 20,
            outcome=original.outcome,
            map_stages=original.map_stages,
            prediction=original.prediction,
            activity=original.activity,
            evidence=original.evidence,
            explanation=original.explanation,
        )
        with self.assertRaisesRegex(SchemaError, "missing accepted claim"):
            LearningPackBundle.build(
                accepted_snapshot(),
                concepts=(
                    Concept(
                        "proof-scope",
                        "Evidence scope",
                        "Evidence must match the exact claim.",
                    ),
                ),
                lessons=(bad_lesson,),
            )

    def test_activity_requires_accessible_evidence_capabilities(self) -> None:
        with self.assertRaisesRegex(SchemaError, "required renderer capability"):
            replace(
                lesson().activity,
                renderer_capabilities=("deterministic-reset/v1",),
            )

    def test_bundle_rejects_private_storage_markers(self) -> None:
        with self.assertRaisesRegex(SchemaError, "private-only"):
            replace(
                lesson().map_stages[0],
                description="Read /private/raw.bin for the real record.",
            )


if __name__ == "__main__":
    unittest.main()
