"""Shared synthetic Learning Pack Bundle fixture."""

from __future__ import annotations

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
    PredictionPrompt,
    ScenarioAction,
)
from ops_learning_lab.promotion_models import (
    AcceptedClaim,
    AcceptedPackSnapshot,
    AcceptedProvenance,
    LearningPack,
    PromotionDecision,
    PromotionPlan,
    PromotionRecord,
)


def accepted_claim() -> AcceptedClaim:
    return AcceptedClaim(
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
    )


def learning_pack() -> LearningPack:
    claim = accepted_claim()
    decision = PromotionDecision(
        proposal_id=claim.provenance.proposal_id,
        action="accept",
        sanitized_text=claim.text,
        fact_status=claim.fact_status,
        history_action=claim.history_action,
        target_claim_id=None,
        sensitivity_reviewed=True,
        rejection_reason=None,
    )
    plan = PromotionPlan(
        update_id=claim.provenance.staged_update_id,
        proposal_sha256="5" * 64,
        target_pack_id="synthetic-etl",
        target_pack_title="Synthetic ETL evidence",
        expected_base_version=None,
        expected_base_sha256=None,
        decisions=(decision,),
    )
    record = PromotionRecord(
        promotion_id=plan.promotion_id,
        promotion_sha256=plan.promotion_sha256,
        update_id=plan.update_id,
        proposal_sha256=plan.proposal_sha256,
        applied_at="2026-07-24T12:00:01Z",
        base_version=None,
        base_sha256=None,
        decisions=plan.decisions,
    )
    return LearningPack.build(
        pack_id=plan.target_pack_id,
        title=plan.target_pack_title,
        version=1,
        claims=(claim,),
        promotions=(record,),
    )


def accepted_snapshot() -> AcceptedPackSnapshot:
    return AcceptedPackSnapshot.from_pack(learning_pack())


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
        prediction=PredictionPrompt(
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
            qualification=PredictionPrompt(
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
