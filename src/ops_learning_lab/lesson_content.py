"""Public synthetic content for the single version-one Learning Pack."""

from __future__ import annotations

from .activity import CODEX_ETL_ACTIVITY
from .domain import SchemaError
from .learning_bundle import (
    ActivitySpec,
    Choice,
    Concept,
    EvidenceCard,
    EvidenceExercise,
    ExplanationPrompt,
    LearningOutcome,
    LearningPackBundle,
    LessonBlueprint,
    MapStage,
    PredictionPrompt,
    ScenarioAction,
)
from .promotion_models import AcceptedPackSnapshot


OBSERVED_AT = "2026-07-24T12:00:00Z"


def build_codex_etl_bundle(
    snapshot: AcceptedPackSnapshot,
) -> LearningPackBundle:
    """Bind one reviewed Codex ETL claim to the fixed publishable lesson."""

    if not isinstance(snapshot, AcceptedPackSnapshot):
        raise SchemaError("lesson content requires an Accepted Pack snapshot")
    if snapshot.pack_id != "codex-etl":
        raise SchemaError("no public lesson content exists for this pack")
    accepted_claim = next(
        (
            claim
            for claim in snapshot.claims
            if claim.fact_status == "current"
        ),
        None,
    )
    if accepted_claim is None:
        raise SchemaError("Codex ETL lesson requires one current accepted claim")

    outcome = LearningOutcome.build(
        "evidence-scope",
        "Use evidence of the same scope as the operational claim.",
    )
    lesson = LessonBlueprint.build(
        lesson_id="lesson-codex-etl-quality",
        title="Prove what a green ETL run does not prove",
        concept_id="evidence-scope",
        claim_id=accepted_claim.claim_id,
        outcome=outcome,
        map_stages=(
            MapStage(
                "source",
                "Source response",
                "Three public synthetic usage records arrive; one repeats a key.",
            ),
            MapStage(
                "raw",
                "Raw storage",
                "All three records are retained without changing their meaning.",
            ),
            MapStage(
                "normalized",
                "Normalized data",
                "The three records become cost rows, including the duplicate.",
            ),
            MapStage(
                "validation",
                "Validation",
                "A uniqueness rule checks the rows and is configured as non-blocking.",
            ),
            MapStage(
                "downstream",
                "Downstream representation",
                "The result will show whether processing continued and what total was published.",
            ),
        ),
        prediction=PredictionPrompt(
            prompt=(
                "What happens when the non-blocking uniqueness rule sees the "
                "duplicate?"
            ),
            choices=(
                Choice(
                    "stops-before-write",
                    "The job stops before publishing downstream data.",
                ),
                Choice(
                    "continues-with-duplicate",
                    "The job completes and publishes the duplicated total.",
                ),
                Choice(
                    "deduplicates-before-write",
                    "The rule removes the duplicate before publishing.",
                ),
            ),
            expected_choice_id="continues-with-duplicate",
        ),
        activity=ActivitySpec(
            scenario_id=CODEX_ETL_ACTIVITY.scenario_id,
            instructions=(
                "Run the supplied records through the five synthetic stages. "
                "Reset restores the same input and seed."
            ),
            seed=CODEX_ETL_ACTIVITY.seed,
            input_revision_sha256=CODEX_ETL_ACTIVITY.input_sha256,
            actions=(
                ScenarioAction(
                    "run-pipeline",
                    "Run the pipeline",
                    "Produce deterministic stage observations.",
                ),
                ScenarioAction(
                    "reset-scenario",
                    "Reset the scenario",
                    "Restore the original input without changing the attempt.",
                ),
            ),
            renderer_capabilities=(
                "deterministic-reset/v1",
                "evidence-producing-result/v1",
                "keyboard-operable/v1",
            ),
        ),
        evidence=EvidenceExercise(
            claim=(
                "One duplicate normalized record reached the downstream "
                "representation because validation was non-blocking."
            ),
            cards=(
                _evidence(
                    "validation-policy",
                    "Validation policy",
                    "The uniqueness rule is configured as non-blocking.",
                    "The rule failed or downstream data changed.",
                    "synthetic-rule-configuration",
                    "one fixed scenario revision",
                ),
                _evidence(
                    "validation-result",
                    "Validation result",
                    "The current scenario state contains one duplicate excess row.",
                    "The failed rule stopped processing.",
                    "synthetic-validation-output",
                    "one deterministic validation result",
                ),
                _evidence(
                    "downstream-snapshot",
                    "Downstream snapshot",
                    "The same scenario state published 7 cents instead of 5.",
                    "The rule configuration that allowed the write.",
                    "synthetic-downstream-output",
                    "one deterministic downstream representation",
                ),
                _evidence(
                    "green-job-status",
                    "Green job status",
                    "The simulated process completed.",
                    "The data is unique, fresh, or correct.",
                    "synthetic-job-status",
                    "one process completion state",
                ),
            ),
            required_support=(
                "validation-policy",
                "validation-result",
                "downstream-snapshot",
            ),
            required_reject=("green-job-status",),
        ),
        explanation=ExplanationPrompt(
            prompt=(
                "Explain why the duplicate reached downstream and name what "
                "remains uncertain."
            ),
            minimum_characters=24,
            qualification=PredictionPrompt(
                prompt="Which mechanism caused the observed downstream result?",
                choices=(
                    Choice(
                        "nonblocking-validation",
                        "The rule reported the failure but allowed processing.",
                    ),
                    Choice(
                        "green-means-valid",
                        "A completed job guarantees every data rule passed.",
                    ),
                ),
                expected_choice_id="nonblocking-validation",
            ),
        ),
    )
    return LearningPackBundle.build(
        snapshot,
        concepts=(
            Concept(
                "evidence-scope",
                "Evidence scope",
                "Match every operational claim to evidence of the same scope.",
            ),
        ),
        lessons=(lesson,),
    )


def _evidence(
    evidence_id: str,
    title: str,
    proves: str,
    does_not_prove: str,
    source: str,
    scope: str,
) -> EvidenceCard:
    return EvidenceCard(
        evidence_id=evidence_id,
        title=title,
        proves=proves,
        does_not_prove=does_not_prove,
        source=source,
        scope=scope,
        sensitivity="public-synthetic",
        observed_at=OBSERVED_AT,
    )
