from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from ops_learning_lab.codex_import import (
    CodexImportError,
    CodexImportRequest,
    CodexImportService,
    ConversationExtract,
    LearningDestination,
    ProductShellLearningPort,
    TurnSelection,
)
from ops_learning_lab.bundle_repository import BundleRepository
from ops_learning_lab.domain import SchemaError
from ops_learning_lab.learner_state import (
    EventAttemptStore,
    LearnerHistory,
)
from ops_learning_lab.learning_service import LearningService
from ops_learning_lab.promotion_models import (
    AcceptedClaim,
    AcceptedPackSnapshot,
    AcceptedProvenance,
    LearningPack,
)
from ops_learning_lab.pack_repository import PackRepository
from ops_learning_lab.promotion import PromotionService
from ops_learning_lab.staging import PackUpdateRepository
from ops_learning_lab.storage import LearningHome
from tests.fixtures_learning import learning_pack


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = REPOSITORY_ROOT / "src"


def accepted_codex_snapshot() -> AcceptedPackSnapshot:
    return AcceptedPackSnapshot(
        pack_id="codex-etl",
        title="Synthetic Codex ETL",
        version=1,
        content_sha256="a" * 64,
        claims=(
            AcceptedClaim(
                claim_id="claim-" + "1" * 20,
                text="A successful job does not prove valid downstream data.",
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


def install_accepted_codex_pack(home: LearningHome) -> None:
    fixture = learning_pack()
    pack = LearningPack.build(
        pack_id="codex-etl",
        title="Synthetic Codex ETL",
        version=fixture.version,
        claims=fixture.claims,
        promotions=fixture.promotions,
    )
    pack_root = home.root / "packs" / pack.pack_id
    pack_root.mkdir()
    (pack_root / "pack.json").write_text(
        json.dumps(pack.to_dict(), ensure_ascii=False, sort_keys=True),
        encoding="utf-8",
    )


class CodexImportCliTests(unittest.TestCase):
    def test_inline_selected_turns_capture_is_idempotent_and_preserves_scope(
        self,
    ) -> None:
        text = (
            "Selected turn 2: Synthetic Codex ETL usage cost tokens.\n"
            "Selected turn 4: Claim: Synthetic normalized cost is non-negative.\n"
        )
        request = {
            "schema_version": 1,
            "mode": "capture",
            "source": {
                "kind": "task_turns_extract",
                "task_id": "synthetic-task-7",
                "turn_ids": ["turn-2", "turn-4"],
                "observed_at": "2026-07-24T12:30:00Z",
                "text": text,
            },
        }
        with tempfile.TemporaryDirectory() as directory:
            home = LearningHome.initialize(Path(directory) / "learning-home")

            first = run_codex_import(home.root, request)
            second = run_codex_import(home.root, request)

            self.assertEqual(first.returncode, 0, first.stderr)
            self.assertEqual(first.stdout, second.stdout)
            result = json.loads(first.stdout)
            self.assertEqual(result["status"], "staged")
            self.assertFalse(result["lesson_started"])
            manifest = home.read_manifest(result["intake_id"])
            self.assertEqual(manifest.source.source_type, "codex-task")
            self.assertEqual(manifest.source.source_id, "synthetic-task-7")
            self.assertEqual(
                manifest.source.retrieval_scope,
                '{"turn_ids":["turn-2","turn-4"]}',
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
            self.assertEqual(raw, text.encode("utf-8"))
            self.assertEqual(
                len(list((home.root / "private" / "inbox").glob("intake-*"))),
                1,
            )

    def test_inline_turn_range_learn_opens_ready_to_start_route_without_attempt(
        self,
    ) -> None:
        request = {
            "schema_version": 1,
            "mode": "learn",
            "source": {
                "kind": "task_turn_range_extract",
                "task_id": "synthetic-task-8",
                "start_turn_id": "turn-2",
                "end_turn_id": "turn-4",
                "observed_at": "2026-07-24T12:45:00Z",
                "text": "Synthetic Codex ETL usage cost tokens.",
            },
        }
        with tempfile.TemporaryDirectory() as directory:
            home = LearningHome.initialize(Path(directory) / "learning-home")
            install_accepted_codex_pack(home)

            first = run_codex_import(home.root, request)
            second = run_codex_import(home.root, request)

            self.assertEqual(first.returncode, 0, first.stderr)
            self.assertEqual(first.stdout, second.stdout)
            result = json.loads(first.stdout)
            self.assertEqual(result["status"], "learning_ready")
            self.assertEqual(result["learning_disposition"], "opened")
            self.assertEqual(
                result["learning_path"],
                "/learn/codex-etl/lesson-codex-etl-quality",
            )
            self.assertIsNone(result["attempt_id"])
            self.assertFalse(result["lesson_started"])
            with EventAttemptStore.open(home.root) as attempts:
                self.assertEqual(attempts.history().events, ())

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

    def test_capture_does_not_require_the_durable_learning_lock(self) -> None:
        request = {
            "schema_version": 1,
            "mode": "capture",
            "source": {
                "kind": "pasted_text",
                "source_id": "capture-with-open-shell",
                "observed_at": "2026-07-24T12:00:00Z",
                "text": "Synthetic Codex ETL usage cost tokens.",
            },
        }
        with tempfile.TemporaryDirectory() as directory:
            home = LearningHome.initialize(Path(directory) / "learning-home")
            held_by_shell = EventAttemptStore.open(home.root)
            try:
                result = run_codex_import(home.root, request)
            finally:
                held_by_shell.close()

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(json.loads(result.stdout)["status"], "staged")

    def test_learn_reads_history_while_shell_owns_the_writer_lock(self) -> None:
        request = {
            "schema_version": 1,
            "mode": "learn",
            "source": {
                "kind": "pasted_text",
                "source_id": "novel-learn-lock-release",
                "observed_at": "2026-07-24T12:00:00Z",
                "text": "A completely novel synthetic subject.",
            },
        }
        with tempfile.TemporaryDirectory() as directory:
            home = LearningHome.initialize(Path(directory) / "learning-home")
            held_by_shell = EventAttemptStore.open(home.root)
            try:
                result = run_codex_import(home.root, request)
            finally:
                held_by_shell.close()

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(
                json.loads(result.stdout)["status"],
                "learning_incomplete",
            )
            reopened = EventAttemptStore.open(home.root)
            reopened.close()

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

    def test_inline_task_extract_rejects_whole_history_and_mixed_selection(
        self,
    ) -> None:
        unsafe_sources = (
            {
                "kind": "task_history_extract",
                "task_id": "synthetic-task-7",
                "observed_at": "2026-07-24T12:30:00Z",
                "text": "Synthetic Codex ETL usage.",
            },
            {
                "kind": "task_turns_extract",
                "task_id": "synthetic-task-7",
                "turn_ids": ["turn-2"],
                "start_turn_id": "turn-2",
                "end_turn_id": "turn-4",
                "observed_at": "2026-07-24T12:30:00Z",
                "text": "Synthetic Codex ETL usage.",
            },
            {
                "kind": "task_turns_extract",
                "task_id": "synthetic-task-7",
                "turn_ids": [" ALL "],
                "observed_at": "2026-07-24T12:30:00Z",
                "text": "Synthetic Codex ETL usage.",
            },
            {
                "kind": "task_turn_range_extract",
                "task_id": "synthetic-task-7",
                "start_turn_id": "*",
                "end_turn_id": "turn-4",
                "observed_at": "2026-07-24T12:30:00Z",
                "text": "Synthetic Codex ETL usage.",
            },
        )
        for source in unsafe_sources:
            with self.subTest(kind=source["kind"]):
                with tempfile.TemporaryDirectory() as directory:
                    home = LearningHome.initialize(
                        Path(directory) / "learning-home"
                    )
                    prior = run_codex_import(
                        home.root,
                        {
                            "schema_version": 1,
                            "mode": "capture",
                            "source": {
                                "kind": "pasted_text",
                                "source_id": "prior-synthetic-paste",
                                "observed_at": "2026-07-24T11:00:00Z",
                                "text": "Synthetic workflow validation freshness.",
                            },
                        },
                    )
                    self.assertEqual(prior.returncode, 0, prior.stderr)
                    before = {
                        path.relative_to(home.root): path.read_bytes()
                        for path in home.root.rglob("*")
                        if path.is_file()
                    }

                    result = run_codex_import(
                        home.root,
                        {
                            "schema_version": 1,
                            "mode": "capture",
                            "source": source,
                        },
                    )

                    self.assertEqual(result.returncode, 1)
                    after = {
                        path.relative_to(home.root): path.read_bytes()
                        for path in home.root.rglob("*")
                        if path.is_file()
                    }
                    self.assertEqual(after, before)


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
                '{"turn_ids":["turn-2","turn-4"]}',
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

    def test_inline_task_extract_never_calls_conversation_port(self) -> None:
        class MutationTrap:
            def __getattr__(self, name: str):
                raise AssertionError(
                    f"source capability should not be used: {name}"
                )

        request = CodexImportRequest.from_dict(
            {
                "schema_version": 1,
                "mode": "capture",
                "source": {
                    "kind": "task_turns_extract",
                    "task_id": "synthetic-task-7",
                    "turn_ids": ["turn-2"],
                    "observed_at": "2026-07-24T12:30:00Z",
                    "text": "Synthetic Codex ETL usage cost tokens.",
                },
            }
        )
        with tempfile.TemporaryDirectory() as directory:
            home = LearningHome.initialize(Path(directory) / "learning-home")

            result = CodexImportService(
                home,
                conversation_port=MutationTrap(),
            ).run(request)

            self.assertEqual(result["status"], "staged")

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

    def test_schema_version_rejects_boolean_alias_for_one(self) -> None:
        with self.assertRaisesRegex(SchemaError, "schema_version"):
            CodexImportRequest.from_dict(
                {
                    "schema_version": True,
                    "mode": "capture",
                    "source": {
                        "kind": "pasted_text",
                        "source_id": "strict-version",
                        "observed_at": "2026-07-24T12:00:00Z",
                        "text": "Synthetic Codex ETL usage.",
                    },
                }
            )

    def test_scope_identity_is_unambiguous_for_adversarial_turn_ids(self) -> None:
        self.assertNotEqual(
            TurnSelection(turn_ids=("a,b", "c")).scope(),
            TurnSelection(turn_ids=("a", "b", "c")).scope(),
        )
        self.assertNotEqual(
            TurnSelection(
                start_turn_id="a..b",
                end_turn_id="c",
            ).scope(),
            TurnSelection(
                start_turn_id="a",
                end_turn_id="b..c",
            ).scope(),
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
            updates = PackUpdateRepository.open(home.root)
            persisted = updates.get(selected["update_id"])
            self.assertEqual(persisted.match.kind, "selected")
            self.assertEqual(
                persisted.match.proposed_pack_id,
                "workflow-validation",
            )
            review = PromotionService(
                updates,
                PackRepository.open(home.root),
            ).review(selected["update_id"])
            self.assertEqual(review.target_pack_id, "workflow-validation")
            self.assertEqual(learning.calls, ["workflow-validation"])

    def test_same_content_observed_later_retains_both_observation_times(self) -> None:
        source = {
            "kind": "pasted_text",
            "source_id": "observed-synthetic-paste",
            "text": "Synthetic Codex ETL usage cost tokens.",
        }
        with tempfile.TemporaryDirectory() as directory:
            home = LearningHome.initialize(Path(directory) / "learning-home")
            service = CodexImportService(home)

            first = service.run(
                CodexImportRequest.from_dict(
                    {
                        "schema_version": 1,
                        "mode": "capture",
                        "source": {
                            **source,
                            "observed_at": "2026-07-24T12:00:00Z",
                        },
                    }
                )
            )
            later = service.run(
                CodexImportRequest.from_dict(
                    {
                        "schema_version": 1,
                        "mode": "capture",
                        "source": {
                            **source,
                            "observed_at": "2026-07-25T12:00:00Z",
                        },
                    }
                )
            )

            self.assertNotEqual(first["intake_id"], later["intake_id"])
            self.assertEqual(
                home.read_manifest(first["intake_id"]).source.observed_at,
                "2026-07-24T12:00:00Z",
            )
            self.assertEqual(
                home.read_manifest(later["intake_id"]).source.observed_at,
                "2026-07-25T12:00:00Z",
            )

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

        class NoAttemptHistory:
            @staticmethod
            def history():
                return LearnerHistory((), ())

        learning = ExistingLearningService()

        destination = ProductShellLearningPort(
            learning,
            NoAttemptHistory(),
        ).open_or_resume(
            "codex-etl"
        )

        self.assertEqual(learning.requested, ["codex-etl"])
        self.assertEqual(
            destination.path,
            "/learn/codex-etl/lesson-codex-etl-quality",
        )
        self.assertEqual(destination.disposition, "opened")
        self.assertIsNone(destination.attempt_id)

    def test_product_shell_adapter_resumes_one_durable_active_attempt(self) -> None:
        class Packs:
            @staticmethod
            def snapshot(pack_id: str):
                snapshot = accepted_codex_snapshot()
                return snapshot if pack_id == snapshot.pack_id else None

        with tempfile.TemporaryDirectory() as directory:
            home = LearningHome.initialize(Path(directory) / "learning-home")
            attempts = EventAttemptStore.open(home.root)
            bundles = BundleRepository.open(home.root)
            try:
                learning = LearningService(
                    Packs(),
                    bundles,
                    attempts,
                    clock=lambda: "2026-07-24T12:00:00Z",
                    attempt_id_factory=lambda: "attempt-0123456789abcdef0123",
                )
                started = learning.start(
                    "codex-etl",
                    "lesson-codex-etl-quality",
                )

                destination = ProductShellLearningPort(
                    learning,
                    attempts,
                ).open_or_resume("codex-etl")

                self.assertEqual(destination.disposition, "resumed")
                self.assertEqual(
                    destination.path,
                    "/attempts/attempt-0123456789abcdef0123",
                )
                self.assertEqual(
                    destination.attempt_id,
                    started.attempt.attempt_id,
                )
            finally:
                bundles.close()
                attempts.close()

    def test_product_shell_adapter_fails_closed_for_multiple_active_attempts(
        self,
    ) -> None:
        class Packs:
            @staticmethod
            def snapshot(pack_id: str):
                snapshot = accepted_codex_snapshot()
                return snapshot if pack_id == snapshot.pack_id else None

        with tempfile.TemporaryDirectory() as directory:
            home = LearningHome.initialize(Path(directory) / "learning-home")
            attempts = EventAttemptStore.open(home.root)
            bundles = BundleRepository.open(home.root)
            ids = iter(
                (
                    "attempt-11111111111111111111",
                    "attempt-22222222222222222222",
                )
            )
            try:
                learning = LearningService(
                    Packs(),
                    bundles,
                    attempts,
                    clock=lambda: "2026-07-24T12:00:00Z",
                    attempt_id_factory=lambda: next(ids),
                )
                learning.start("codex-etl", "lesson-codex-etl-quality")
                learning.start("codex-etl", "lesson-codex-etl-quality")
                event_count = len(attempts.history().events)

                with self.assertRaisesRegex(
                    CodexImportError,
                    "multiple active",
                ):
                    ProductShellLearningPort(
                        learning,
                        attempts,
                    ).open_or_resume("codex-etl")

                self.assertEqual(len(attempts.history().events), event_count)
            finally:
                bundles.close()
                attempts.close()

    def test_learning_port_error_reports_staged_artifacts_and_preserves_prior_state(
        self,
    ) -> None:
        class FailingLearningPort:
            def open_or_resume(self, pack_id: str) -> LearningDestination:
                raise CodexImportError("synthetic Product Shell failure")

        initial = CodexImportRequest.from_dict(
            {
                "schema_version": 1,
                "mode": "capture",
                "source": {
                    "kind": "pasted_text",
                    "source_id": "prior-synthetic-paste",
                    "observed_at": "2026-07-24T11:00:00Z",
                    "text": "Synthetic workflow validation freshness quality.",
                },
            }
        )
        learning_request = CodexImportRequest.from_dict(
            {
                "schema_version": 1,
                "mode": "learn",
                "source": {
                    "kind": "pasted_text",
                    "source_id": "failing-learn-paste",
                    "observed_at": "2026-07-24T12:00:00Z",
                    "text": "Synthetic Codex ETL usage cost tokens.",
                },
            }
        )
        with tempfile.TemporaryDirectory() as directory:
            home = LearningHome.initialize(Path(directory) / "learning-home")
            CodexImportService(home).run(initial)
            prior = {
                path.relative_to(home.root): path.read_bytes()
                for path in home.root.rglob("*")
                if path.is_file()
            }

            result = CodexImportService(
                home,
                learning_port=FailingLearningPort(),
            ).run(learning_request)

            self.assertEqual(result["status"], "learning_incomplete")
            self.assertIn("synthetic Product Shell failure", result["learning_error"])
            self.assertTrue(result["intake_id"])
            self.assertTrue(result["update_id"])
            current = {
                path.relative_to(home.root): path.read_bytes()
                for path in home.root.rglob("*")
                if path.is_file()
            }
            for path, content in prior.items():
                self.assertEqual(current[path], content)


if __name__ == "__main__":
    unittest.main()
