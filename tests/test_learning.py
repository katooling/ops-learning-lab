from __future__ import annotations

import http.client
import re
import unittest
from pathlib import Path
import tempfile
from threading import Thread
from types import SimpleNamespace
from urllib.parse import urlencode

from ops_learning_lab.activity import (
    CODEX_ETL_ACTIVITY,
    ActivityResult,
    render_activity,
    render_scenario,
)
from ops_learning_lab.attempts import (
    AttemptCheckpoint,
    EvidenceDecision,
    Explanation,
    Prediction,
    RendererCheckpoint,
)
from ops_learning_lab.bundle_repository import BundleRepository
from ops_learning_lab.domain import SchemaError
from ops_learning_lab.learning import (
    LearnerAttemptRecord,
    derive_mastery,
    evaluate_attempt,
)
from ops_learning_lab.learning_service import (
    InMemoryAttemptStore,
    LearningError,
    LearningService,
)
from ops_learning_lab.lesson_content import build_codex_etl_bundle
from ops_learning_lab.promotion_models import (
    AcceptedClaim,
    AcceptedPackSnapshot,
    AcceptedProvenance,
)
from ops_learning_lab.shell import make_server
from ops_learning_lab.staging import PackUpdateRepository
from ops_learning_lab.storage import LearningHome


NOW = "2026-07-24T12:00:00Z"


def accepted_snapshot() -> AcceptedPackSnapshot:
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
                    observed_at=NOW,
                    staged_update_id="update-" + "2" * 20,
                    proposal_id="proposal-" + "3" * 20,
                ),
            ),
        ),
    )


def completed_attempt(
    *,
    prediction_choice_id: str = "continues-with-duplicate",
    evidence: tuple[EvidenceDecision, ...] | None = None,
    mechanism_choice_id: str = "nonblocking-validation",
    explanation_text: str = (
        "The uniqueness rule detected the duplicate but did not stop processing."
    ),
) -> AttemptCheckpoint:
    bundle = build_codex_etl_bundle(accepted_snapshot())
    lesson = bundle.lessons[0]
    result = render_scenario(
        lesson.activity.scenario_id,
        lesson.activity.seed,
        lesson.activity.input_revision_sha256,
        ("run-pipeline",),
    )
    decisions = evidence or tuple(
        EvidenceDecision(
            card.evidence_id,
            (
                "supports"
                if card.evidence_id in lesson.evidence.required_support
                else "rejects"
            ),
        )
        for card in lesson.evidence.cards
    )
    return AttemptCheckpoint.build(
        attempt_id="attempt-0123456789abcdef0123",
        pack_id=bundle.pack_id,
        pack_version=bundle.pack_version,
        pack_sha256=bundle.accepted_snapshot_sha256,
        bundle_sha256=bundle.bundle_sha256,
        lesson_id=lesson.lesson_id,
        lesson_revision_sha256=lesson.lesson_revision_sha256,
        outcome_id=lesson.outcome.outcome_id,
        outcome_revision_sha256=lesson.outcome.outcome_revision_sha256,
        started_at=NOW,
        updated_at="2026-07-24T12:05:00Z",
        next_step="complete",
        prediction=Prediction(
            choice_id=prediction_choice_id,
            confidence=4,
        ),
        renderer=RendererCheckpoint(
            scenario_id=lesson.activity.scenario_id,
            input_sha256=lesson.activity.input_revision_sha256,
            seed=lesson.activity.seed,
            effective_actions=("run-pipeline",),
            action_history=("run-pipeline",),
            result=result,
        ),
        evidence=decisions,
        explanation=Explanation(
            mechanism_choice_id=mechanism_choice_id,
            text=explanation_text,
            remaining_uncertainty=(
                "This synthetic result does not prove any real invoice is correct."
            ),
            confidence_after=5,
        ),
        hints=(),
        completed=True,
    )


class SyntheticEtlActivityTests(unittest.TestCase):
    def test_same_input_seed_and_action_produce_the_worked_example(self):
        first = render_activity(CODEX_ETL_ACTIVITY, ("run-pipeline",))
        second = render_activity(CODEX_ETL_ACTIVITY, ("run-pipeline",))

        self.assertEqual(first, second)
        self.assertEqual(first.input_sha256, CODEX_ETL_ACTIVITY.input_sha256)
        self.assertEqual(first.scenario_id, CODEX_ETL_ACTIVITY.scenario_id)
        self.assertEqual(first.status, "complete")
        self.assertEqual(first.source_rows, 3)
        self.assertEqual(first.raw_rows, 3)
        self.assertEqual(first.normalized_rows, 3)
        self.assertEqual(first.duplicate_excess_rows, 1)
        self.assertFalse(first.validation_passed)
        self.assertFalse(first.processing_stopped)
        self.assertTrue(first.job_completed)
        self.assertEqual(first.downstream_cost_cents, 7)
        self.assertEqual(first.unique_cost_cents, 5)
        self.assertEqual(len(first.state_sha256), 64)

    def test_reset_reproduces_initial_state_without_awarding_authority(self):
        initial = render_activity(CODEX_ETL_ACTIVITY, ())
        completed = render_activity(CODEX_ETL_ACTIVITY, ("run-pipeline",))
        reset = render_activity(CODEX_ETL_ACTIVITY, ())

        self.assertEqual(initial, reset)
        self.assertNotEqual(initial.state_sha256, completed.state_sha256)
        self.assertEqual(initial.status, "ready")
        self.assertEqual(initial.source_rows, 3)
        self.assertEqual(initial.raw_rows, 0)
        self.assertEqual(initial.downstream_cost_cents, 0)
        self.assertFalse(hasattr(initial, "mastery"))
        self.assertFalse(hasattr(initial, "navigation"))

    def test_renderer_result_rejects_prohibited_authority_fields(self):
        result = render_activity(CODEX_ETL_ACTIVITY, ("run-pipeline",)).to_dict()

        with self.assertRaisesRegex(SchemaError, "fields"):
            ActivityResult.from_dict({**result, "mastery": "demonstrated"})

    def test_unknown_scenario_or_seed_fails_closed(self):
        with self.assertRaisesRegex(SchemaError, "unknown synthetic scenario"):
            render_scenario(
                "unknown-scenario",
                7,
                CODEX_ETL_ACTIVITY.input_sha256,
                (),
            )
        with self.assertRaisesRegex(SchemaError, "seed"):
            render_scenario(
                CODEX_ETL_ACTIVITY.scenario_id,
                8,
                CODEX_ETL_ACTIVITY.input_sha256,
                (),
            )
        with self.assertRaisesRegex(SchemaError, "input revision"):
            render_scenario(
                CODEX_ETL_ACTIVITY.scenario_id,
                CODEX_ETL_ACTIVITY.seed,
                "f" * 64,
                (),
            )

    def test_renderer_rejects_unknown_or_reordered_commands(self):
        for actions in (
            ("skip-validation",),
            ("run-pipeline", "run-pipeline"),
        ):
            with self.subTest(actions=actions):
                with self.assertRaisesRegex(SchemaError, "activity actions"):
                    render_activity(CODEX_ETL_ACTIVITY, actions)


class LearnerAttemptContractTests(unittest.TestCase):
    def test_completed_checkpoint_round_trips_without_renderer_authority(self):
        result = render_activity(CODEX_ETL_ACTIVITY, ("run-pipeline",))
        checkpoint = AttemptCheckpoint.build(
            attempt_id="attempt-0123456789abcdef0123",
            pack_id="codex-etl",
            pack_version=1,
            pack_sha256="a" * 64,
            bundle_sha256="d" * 64,
            lesson_id="lesson-codex-etl-quality",
            lesson_revision_sha256="c" * 64,
            outcome_id="outcome-evidence-scope",
            outcome_revision_sha256="b" * 64,
            started_at="2026-07-24T12:00:00Z",
            updated_at="2026-07-24T12:05:00Z",
            next_step="complete",
            prediction=Prediction(
                choice_id="continues-with-duplicate",
                confidence=4,
            ),
            renderer=RendererCheckpoint(
                scenario_id=CODEX_ETL_ACTIVITY.scenario_id,
                input_sha256=CODEX_ETL_ACTIVITY.input_sha256,
                seed=CODEX_ETL_ACTIVITY.seed,
                effective_actions=("run-pipeline",),
                action_history=("run-pipeline",),
                result=result,
            ),
            evidence=(
                EvidenceDecision("validation-policy", "supports"),
                EvidenceDecision("validation-result", "supports"),
                EvidenceDecision("downstream-snapshot", "supports"),
                EvidenceDecision("green-job-status", "rejects"),
            ),
            explanation=Explanation(
                mechanism_choice_id="nonblocking-validation",
                text=(
                    "The rule reported the duplicate but allowed processing "
                    "to continue."
                ),
                remaining_uncertainty=(
                    "This does not prove any real provider invoice is correct."
                ),
                confidence_after=5,
            ),
            hints=(),
            completed=True,
        )

        reopened = AttemptCheckpoint.from_dict(checkpoint.to_dict())

        self.assertEqual(reopened, checkpoint)
        self.assertEqual(len(checkpoint.checkpoint_sha256), 64)
        self.assertFalse(hasattr(checkpoint.renderer, "mastery"))
        self.assertFalse(hasattr(checkpoint.renderer, "navigation"))
        self.assertFalse(hasattr(checkpoint.renderer, "learning_home"))

    def test_checkpoint_rejects_unknown_fields_and_cross_activity_results(self):
        result = render_activity(CODEX_ETL_ACTIVITY, ("run-pipeline",))
        renderer = RendererCheckpoint(
            scenario_id=CODEX_ETL_ACTIVITY.scenario_id,
            input_sha256=CODEX_ETL_ACTIVITY.input_sha256,
            seed=CODEX_ETL_ACTIVITY.seed,
            effective_actions=("run-pipeline",),
            action_history=("run-pipeline",),
            result=result,
        )

        with self.assertRaisesRegex(SchemaError, "fields"):
            RendererCheckpoint.from_dict({**renderer.to_dict(), "mastery": "owned"})
        with self.assertRaisesRegex(SchemaError, "does not match"):
            RendererCheckpoint(
                scenario_id="other-scenario",
                input_sha256=CODEX_ETL_ACTIVITY.input_sha256,
                seed=CODEX_ETL_ACTIVITY.seed,
                effective_actions=("run-pipeline",),
                action_history=("run-pipeline",),
                result=result,
            )

    def test_scenario_reset_keeps_attempt_and_seed_but_clears_effective_actions(self):
        initial = render_activity(CODEX_ETL_ACTIVITY, ())
        reset = RendererCheckpoint(
            scenario_id=CODEX_ETL_ACTIVITY.scenario_id,
            input_sha256=CODEX_ETL_ACTIVITY.input_sha256,
            seed=CODEX_ETL_ACTIVITY.seed,
            effective_actions=(),
            action_history=("run-pipeline", "reset-scenario"),
            result=initial,
        )

        self.assertEqual(reset.effective_actions, ())
        self.assertEqual(reset.action_history[-1], "reset-scenario")
        self.assertEqual(
            reset.result.state_sha256,
            render_activity(CODEX_ETL_ACTIVITY, ()).state_sha256,
        )


class EvidenceCenteredLearningTests(unittest.TestCase):
    def test_public_blueprint_maps_the_full_synthetic_journey(self):
        bundle = build_codex_etl_bundle(accepted_snapshot())
        lesson = bundle.lessons[0]

        self.assertEqual(
            tuple(stage.stage_id for stage in lesson.map_stages),
            ("source", "raw", "normalized", "validation", "downstream"),
        )
        self.assertEqual(
            lesson.activity.input_revision_sha256,
            CODEX_ETL_ACTIVITY.input_sha256,
        )
        self.assertEqual(
            lesson.evidence.required_reject,
            ("green-job-status",),
        )
        self.assertEqual(
            tuple(card.sensitivity for card in lesson.evidence.cards),
            (
                "public-synthetic",
                "public-synthetic",
                "public-synthetic",
                "public-synthetic",
            ),
        )

    def test_qualifying_attempt_is_deterministic_and_demonstrates_mastery(self):
        bundle = build_codex_etl_bundle(accepted_snapshot())
        attempt = completed_attempt()

        first = evaluate_attempt(bundle, attempt)
        second = evaluate_attempt(bundle, attempt)
        mastery = derive_mastery(bundle, (attempt,))

        self.assertEqual(first, second)
        self.assertTrue(first.qualifies)
        self.assertEqual(first.feedback, ())
        self.assertEqual(mastery.state, "demonstrated")
        self.assertEqual(mastery.earned_by_attempt_id, attempt.attempt_id)

    def test_terminal_record_preserves_feedback_and_fails_closed(self):
        bundle = build_codex_etl_bundle(accepted_snapshot())
        attempt = completed_attempt(
            prediction_choice_id="stops-before-write",
            evidence=(
                EvidenceDecision("validation-policy", "supports"),
                EvidenceDecision("validation-result", "supports"),
                EvidenceDecision("downstream-snapshot", "supports"),
                EvidenceDecision("green-job-status", "supports"),
            ),
        )
        evaluation = evaluate_attempt(bundle, attempt)
        record = LearnerAttemptRecord.build(attempt, evaluation)

        reopened = LearnerAttemptRecord.from_dict(record.to_dict())

        self.assertEqual(reopened, record)
        self.assertEqual(
            reopened.evaluation.feedback,
            ("prediction-incorrect", "evidence-insufficient"),
        )
        self.assertEqual(len(record.record_sha256), 64)
        with self.assertRaisesRegex(SchemaError, "fields"):
            LearnerAttemptRecord.from_dict(
                {**record.to_dict(), "mastery": "demonstrated"}
            )
        with self.assertRaisesRegex(SchemaError, "does not match"):
            LearnerAttemptRecord.build(
                completed_attempt(),
                evaluation,
            )

    def test_wrong_prediction_or_insufficient_evidence_stays_introduced(self):
        bundle = build_codex_etl_bundle(accepted_snapshot())
        wrong_prediction = completed_attempt(prediction_choice_id="stops-before-write")
        insufficient = completed_attempt(
            evidence=(
                EvidenceDecision("validation-policy", "supports"),
                EvidenceDecision("green-job-status", "supports"),
            )
        )

        self.assertIn(
            "prediction-incorrect",
            evaluate_attempt(bundle, wrong_prediction).feedback,
        )
        self.assertIn(
            "evidence-insufficient",
            evaluate_attempt(bundle, insufficient).feedback,
        )
        self.assertEqual(
            derive_mastery(bundle, (wrong_prediction, insufficient)).state,
            "introduced",
        )

    def test_structured_mechanism_and_prose_both_qualify_explanation(self):
        bundle = build_codex_etl_bundle(accepted_snapshot())
        wrong_mechanism = completed_attempt(
            mechanism_choice_id="green-means-valid",
        )
        short_prose = completed_attempt(explanation_text="Too short.")

        self.assertIn(
            "explanation-choice-incorrect",
            evaluate_attempt(bundle, wrong_mechanism).feedback,
        )
        self.assertIn(
            "explanation-too-short",
            evaluate_attempt(bundle, short_prose).feedback,
        )

    def test_advancing_past_map_is_introduced_but_not_demonstrated(self):
        bundle = build_codex_etl_bundle(accepted_snapshot())
        lesson = bundle.lessons[0]
        initial = render_scenario(
            lesson.activity.scenario_id,
            lesson.activity.seed,
            lesson.activity.input_revision_sha256,
            (),
        )
        completed = completed_attempt()
        introduced = AttemptCheckpoint.build(
            attempt_id=completed.attempt_id,
            pack_id=completed.pack_id,
            pack_version=completed.pack_version,
            pack_sha256=completed.pack_sha256,
            bundle_sha256=completed.bundle_sha256,
            lesson_id=completed.lesson_id,
            lesson_revision_sha256=completed.lesson_revision_sha256,
            outcome_id=completed.outcome_id,
            outcome_revision_sha256=completed.outcome_revision_sha256,
            started_at=completed.started_at,
            updated_at=completed.started_at,
            next_step="predict",
            prediction=None,
            renderer=RendererCheckpoint(
                scenario_id=lesson.activity.scenario_id,
                input_sha256=lesson.activity.input_revision_sha256,
                seed=lesson.activity.seed,
                effective_actions=(),
                action_history=(),
                result=initial,
            ),
            evidence=(),
            explanation=None,
            hints=(),
            completed=False,
        )

        mastery = derive_mastery(bundle, (introduced,))

        self.assertEqual(mastery.state, "introduced")
        self.assertEqual(mastery.earned_by_attempt_id, introduced.attempt_id)


class BundleRepositoryTests(unittest.TestCase):
    def test_bundle_snapshot_is_content_addressed_and_reopens_strictly(self):
        with tempfile.TemporaryDirectory() as directory:
            home = LearningHome.initialize(Path(directory) / "learning-home")
            repository = BundleRepository.open(home.root)
            bundle = build_codex_etl_bundle(accepted_snapshot())

            self.assertEqual(repository.save(bundle), bundle)
            self.assertEqual(repository.save(bundle), bundle)
            self.assertEqual(repository.snapshot(bundle.bundle_sha256), bundle)
            path = (
                home.root
                / "snapshots"
                / "learning-packs"
                / f"{bundle.bundle_sha256}.json"
            )
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)


class LearningServiceTests(unittest.TestCase):
    class Packs:
        def snapshot(self, pack_id: str):
            snapshot = accepted_snapshot()
            return snapshot if pack_id == snapshot.pack_id else None

    def service(self, directory: str) -> LearningService:
        home = LearningHome.initialize(Path(directory) / "learning-home")
        return LearningService(
            self.Packs(),
            BundleRepository.open(home.root),
            InMemoryAttemptStore(),
            clock=lambda: NOW,
            attempt_id_factory=lambda: "attempt-abcdef0123456789abcd",
        )

    def test_service_enforces_order_and_completes_one_attempt(self):
        with tempfile.TemporaryDirectory() as directory:
            service = self.service(directory)
            overview = service.open_lesson(
                "codex-etl",
                "lesson-codex-etl-quality",
            )
            self.assertIsNone(overview.attempt)
            self.assertEqual(overview.mastery.state, "captured")

            view = service.start(
                "codex-etl",
                "lesson-codex-etl-quality",
            )
            self.assertEqual(view.attempt.next_step, "map")
            with self.assertRaisesRegex(LearningError, "Map"):
                service.predict(
                    view.attempt.attempt_id,
                    "continues-with-duplicate",
                    4,
                )

            view = service.advance_map(view.attempt.attempt_id)
            self.assertEqual(view.attempt.next_step, "predict")
            self.assertEqual(view.mastery.state, "introduced")
            view = service.predict(
                view.attempt.attempt_id,
                "continues-with-duplicate",
                4,
            )
            self.assertEqual(view.attempt.next_step, "try")
            view = service.run_scenario(view.attempt.attempt_id)
            self.assertEqual(view.attempt.next_step, "prove")
            lesson = view.lesson
            decisions = tuple(
                EvidenceDecision(
                    card.evidence_id,
                    (
                        "supports"
                        if card.evidence_id
                        in lesson.evidence.required_support
                        else "rejects"
                    ),
                )
                for card in lesson.evidence.cards
            )
            view = service.prove(view.attempt.attempt_id, decisions)
            self.assertEqual(view.attempt.next_step, "explain")
            view = service.explain(
                view.attempt.attempt_id,
                mechanism_choice_id="nonblocking-validation",
                text=(
                    "The uniqueness rule reported the duplicate but allowed "
                    "processing to continue."
                ),
                remaining_uncertainty=(
                    "The synthetic run does not prove a real invoice is correct."
                ),
                confidence_after=5,
            )

            self.assertTrue(view.attempt.completed)
            self.assertTrue(view.evaluation.qualifies)
            self.assertIsNotNone(view.record)
            self.assertEqual(
                view.record.evaluation.feedback,
                (),
            )
            self.assertEqual(view.mastery.state, "demonstrated")
            self.assertEqual(
                view.mastery.earned_by_attempt_id,
                view.attempt.attempt_id,
            )

    def test_scenario_reset_keeps_attempt_and_returns_to_try(self):
        with tempfile.TemporaryDirectory() as directory:
            service = self.service(directory)
            view = service.start("codex-etl", "lesson-codex-etl-quality")
            view = service.advance_map(view.attempt.attempt_id)
            view = service.predict(
                view.attempt.attempt_id,
                "continues-with-duplicate",
                3,
            )
            view = service.run_scenario(view.attempt.attempt_id)
            completed_hash = view.attempt.renderer.result.state_sha256
            attempt_id = view.attempt.attempt_id
            view = service.reset_scenario(attempt_id)

            self.assertEqual(view.attempt.attempt_id, attempt_id)
            self.assertEqual(view.attempt.next_step, "try")
            self.assertEqual(view.attempt.renderer.effective_actions, ())
            self.assertNotEqual(
                view.attempt.renderer.result.state_sha256,
                completed_hash,
            )
            self.assertEqual(
                view.attempt.renderer.result.state_sha256,
                render_activity(CODEX_ETL_ACTIVITY, ()).state_sha256,
            )


class LearningHttpJourneyTests(unittest.TestCase):
    class Packs:
        def snapshot(self, pack_id: str):
            snapshot = accepted_snapshot()
            return snapshot if pack_id == snapshot.pack_id else None

    def test_get_is_read_only_and_http_journey_records_full_attempt(self):
        with tempfile.TemporaryDirectory() as directory:
            home = LearningHome.initialize(Path(directory) / "learning-home")
            bundles = BundleRepository.open(home.root)
            attempts = InMemoryAttemptStore()
            learning = LearningService(
                self.Packs(),
                bundles,
                attempts,
                clock=lambda: NOW,
                attempt_id_factory=lambda: "attempt-fedcba9876543210abcd",
            )
            server = make_server(
                PackUpdateRepository.open(home.root),
                "127.0.0.1",
                0,
                learning=learning,
            )
            thread = Thread(target=server.serve_forever, daemon=True)
            thread.start()
            connection = http.client.HTTPConnection(
                "127.0.0.1",
                server.server_address[1],
            )
            try:
                path = "/learn/codex-etl/lesson-codex-etl-quality"
                status, body, _ = self._request(connection, "GET", path)
                self.assertEqual(status, 200)
                self.assertIn("Map", body)
                self.assertIn("Predict", body)
                self.assertEqual(tuple(bundles.root.iterdir()), ())

                status, repeated, _ = self._request(connection, "GET", path)
                self.assertEqual(status, 200)
                self.assertEqual(repeated, body)
                self.assertEqual(tuple(bundles.root.iterdir()), ())

                csrf = self._csrf(body)
                status, _, location = self._post(
                    connection,
                    f"{path}/begin",
                    {"csrf-token": csrf},
                )
                self.assertEqual(status, 303)
                self.assertEqual(len(tuple(bundles.root.iterdir())), 1)
                attempt_id = location.removeprefix("/attempts/")

                body = self._follow(connection, location, "Map")
                checkpoint = attempts.get(attempt_id)
                assert checkpoint is not None
                before_get = checkpoint.checkpoint_sha256
                self._follow(connection, location, "Map")
                self.assertEqual(
                    attempts.get(attempt_id).checkpoint_sha256,
                    before_get,
                )

                body = self._action(connection, location, "map", body, {})
                body = self._action(
                    connection,
                    location,
                    "predict",
                    body,
                    {
                        "choice-id": "continues-with-duplicate",
                        "confidence": "4",
                    },
                )
                body = self._action(connection, location, "run", body, {})
                self.assertIn("7 cents", body)
                body = self._action(
                    connection,
                    location,
                    "prove",
                    body,
                    {
                        "evidence-validation-policy": "supports",
                        "evidence-validation-result": "supports",
                        "evidence-downstream-snapshot": "supports",
                        "evidence-green-job-status": "rejects",
                    },
                )
                body = self._action(
                    connection,
                    location,
                    "explain",
                    body,
                    {
                        "mechanism-choice-id": "nonblocking-validation",
                        "explanation": (
                            "The rule reported the duplicate but allowed the "
                            "downstream write to continue."
                        ),
                        "uncertainty": (
                            "The synthetic evidence does not prove a real "
                            "provider invoice is correct."
                        ),
                        "confidence-after": "5",
                    },
                )

                self.assertIn("Mastery: Demonstrated", body)
                completed = attempts.get(attempt_id)
                assert completed is not None
                self.assertTrue(completed.completed)
                self.assertEqual(completed.next_step, "complete")
            finally:
                connection.close()
                server.shutdown()
                server.server_close()
                thread.join(timeout=2)

    def test_pack_page_stays_available_when_optional_lesson_is_absent(self):
        historical = AcceptedPackSnapshot(
            pack_id="codex-etl",
            title="Historical Codex ETL",
            version=1,
            content_sha256="b" * 64,
            claims=(
                AcceptedClaim(
                    claim_id="claim-" + "4" * 20,
                    text="This accepted claim is retained as history.",
                    fact_status="historical",
                    history_action="add",
                    target_claim_id=None,
                    provenance=AcceptedProvenance(
                        source_type="synthetic-note",
                        observed_at=NOW,
                        staged_update_id="update-" + "5" * 20,
                        proposal_id="proposal-" + "6" * 20,
                    ),
                ),
            ),
        )
        other = AcceptedPackSnapshot(
            pack_id="other-pack",
            title="Other accepted pack",
            version=1,
            content_sha256="c" * 64,
            claims=accepted_snapshot().claims,
        )
        for snapshot in (other, historical):
            with self.subTest(pack_id=snapshot.pack_id):
                with tempfile.TemporaryDirectory() as directory:
                    home = LearningHome.initialize(
                        Path(directory) / "learning-home"
                    )

                    class Packs:
                        def snapshot(self, pack_id: str):
                            return snapshot if pack_id == snapshot.pack_id else None

                        def get(self, pack_id: str):
                            if pack_id != snapshot.pack_id:
                                return None
                            return SimpleNamespace(
                                pack_id=snapshot.pack_id,
                                title=snapshot.title,
                                version=snapshot.version,
                                content_sha256=snapshot.content_sha256,
                                claims=(),
                                promotions=(),
                            )

                    packs = Packs()
                    learning = LearningService(
                        packs,
                        BundleRepository.open(home.root),
                        InMemoryAttemptStore(),
                    )
                    promotion = SimpleNamespace(packs=packs)
                    server = make_server(
                        PackUpdateRepository.open(home.root),
                        "127.0.0.1",
                        0,
                        promotion=promotion,
                        learning=learning,
                    )
                    thread = Thread(target=server.serve_forever, daemon=True)
                    thread.start()
                    connection = http.client.HTTPConnection(
                        "127.0.0.1",
                        server.server_address[1],
                    )
                    try:
                        status, body, _ = self._request(
                            connection,
                            "GET",
                            f"/packs/{snapshot.pack_id}",
                        )
                        self.assertEqual(status, 200)
                        self.assertIn(
                            "No lesson is available for this pack.",
                            body,
                        )
                        status, _, _ = self._request(
                            connection,
                            "GET",
                            f"/learn/{snapshot.pack_id}/"
                            "lesson-codex-etl-quality",
                        )
                        self.assertEqual(status, 404)
                    finally:
                        connection.close()
                        server.shutdown()
                        server.server_close()
                        thread.join(timeout=2)

    @staticmethod
    def _request(connection, method: str, path: str, body: str | None = None):
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
        decoded = response.read().decode("utf-8")
        return response.status, decoded, response.getheader("Location")

    @staticmethod
    def _csrf(body: str) -> str:
        match = re.search(r'name="csrf-token" value="([0-9a-f]{64})"', body)
        if match is None:
            raise AssertionError("page did not contain a CSRF token")
        return match.group(1)

    def _post(self, connection, path: str, fields: dict[str, str]):
        return self._request(
            connection,
            "POST",
            path,
            urlencode(fields),
        )

    def _follow(self, connection, path: str, expected: str) -> str:
        status, body, _ = self._request(connection, "GET", path)
        self.assertEqual(status, 200)
        self.assertIn(f"<h2>{expected}</h2>", body)
        return body

    def _action(
        self,
        connection,
        attempt_path: str,
        action: str,
        current_body: str,
        fields: dict[str, str],
    ) -> str:
        status, _, location = self._post(
            connection,
            f"{attempt_path}/{action}",
            {"csrf-token": self._csrf(current_body), **fields},
        )
        self.assertEqual(status, 303)
        self.assertEqual(location, attempt_path)
        status, next_body, _ = self._request(connection, "GET", location)
        self.assertEqual(status, 200)
        return next_body


if __name__ == "__main__":
    unittest.main()
