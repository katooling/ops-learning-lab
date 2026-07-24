from __future__ import annotations

from dataclasses import replace
import unittest

from ops_learning_lab.domain import SchemaError
from ops_learning_lab.learning_bundle import (
    Concept,
    LearningPackBundle,
    LessonBlueprint,
)
from tests.fixtures_learning import accepted_snapshot, bundle, lesson


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

    def test_bundle_rejects_a_different_accepted_snapshot(self) -> None:
        current = accepted_snapshot()
        stale = replace(current, version=current.version + 1)
        with self.assertRaisesRegex(SchemaError, "does not match"):
            bundle().require_snapshot(stale)

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
