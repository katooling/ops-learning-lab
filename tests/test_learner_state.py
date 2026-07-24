from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import http.client
import json
from pathlib import Path
import re
import tempfile
from threading import Thread
import unittest
from urllib.parse import urlencode

from ops_learning_lab._bound_directory import _BoundDirectory
from ops_learning_lab.attempts import EvidenceDecision
from ops_learning_lab.learner_state import EventAttemptStore, LearnerStateError
from ops_learning_lab.learning import LearnerAttemptRecord, evaluate_attempt
from ops_learning_lab.learning_service import LearningError, LearningService
from ops_learning_lab.lesson_content import build_codex_etl_bundle
from ops_learning_lab.bundle_repository import BundleRepository
from ops_learning_lab.storage import LearningHome, StorageError
from ops_learning_lab.shell import make_server
from ops_learning_lab.staging import PackUpdateRepository

from tests.test_learning import accepted_snapshot, completed_attempt


class BoundDirectoryAppendTests(unittest.TestCase):
    def test_atomic_create_never_rewrites_an_existing_event(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "events"
            root.mkdir(mode=0o700)
            with _BoundDirectory.open(root, "event directory") as bound:
                bound.atomic_create("0001.json", b'{"event":"first"}\n', 0o600)
                with self.assertRaisesRegex(StorageError, "already exists"):
                    bound.atomic_create(
                        "0001.json",
                        b'{"event":"replacement"}\n',
                        0o600,
                    )

            self.assertEqual(
                json.loads((root / "0001.json").read_bytes()),
                {"event": "first"},
            )


class AppendOnlyLearnerHistoryTests(unittest.TestCase):
    def test_event_directory_permission_drift_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            home = LearningHome.initialize(Path(directory) / "learning-home")
            events = home.root / "private/learner-state/events"
            events.chmod(0o755)
            with self.assertRaisesRegex(LearnerStateError, "0700"):
                EventAttemptStore.open(home.root)

    def test_reopen_restores_exact_checkpoint_and_idempotent_command(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            home = LearningHome.initialize(Path(directory) / "learning-home")
            final = completed_attempt()
            initial = final.evolve(
                updated_at=final.started_at,
                next_step="map",
                prediction=None,
                renderer=_ready_renderer(final),
                evidence=(),
                explanation=None,
                completed=False,
            )
            store = EventAttemptStore.open(home.root)
            store.save(
                initial,
                expected_checkpoint_sha256=None,
                command_id="command-" + "1" * 20,
            )
            predicted = initial.evolve(
                updated_at="2026-07-24T12:01:00Z",
                next_step="predict",
            )
            store.save(
                predicted,
                expected_checkpoint_sha256=initial.checkpoint_sha256,
                command_id="command-" + "2" * 20,
            )
            store.close()

            reopened = EventAttemptStore.open(home.root)
            self.assertEqual(reopened.get(initial.attempt_id), predicted)
            before = tuple((home.root / "private/learner-state/events").iterdir())
            reopened.save(
                predicted,
                expected_checkpoint_sha256=initial.checkpoint_sha256,
                command_id="command-" + "2" * 20,
            )
            after = tuple((home.root / "private/learner-state/events").iterdir())
            self.assertEqual(after, before)
            reopened.close()

    def test_stale_reset_and_corruption_fail_without_rewriting_history(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            home = LearningHome.initialize(Path(directory) / "learning-home")
            original = completed_attempt().evolve(
                updated_at=completed_attempt().started_at,
                next_step="map",
                prediction=None,
                renderer=_ready_renderer(completed_attempt()),
                evidence=(),
                explanation=None,
                completed=False,
            )
            replacement = _new_attempt_from(
                original,
                "attempt-fedcba9876543210abcd",
                "2026-07-24T12:01:00Z",
            )
            store = EventAttemptStore.open(home.root)
            store.save(original, expected_checkpoint_sha256=None)
            with self.assertRaisesRegex(LearnerStateError, "stale"):
                store.restart(
                    original.attempt_id,
                    replacement,
                    expected_checkpoint_sha256="f" * 64,
                )
            store.restart(
                original.attempt_id,
                replacement,
                expected_checkpoint_sha256=original.checkpoint_sha256,
            )
            history = store.history()
            self.assertEqual(
                tuple(entry.status for entry in history.attempts),
                ("reset", "active"),
            )
            self.assertEqual(
                history.attempts[0].reset_by_attempt_id,
                replacement.attempt_id,
            )
            store.close()

            events = home.root / "private/learner-state/events"
            last = sorted(events.iterdir())[-1]
            valid_bytes = last.read_bytes()
            last.write_text('{"broken":true}\n', encoding="utf-8")
            reopened = EventAttemptStore.open(home.root)
            with self.assertRaisesRegex(LearnerStateError, "corrupt"):
                reopened.history()
            with self.assertRaisesRegex(LearnerStateError, "corrupt"):
                reopened.save(
                    replacement.evolve(
                        updated_at="2026-07-24T12:02:00Z",
                        next_step="predict",
                    ),
                    expected_checkpoint_sha256=replacement.checkpoint_sha256,
                )
            self.assertEqual(last.read_text(encoding="utf-8"), '{"broken":true}\n')
            last.write_bytes(valid_bytes)
            self.assertEqual(
                reopened.get(replacement.attempt_id),
                replacement,
            )
            reopened.close()

    def test_completion_persists_exact_evaluated_record(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            home = LearningHome.initialize(Path(directory) / "learning-home")
            completed = completed_attempt()
            prior = completed.evolve(
                updated_at="2026-07-24T12:04:00Z",
                next_step="explain",
                explanation=None,
                completed=False,
            )
            bundle = build_codex_etl_bundle(accepted_snapshot())
            record = LearnerAttemptRecord.build(
                completed,
                evaluate_attempt(bundle, completed),
            )
            store = EventAttemptStore.open(home.root)
            store.save(prior, expected_checkpoint_sha256=None)
            store.complete(
                record,
                expected_checkpoint_sha256=prior.checkpoint_sha256,
            )
            entry = store.history().get(completed.attempt_id)
            assert entry is not None
            self.assertEqual(entry.status, "completed")
            self.assertEqual(entry.completed_record, record)
            self.assertEqual(entry.completed_at, completed.updated_at)
            store.close()


class DurableLearningJourneyTests(unittest.TestCase):
    class Packs:
        def snapshot(self, pack_id: str):
            snapshot = accepted_snapshot()
            return snapshot if pack_id == snapshot.pack_id else None

    def test_restart_restores_exact_mid_attempt_input_and_loop_state(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            home = LearningHome.initialize(Path(directory) / "learning-home")
            clock = MutableClock("2026-07-24T12:00:00Z")
            ids = iter(
                (
                    "attempt-11111111111111111111",
                    "attempt-22222222222222222222",
                )
            )
            first_store = EventAttemptStore.open(home.root)
            first = LearningService(
                self.Packs(),
                BundleRepository.open(home.root),
                first_store,
                clock=clock,
                attempt_id_factory=lambda: next(ids),
            )
            view = first.start("codex-etl", "lesson-codex-etl-quality")
            view = first.advance_map(view.attempt.attempt_id)
            view = first.predict(
                view.attempt.attempt_id,
                "continues-with-duplicate",
                4,
            )
            expected = view.attempt
            first_store.close()

            reopened_store = EventAttemptStore.open(home.root)
            reopened = LearningService(
                self.Packs(),
                BundleRepository.open(home.root),
                reopened_store,
                clock=clock,
                attempt_id_factory=lambda: next(ids),
            )
            restored = reopened.view(expected.attempt_id)
            self.assertEqual(restored.attempt, expected)
            self.assertEqual(restored.attempt.next_step, "try")
            self.assertEqual(
                restored.attempt.prediction.choice_id,
                "continues-with-duplicate",
            )
            self.assertEqual(restored.attempt.renderer.seed, 7)
            self.assertEqual(restored.attempt.renderer.effective_actions, ())
            self.assertFalse(restored.attempt.completed)

            clock.value = "2026-07-24T12:10:00Z"
            restarted = reopened.restart_attempt(expected.attempt_id)
            self.assertNotEqual(
                restarted.attempt.attempt_id,
                expected.attempt_id,
            )
            self.assertEqual(restarted.attempt.next_step, "map")
            self.assertEqual(restarted.attempt.renderer.seed, 7)
            history = reopened_store.history()
            self.assertEqual(
                tuple(entry.status for entry in history.attempts),
                ("reset", "active"),
            )
            reopened_store.close()

    def test_premature_review_is_read_only_and_due_review_proves_retention(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            home = LearningHome.initialize(Path(directory) / "learning-home")
            clock = MutableClock("2026-07-24T12:00:00Z")
            ids = iter(
                (
                    "attempt-11111111111111111111",
                    "attempt-22222222222222222222",
                )
            )
            store = EventAttemptStore.open(home.root)
            service = LearningService(
                self.Packs(),
                BundleRepository.open(home.root),
                store,
                clock=clock,
                attempt_id_factory=lambda: next(ids),
            )
            demonstrated = _complete_service_attempt(service)
            self.assertEqual(demonstrated.mastery.state, "demonstrated")
            self.assertEqual(demonstrated.review.status, "scheduled")
            self.assertEqual(
                demonstrated.review.due_at,
                "2026-07-31T12:00:00Z",
            )
            event_count = len(store.history().events)

            clock.value = "2026-07-30T12:00:00Z"
            for _ in range(2):
                overview = service.open_lesson(
                    "codex-etl",
                    "lesson-codex-etl-quality",
                )
                self.assertEqual(overview.mastery.state, "demonstrated")
                self.assertEqual(overview.review.status, "scheduled")
            with self.assertRaisesRegex(LearningError, "not due"):
                service.start_review(
                    "codex-etl",
                    "lesson-codex-etl-quality",
                )
            self.assertEqual(len(store.history().events), event_count)

            clock.value = "2026-07-31T12:00:00Z"
            review = service.start_review(
                "codex-etl",
                "lesson-codex-etl-quality",
            )
            self.assertEqual(review.review.status, "in-progress")
            retained = _complete_service_attempt(
                service,
                attempt_id=review.attempt.attempt_id,
            )
            self.assertEqual(retained.mastery.state, "retained")
            self.assertEqual(
                retained.mastery.earned_by_attempt_id,
                review.attempt.attempt_id,
            )
            self.assertEqual(retained.review.status, "retained")
            self.assertEqual(
                retained.review.demonstrated_by_attempt_id,
                demonstrated.attempt.attempt_id,
            )
            store.close()

    def test_unsuccessful_review_preserves_demonstrated_and_retries_in_one_day(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            home = LearningHome.initialize(Path(directory) / "learning-home")
            clock = MutableClock("2026-07-24T12:00:00Z")
            ids = iter(
                (
                    "attempt-11111111111111111111",
                    "attempt-22222222222222222222",
                )
            )
            store = EventAttemptStore.open(home.root)
            service = LearningService(
                self.Packs(),
                BundleRepository.open(home.root),
                store,
                clock=clock,
                attempt_id_factory=lambda: next(ids),
            )
            demonstrated = _complete_service_attempt(service)
            clock.value = "2026-07-31T12:00:00Z"
            review = service.start_review(
                "codex-etl",
                "lesson-codex-etl-quality",
            )
            unsuccessful = _complete_service_attempt(
                service,
                attempt_id=review.attempt.attempt_id,
                prediction_choice="stops-before-write",
            )
            self.assertEqual(unsuccessful.mastery.state, "demonstrated")
            self.assertEqual(
                unsuccessful.mastery.earned_by_attempt_id,
                demonstrated.attempt.attempt_id,
            )
            self.assertEqual(
                unsuccessful.record.evaluation.feedback,
                ("prediction-incorrect",),
            )
            self.assertEqual(
                unsuccessful.review.status,
                "retry-scheduled",
            )
            self.assertEqual(
                unsuccessful.review.due_at,
                "2026-08-01T12:00:00Z",
            )
            clock.value = "2026-08-01T12:00:00Z"
            self.assertEqual(
                service.open_lesson(
                    "codex-etl",
                    "lesson-codex-etl-quality",
                ).review.status,
                "due",
            )
            store.close()

    def test_product_shell_due_view_is_read_only_and_starts_exact_review(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            home = LearningHome.initialize(Path(directory) / "learning-home")
            clock = MutableClock("2026-07-24T12:00:00Z")
            ids = iter(
                (
                    "attempt-11111111111111111111",
                    "attempt-22222222222222222222",
                )
            )
            store = EventAttemptStore.open(home.root)
            service = LearningService(
                self.Packs(),
                BundleRepository.open(home.root),
                store,
                clock=clock,
                attempt_id_factory=lambda: next(ids),
            )
            _complete_service_attempt(service)
            clock.value = "2026-07-31T12:00:00Z"
            before = len(store.history().events)

            server = make_server(
                PackUpdateRepository.open(home.root),
                "127.0.0.1",
                0,
                learning=service,
            )
            thread = Thread(target=server.serve_forever, daemon=True)
            thread.start()
            connection = http.client.HTTPConnection(
                "127.0.0.1",
                server.server_address[1],
            )
            path = "/learn/codex-etl/lesson-codex-etl-quality"
            try:
                status, body, _ = _http_request(connection, "GET", path)
                self.assertEqual(status, 200)
                self.assertIn("Review due now.", body)
                self.assertIn("Begin due review", body)
                self.assertEqual(len(store.history().events), before)

                status, repeated, _ = _http_request(connection, "GET", path)
                self.assertEqual(status, 200)
                self.assertEqual(repeated, body)
                self.assertEqual(len(store.history().events), before)

                csrf = re.search(
                    r'name="csrf-token" value="([0-9a-f]{64})"',
                    body,
                )
                assert csrf is not None
                status, _, location = _http_request(
                    connection,
                    "POST",
                    f"{path}/review",
                    urlencode({"csrf-token": csrf.group(1)}),
                )
                self.assertEqual(status, 303)
                self.assertEqual(
                    location,
                    "/attempts/attempt-22222222222222222222",
                )
                self.assertEqual(len(store.history().events), before + 1)
                active = store.history().get(
                    "attempt-22222222222222222222"
                )
                assert active is not None
                self.assertEqual(active.attempt_kind, "review")
                self.assertEqual(active.status, "active")
            finally:
                connection.close()
                server.shutdown()
                server.server_close()
                thread.join(timeout=2)
                store.close()

    def test_concurrent_due_review_requests_create_one_active_review(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            home = LearningHome.initialize(Path(directory) / "learning-home")
            clock = MutableClock("2026-07-24T12:00:00Z")
            next_id = iter(
                (
                    "attempt-11111111111111111111",
                    "attempt-22222222222222222222",
                    "attempt-33333333333333333333",
                )
            )
            store = EventAttemptStore.open(home.root)
            service = LearningService(
                self.Packs(),
                BundleRepository.open(home.root),
                store,
                clock=clock,
                attempt_id_factory=lambda: next(next_id),
            )
            _complete_service_attempt(service)
            clock.value = "2026-07-31T12:00:00Z"

            def begin():
                try:
                    return service.start_review(
                        "codex-etl",
                        "lesson-codex-etl-quality",
                    ).attempt.attempt_id
                except LearningError as exc:
                    return str(exc)

            with ThreadPoolExecutor(max_workers=2) as executor:
                results = tuple(executor.map(lambda _: begin(), range(2)))

            self.assertEqual(
                sum(result.startswith("attempt-") for result in results),
                1,
            )
            self.assertIn("Review is not due yet", results)
            reviews = tuple(
                entry
                for entry in store.history().attempts
                if entry.attempt_kind == "review"
            )
            self.assertEqual(len(reviews), 1)
            self.assertEqual(reviews[0].status, "active")
            store.close()


class MutableClock:
    def __init__(self, value: str) -> None:
        self.value = value

    def __call__(self) -> str:
        return self.value


def _complete_service_attempt(
    service: LearningService,
    *,
    attempt_id: str | None = None,
    prediction_choice: str = "continues-with-duplicate",
):
    if attempt_id is None:
        view = service.start("codex-etl", "lesson-codex-etl-quality")
        attempt_id = view.attempt.attempt_id
    view = service.view(attempt_id)
    if view.attempt.next_step == "map":
        view = service.advance_map(attempt_id)
    view = service.predict(attempt_id, prediction_choice, 4)
    view = service.run_scenario(attempt_id)
    view = service.prove(
        attempt_id,
        tuple(
            EvidenceDecision(
                card.evidence_id,
                (
                    "supports"
                    if card.evidence_id
                    in view.lesson.evidence.required_support
                    else "rejects"
                ),
            )
            for card in view.lesson.evidence.cards
        ),
    )
    return service.explain(
        attempt_id,
        mechanism_choice_id="nonblocking-validation",
        text=(
            "The uniqueness rule detected the duplicate but allowed processing."
        ),
        remaining_uncertainty=(
            "This synthetic result does not prove any real invoice is correct."
        ),
        confidence_after=5,
    )


def _http_request(
    connection: http.client.HTTPConnection,
    method: str,
    path: str,
    body: str | None = None,
) -> tuple[int, str, str | None]:
    headers = {"Host": f"127.0.0.1:{connection.port}"}
    if body is not None:
        headers.update(
            {
                "Content-Type": "application/x-www-form-urlencoded",
                "Content-Length": str(len(body.encode("utf-8"))),
                "Origin": f"http://127.0.0.1:{connection.port}",
            }
        )
    connection.request(method, path, body=body, headers=headers)
    response = connection.getresponse()
    return (
        response.status,
        response.read().decode("utf-8"),
        response.getheader("Location"),
    )


def _ready_renderer(attempt):
    from ops_learning_lab.activity import render_scenario
    from ops_learning_lab.attempts import RendererCheckpoint

    return RendererCheckpoint(
        scenario_id=attempt.renderer.scenario_id,
        input_sha256=attempt.renderer.input_sha256,
        seed=attempt.renderer.seed,
        effective_actions=(),
        action_history=(),
        result=render_scenario(
            attempt.renderer.scenario_id,
            attempt.renderer.seed,
            attempt.renderer.input_sha256,
            (),
        ),
    )


def _new_attempt_from(attempt, attempt_id: str, started_at: str):
    from ops_learning_lab.attempts import AttemptCheckpoint

    return AttemptCheckpoint.build(
        attempt_id=attempt_id,
        pack_id=attempt.pack_id,
        pack_version=attempt.pack_version,
        pack_sha256=attempt.pack_sha256,
        bundle_sha256=attempt.bundle_sha256,
        lesson_id=attempt.lesson_id,
        lesson_revision_sha256=attempt.lesson_revision_sha256,
        outcome_id=attempt.outcome_id,
        outcome_revision_sha256=attempt.outcome_revision_sha256,
        started_at=started_at,
        updated_at=started_at,
        next_step="map",
        prediction=None,
        renderer=_ready_renderer(attempt),
        evidence=(),
        explanation=None,
        hints=(),
        completed=False,
    )


if __name__ == "__main__":
    unittest.main()
