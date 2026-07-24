from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from ops_learning_lab.codex_import import (
    CodexImportRequest,
    CodexImportService,
    ConversationExtract,
    LearningDestination,
    ProductShellLearningPort,
    TurnSelection,
)
from ops_learning_lab.domain import SchemaError
from ops_learning_lab.storage import LearningHome


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = REPOSITORY_ROOT / "src"


def run_codex_import(home: Path, request: dict[str, object]) -> subprocess.CompletedProcess[str]:
    environment = os.environ.copy()
    environment["PYTHONPATH"] = str(SOURCE_ROOT)
    return subprocess.run(
        [
            sys.executable,
            "-m",
            "ops_learning_lab",
            "codex-import",
            "--home",
            str(home),
        ],
        cwd=REPOSITORY_ROOT,
        env=environment,
        input=json.dumps(request),
        text=True,
        capture_output=True,
        check=False,
    )


class CodexImportCliTests(unittest.TestCase):
    def test_exact_pasted_text_is_staged_once_with_provenance(self) -> None:
        text = (
            "Synthetic Codex ETL usage cost tokens.\n"
            "Claim: Synthetic normalized cost is non-negative.\n"
        )
        request = {
            "schema_version": 1,
            "mode": "capture",
            "source": {
                "kind": "pasted_text",
                "source_id": "synthetic-codex-paste",
                "observed_at": "2026-07-24T12:00:00Z",
                "text": text,
            },
        }
        with tempfile.TemporaryDirectory() as directory:
            home_path = Path(directory) / "learning-home"
            home = LearningHome.initialize(home_path)

            first = run_codex_import(home_path, request)
            second = run_codex_import(home_path, request)

            self.assertEqual(first.returncode, 0, first.stderr)
            self.assertEqual(first.stdout, second.stdout)
            result = json.loads(first.stdout)
            self.assertEqual(result["status"], "staged")
            self.assertEqual(result["mode"], "capture")
            self.assertFalse(result["lesson_started"])
            self.assertEqual(result["proposed_pack_id"], "codex-etl")
            manifest = home.read_manifest(result["intake_id"])
            self.assertEqual(manifest.source.source_type, "codex-pasted-text")
            self.assertEqual(
                manifest.source.source_id,
                "synthetic-codex-paste",
            )
            self.assertEqual(
                manifest.source.observed_at,
                "2026-07-24T12:00:00Z",
            )
            self.assertEqual(
                manifest.source.retrieval_scope,
                "exact-pasted-text",
            )
            raw = (
                home.root
                / "private"
                / "inbox"
                / manifest.intake_id
                / manifest.raw_file
            ).read_bytes()
            self.assertEqual(raw, text.encode("utf-8"))
            self.assertEqual(
                len(list((home.root / "private" / "inbox").glob("intake-*"))),
                1,
            )

    def test_strict_stdin_rejects_unknown_fields_without_writing(self) -> None:
        request = {
            "schema_version": 1,
            "mode": "capture",
            "source": {
                "kind": "pasted_text",
                "source_id": "synthetic-codex-paste",
                "observed_at": "2026-07-24T12:00:00Z",
                "text": "Synthetic Codex ETL cost usage.",
                "unexpected": "not allowed",
            },
        }
        with tempfile.TemporaryDirectory() as directory:
            home_path = Path(directory) / "learning-home"
            LearningHome.initialize(home_path)

            result = run_codex_import(home_path, request)

            self.assertEqual(result.returncode, 1)
            self.assertIn("fields do not match the schema", result.stderr)
            self.assertEqual(
                list((home_path / "private" / "inbox").iterdir()),
                [],
            )
            self.assertEqual(
                list((home_path / "staged" / "updates").iterdir()),
                [],
            )

    def test_task_request_fails_closed_when_no_read_port_is_configured(self) -> None:
        request = {
            "schema_version": 1,
            "mode": "capture",
            "source": {
                "kind": "task_turn_range",
                "task_id": "synthetic-task-7",
                "start_turn_id": "turn-2",
                "end_turn_id": "turn-4",
            },
        }
        with tempfile.TemporaryDirectory() as directory:
            home_path = Path(directory) / "learning-home"
            LearningHome.initialize(home_path)

            result = run_codex_import(home_path, request)

            self.assertEqual(result.returncode, 1)
            self.assertIn("task reads are not configured", result.stderr)
            self.assertEqual(
                list((home_path / "private" / "inbox").iterdir()),
                [],
            )


class CodexImportServiceTests(unittest.TestCase):
    class ReadOnlyConversationPort:
        def __init__(self, extract: ConversationExtract) -> None:
            self.extract = extract
            self.calls: list[tuple[str, TurnSelection]] = []

        def read_selected(
            self,
            task_id: str,
            selection: TurnSelection,
        ) -> ConversationExtract:
            self.calls.append((task_id, selection))
            return self.extract

        def __getattr__(self, name: str):
            raise AssertionError(f"mutation or unknown task access attempted: {name}")

    class LearningPort:
        def __init__(self) -> None:
            self.calls: list[str] = []

        def open_or_resume(self, pack_id: str) -> LearningDestination:
            self.calls.append(pack_id)
            return LearningDestination(
                path="/attempts/attempt-0123456789abcdef0123",
                disposition="resumed",
                attempt_id="attempt-0123456789abcdef0123",
            )

    def test_selected_task_turns_preserve_scope_and_never_mutate_source(self) -> None:
        selection = TurnSelection(turn_ids=("turn-2", "turn-4"))
        exact_extract = (
            b'{"task_id":"synthetic-task-7","turns":'
            b'[{"turn_id":"turn-2","text":"Codex ETL usage."},'
            b'{"turn_id":"turn-4","text":"Claim: Cost stays non-negative."}]}'
        )
        conversation = self.ReadOnlyConversationPort(
            ConversationExtract(
                task_id="synthetic-task-7",
                selection=selection,
                observed_at="2026-07-24T12:30:00Z",
                content=exact_extract,
            )
        )
        request = CodexImportRequest.from_dict(
            {
                "schema_version": 1,
                "mode": "capture",
                "source": {
                    "kind": "task_turns",
                    "task_id": "synthetic-task-7",
                    "turn_ids": ["turn-2", "turn-4"],
                },
            }
        )
        with tempfile.TemporaryDirectory() as directory:
            home = LearningHome.initialize(Path(directory) / "learning-home")
            service = CodexImportService(home, conversation_port=conversation)

            result = service.run(request)

            self.assertEqual(
                conversation.calls,
                [("synthetic-task-7", selection)],
            )
            manifest = home.read_manifest(result["intake_id"])
            self.assertEqual(manifest.source.source_type, "codex-task")
            self.assertEqual(manifest.source.source_id, "synthetic-task-7")
            self.assertEqual(
                manifest.source.retrieval_scope,
                "turn_ids:turn-2,turn-4",
            )
            self.assertEqual(
                manifest.source.observed_at,
                "2026-07-24T12:30:00Z",
            )
            raw = (
                home.root
                / "private"
                / "inbox"
                / manifest.intake_id
                / manifest.raw_file
            ).read_bytes()
            self.assertEqual(raw, exact_extract)

    def test_task_import_requires_an_explicit_selection(self) -> None:
        with self.assertRaisesRegex(SchemaError, "fields do not match"):
            CodexImportRequest.from_dict(
                {
                    "schema_version": 1,
                    "mode": "capture",
                    "source": {
                        "kind": "task_turns",
                        "task_id": "synthetic-task-7",
                    },
                }
            )

    def test_capture_ambiguity_stages_but_does_not_open_learning(self) -> None:
        learning = self.LearningPort()
        request = CodexImportRequest.from_dict(
            {
                "schema_version": 1,
                "mode": "capture",
                "source": {
                    "kind": "pasted_text",
                    "source_id": "ambiguous-synthetic-paste",
                    "observed_at": "2026-07-24T12:00:00Z",
                    "text": (
                        "Codex ETL cost usage plus workflow validation "
                        "freshness quality."
                    ),
                },
            }
        )
        with tempfile.TemporaryDirectory() as directory:
            home = LearningHome.initialize(Path(directory) / "learning-home")
            service = CodexImportService(home, learning_port=learning)

            result = service.run(request)

            self.assertEqual(result["status"], "learner_choice_required")
            self.assertEqual(
                result["candidate_pack_ids"],
                ["codex-etl", "workflow-validation"],
            )
            self.assertEqual(learning.calls, [])
            self.assertFalse(result["lesson_started"])

    def test_learn_routes_to_resume_capable_product_shell_port(self) -> None:
        learning = self.LearningPort()
        request = CodexImportRequest.from_dict(
            {
                "schema_version": 1,
                "mode": "learn",
                "source": {
                    "kind": "pasted_text",
                    "source_id": "learn-synthetic-paste",
                    "observed_at": "2026-07-24T12:00:00Z",
                    "text": "Synthetic Codex ETL usage cost tokens.",
                },
            }
        )
        with tempfile.TemporaryDirectory() as directory:
            home = LearningHome.initialize(Path(directory) / "learning-home")
            service = CodexImportService(home, learning_port=learning)

            result = service.run(request)

            self.assertEqual(learning.calls, ["codex-etl"])
            self.assertEqual(result["status"], "learning_ready")
            self.assertEqual(result["learning_disposition"], "resumed")
            self.assertEqual(
                result["learning_path"],
                "/attempts/attempt-0123456789abcdef0123",
            )
            self.assertEqual(
                result["attempt_id"],
                "attempt-0123456789abcdef0123",
            )

    def test_selected_pack_is_required_before_ambiguous_learn(self) -> None:
        learning = self.LearningPort()
        base = {
            "schema_version": 1,
            "mode": "learn",
            "source": {
                "kind": "pasted_text",
                "source_id": "ambiguous-learn-paste",
                "observed_at": "2026-07-24T12:00:00Z",
                "text": (
                    "Codex ETL cost usage plus workflow validation "
                    "freshness quality."
                ),
            },
        }
        with tempfile.TemporaryDirectory() as directory:
            home = LearningHome.initialize(Path(directory) / "learning-home")
            service = CodexImportService(home, learning_port=learning)

            unresolved = service.run(CodexImportRequest.from_dict(base))
            selected = service.run(
                CodexImportRequest.from_dict(
                    {**base, "selected_pack_id": "workflow-validation"}
                )
            )

            self.assertEqual(unresolved["status"], "learner_choice_required")
            self.assertEqual(selected["status"], "learning_ready")
            self.assertEqual(
                selected["selected_pack_id"],
                "workflow-validation",
            )
            self.assertEqual(learning.calls, ["workflow-validation"])

    def test_learn_with_no_existing_pack_match_stages_and_explains_gap(self) -> None:
        learning = self.LearningPort()
        request = CodexImportRequest.from_dict(
            {
                "schema_version": 1,
                "mode": "learn",
                "source": {
                    "kind": "pasted_text",
                    "source_id": "novel-synthetic-paste",
                    "observed_at": "2026-07-24T12:00:00Z",
                    "text": "A completely novel synthetic subject.",
                },
            }
        )
        with tempfile.TemporaryDirectory() as directory:
            home = LearningHome.initialize(Path(directory) / "learning-home")

            result = CodexImportService(
                home,
                learning_port=learning,
            ).run(request)

            self.assertEqual(result["status"], "learning_incomplete")
            self.assertIn("new Learning Pack", result["learning_error"])
            self.assertEqual(learning.calls, [])
            self.assertFalse(result["lesson_started"])

    def test_wrong_port_scope_preserves_every_existing_file(self) -> None:
        initial = CodexImportRequest.from_dict(
            {
                "schema_version": 1,
                "mode": "capture",
                "source": {
                    "kind": "pasted_text",
                    "source_id": "existing-synthetic-paste",
                    "observed_at": "2026-07-24T12:00:00Z",
                    "text": "Synthetic Codex ETL usage cost tokens.",
                },
            }
        )
        requested_selection = TurnSelection(turn_ids=("turn-2",))
        wrong_port = self.ReadOnlyConversationPort(
            ConversationExtract(
                task_id="different-task",
                selection=requested_selection,
                observed_at="2026-07-24T12:30:00Z",
                content=b"Synthetic Codex ETL usage.",
            )
        )
        targeted = CodexImportRequest.from_dict(
            {
                "schema_version": 1,
                "mode": "capture",
                "source": {
                    "kind": "task_turns",
                    "task_id": "synthetic-task-7",
                    "turn_ids": ["turn-2"],
                },
            }
        )
        with tempfile.TemporaryDirectory() as directory:
            home = LearningHome.initialize(Path(directory) / "learning-home")
            CodexImportService(home).run(initial)
            before = {
                path.relative_to(home.root): path.read_bytes()
                for path in home.root.rglob("*")
                if path.is_file()
            }

            with self.assertRaisesRegex(
                RuntimeError,
                "different task retrieval scope",
            ):
                CodexImportService(
                    home,
                    conversation_port=wrong_port,
                ).run(targeted)

            after = {
                path.relative_to(home.root): path.read_bytes()
                for path in home.root.rglob("*")
                if path.is_file()
            }
            self.assertEqual(after, before)

    def test_product_shell_adapter_returns_existing_lesson_route(self) -> None:
        class Lesson:
            lesson_id = "lesson-codex-etl-quality"

        class ExistingLearningService:
            def __init__(self) -> None:
                self.requested: list[str] = []

            def available_lessons(self, pack_id: str):
                self.requested.append(pack_id)
                return (Lesson(),)

        learning = ExistingLearningService()

        destination = ProductShellLearningPort(learning).open_or_resume(
            "codex-etl"
        )

        self.assertEqual(learning.requested, ["codex-etl"])
        self.assertEqual(
            destination.path,
            "/learn/codex-etl/lesson-codex-etl-quality",
        )
        self.assertEqual(destination.disposition, "opened")
        self.assertIsNone(destination.attempt_id)


if __name__ == "__main__":
    unittest.main()
