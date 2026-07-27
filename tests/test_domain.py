from __future__ import annotations

import unittest

from ops_learning_lab.domain import IntakeManifest, SchemaError, SourceReference


class IntakeManifestTests(unittest.TestCase):
    def test_round_trip_preserves_exact_schema(self) -> None:
        manifest = IntakeManifest(
            intake_id="intake-0123456789abcdefabcd",
            content_sha256="a" * 64,
            byte_count=12,
            raw_file="raw.bin",
            source=SourceReference(
                source_type="pasted-text",
                source_id="task-123",
                observed_at="2026-07-24T12:00:00Z",
            ),
        )

        self.assertEqual(IntakeManifest.from_dict(manifest.to_dict()), manifest)

    def test_round_trip_preserves_explicit_retrieval_scope(self) -> None:
        source = SourceReference(
            source_type="codex-task",
            source_id="synthetic-task-7",
            observed_at="2026-07-24T12:00:00Z",
            retrieval_scope="turn_ids:turn-2,turn-4",
        )

        self.assertEqual(SourceReference.from_dict(source.to_dict()), source)

    def test_explicit_null_retrieval_scope_fails_closed(self) -> None:
        with self.assertRaisesRegex(
            SchemaError,
            "retrieval_scope must be a non-empty string",
        ):
            SourceReference.from_dict(
                {
                    "source_type": "codex-task",
                    "source_id": "synthetic-task-7",
                    "observed_at": "2026-07-24T12:00:00Z",
                    "retrieval_scope": None,
                }
            )

    def test_unknown_fields_fail_closed(self) -> None:
        value = {
            "schema_version": 1,
            "intake_id": "intake-0123456789abcdefabcd",
            "content_sha256": "a" * 64,
            "byte_count": 12,
            "raw_file": "raw.bin",
            "source": {
                "source_type": "pasted-text",
                "source_id": "task-123",
                "observed_at": "2026-07-24T12:00:00Z",
            },
            "unexpected": True,
        }

        with self.assertRaisesRegex(SchemaError, "fields do not match"):
            IntakeManifest.from_dict(value)

    def test_timestamp_requires_timezone(self) -> None:
        with self.assertRaisesRegex(SchemaError, "include a timezone"):
            SourceReference(
                source_type="pasted-text",
                source_id="task-123",
                observed_at="2026-07-24T12:00:00",
            )
