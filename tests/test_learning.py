from __future__ import annotations

import unittest

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
from ops_learning_lab.domain import SchemaError


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
            render_scenario("unknown-scenario", 7, ())
        with self.assertRaisesRegex(SchemaError, "seed"):
            render_scenario(CODEX_ETL_ACTIVITY.scenario_id, 8, ())

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
            lesson_id="lesson-codex-etl-quality",
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


if __name__ == "__main__":
    unittest.main()
