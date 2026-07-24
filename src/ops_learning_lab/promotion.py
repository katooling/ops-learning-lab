"""Validated review, preview, and atomic Promotion workflow."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timezone
import json
from typing import Any, Callable, Iterator

from .domain import PACK_ID_PATTERN, SHA256_PATTERN, SchemaError, StagedPackUpdate
from .pack_repository import PackRepository, _PromotionPackStore
from .promotion_models import (
    AcceptedClaim,
    AcceptedProvenance,
    LearningPack,
    PromotionDecision,
    PromotionError,
    PromotionPlan,
    PromotionRecord,
    StalePromotionError,
    _canonical_sha256,
    _non_empty,
)
from .staging import PackUpdateRepository


def _logical_strings(value: object) -> Iterator[str]:
    """Visit JSON strings before serialization can escape their contents."""
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for key, child in value.items():
            yield from _logical_strings(key)
            yield from _logical_strings(child)
    elif isinstance(value, (list, tuple)):
        for child in value:
            yield from _logical_strings(child)


@dataclass(frozen=True, slots=True)
class PromotionReview:
    update: StagedPackUpdate
    current_pack: LearningPack | None
    target_pack_id: str
    target_pack_title: str

    @property
    def expected_base_version(self) -> int | None:
        return self.current_pack.version if self.current_pack is not None else None

    @property
    def expected_base_sha256(self) -> str | None:
        return (
            self.current_pack.content_sha256
            if self.current_pack is not None
            else None
        )


@dataclass(frozen=True, slots=True)
class PromotionChangeSummary:
    """Deterministic learner-facing classification of reviewed changes."""

    removed: tuple[str, ...]
    retained: tuple[str, ...]
    generalized: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class PromotionPreview:
    plan: PromotionPlan
    resulting_pack: LearningPack
    preview_sha256: str
    changes: PromotionChangeSummary


@dataclass(frozen=True, slots=True)
class PromotionResult:
    pack: LearningPack
    promotion: PromotionRecord
    already_applied: bool


class PromotionService:
    """The only accepted path from staged material to a Learning Pack."""

    def __init__(
        self,
        updates: PackUpdateRepository,
        packs: PackRepository,
        *,
        clock: Callable[[], str] | None = None,
        forbidden_canaries: tuple[str, ...] = (),
    ) -> None:
        self.updates = updates
        self.packs = packs
        self._store = _PromotionPackStore(packs.root)
        self.clock = clock or (
            lambda: datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        )
        if not isinstance(forbidden_canaries, tuple) or any(
            not isinstance(canary, str) or not canary
            for canary in forbidden_canaries
        ):
            raise ValueError("forbidden_canaries must be non-empty strings")
        self.forbidden_canaries = forbidden_canaries

    def review(
        self,
        update_id: str,
        *,
        target_pack_id: str | None = None,
        target_pack_title: str | None = None,
    ) -> PromotionReview:
        update = self.updates.get(update_id)
        inferred_id, inferred_title = self._inferred_target(update)
        pack_id = target_pack_id or inferred_id
        title = target_pack_title or inferred_title
        if pack_id is None or title is None:
            raise PromotionError("review requires an explicit target Learning Pack")
        if not PACK_ID_PATTERN.fullmatch(pack_id):
            raise SchemaError("target_pack_id must be lowercase kebab-case")
        _non_empty(title, "target_pack_title")
        current = self.packs.get(pack_id)
        if current is not None and current.title != title:
            raise StalePromotionError("target Learning Pack title has changed")
        return PromotionReview(update, current, pack_id, title)

    def plan_from_dict(self, value: Any) -> PromotionPlan:
        return PromotionPlan.from_dict(value)

    def preview(self, plan: PromotionPlan) -> PromotionPreview:
        update = self._validate_plan(plan)
        current = self.packs.get(plan.target_pack_id)
        self._require_expected_base(plan, current)
        resulting = self._apply(plan, update, current, self.clock())
        self._assert_no_configured_canary(resulting)
        preview_sha256 = self._semantic_preview_sha(plan, update, current)
        return PromotionPreview(
            plan,
            resulting,
            preview_sha256,
            self._change_summary(plan, update),
        )

    def commit(
        self,
        plan: PromotionPlan,
        preview_sha256: str,
    ) -> PromotionResult:
        if not isinstance(preview_sha256, str) or not SHA256_PATTERN.fullmatch(
            preview_sha256
        ):
            raise PromotionError("preview confirmation does not match the schema")
        with self._store._locked_for_promotion():
            prior = self._store._find_promotion_by_update(plan.update_id)
            if prior is not None:
                prior_pack, prior_record = prior
                if (
                    prior_record.promotion_id == plan.promotion_id
                    and prior_record.promotion_sha256 == plan.promotion_sha256
                ):
                    return PromotionResult(prior_pack, prior_record, True)
                raise StalePromotionError(
                    "staged Pack Update already has a different Promotion decision"
                )
            update = self._validate_plan(plan)
            current = self.packs.get(plan.target_pack_id)
            self._require_expected_base(plan, current)
            applied_at = self.clock()
            resulting = self._apply(plan, update, current, applied_at)
            self._assert_no_configured_canary(resulting)
            semantic_preview = self._semantic_preview_sha(plan, update, current)
            if preview_sha256 != semantic_preview:
                raise StalePromotionError("promotion preview is stale")
            self._store._write_promoted(resulting)
            return PromotionResult(resulting, resulting.promotions[-1], False)

    def _assert_no_configured_canary(self, pack: LearningPack) -> None:
        if any(
            canary in text
            for text in _logical_strings(pack.to_dict())
            for canary in self.forbidden_canaries
        ):
            raise PromotionError("accepted pack contains a configured private canary")

    @staticmethod
    def _change_summary(
        plan: PromotionPlan,
        update: StagedPackUpdate,
    ) -> PromotionChangeSummary:
        proposed = {
            claim.proposal_id: claim for claim in update.proposed_claims
        }
        removed: list[str] = []
        retained: list[str] = []
        generalized: list[str] = []
        for decision in plan.decisions:
            claim = proposed[decision.proposal_id]
            if decision.action == "reject":
                removed.append(decision.proposal_id)
                continue
            retained.append(f"{decision.proposal_id}:safe-provenance")
            if decision.fact_status == claim.fact_status:
                retained.append(f"{decision.proposal_id}:fact-status")
            else:
                generalized.append(f"{decision.proposal_id}:fact-status")
            generalized.append(f"{decision.proposal_id}:text")
        return PromotionChangeSummary(
            removed=tuple(removed),
            retained=tuple(retained),
            generalized=tuple(generalized),
        )

    def _semantic_preview_sha(
        self,
        plan: PromotionPlan,
        update: StagedPackUpdate,
        current: LearningPack | None,
    ) -> str:
        semantic_pack = self._apply(
            plan,
            update,
            current,
            "1970-01-01T00:00:00Z",
        )
        return _canonical_sha256(
            {
                "plan_sha256": plan.promotion_sha256,
                "result_sha256": semantic_pack.content_sha256,
            }
        )

    def _validate_plan(self, plan: PromotionPlan) -> StagedPackUpdate:
        update = self.updates.get(plan.update_id)
        if update.proposal_sha256 != plan.proposal_sha256:
            raise StalePromotionError("staged Pack Update changed after review")
        if update.match.kind == "strong":
            candidate = update.match.candidates[0]
            if (
                plan.target_pack_id != candidate.pack_id
                or plan.target_pack_title != candidate.title
            ):
                raise PromotionError("strong match target does not match the proposal")
        elif update.match.kind == "ambiguous":
            candidates = {
                (candidate.pack_id, candidate.title)
                for candidate in update.match.candidates
            }
            if (plan.target_pack_id, plan.target_pack_title) not in candidates:
                raise PromotionError("ambiguous match requires a proposed target")
        proposal_ids = tuple(claim.proposal_id for claim in update.proposed_claims)
        decision_ids = tuple(decision.proposal_id for decision in plan.decisions)
        if set(proposal_ids) != set(decision_ids) or len(proposal_ids) != len(
            decision_ids
        ):
            raise PromotionError("every proposal needs exactly one decision")
        accepted_by_id = {
            claim.proposal_id: claim for claim in update.proposed_claims
        }
        for decision in plan.decisions:
            if decision.action == "accept":
                proposed = accepted_by_id[decision.proposal_id]
                assert decision.sanitized_text is not None
                if decision.sanitized_text == proposed.text:
                    raise PromotionError(
                        "accepted text must be independently sanitized, not copied"
                    )
                if (
                    proposed.fact_status == "contradicted"
                    and decision.history_action != "contradict"
                ):
                    raise PromotionError(
                        "a contradiction proposal needs an explicit contradict decision"
                    )
        return update

    @staticmethod
    def _require_expected_base(
        plan: PromotionPlan,
        current: LearningPack | None,
    ) -> None:
        actual = (
            (current.version, current.content_sha256)
            if current is not None
            else (None, None)
        )
        expected = (plan.expected_base_version, plan.expected_base_sha256)
        if actual != expected:
            raise StalePromotionError("accepted Learning Pack changed after review")
        if current is not None and current.title != plan.target_pack_title:
            raise StalePromotionError("accepted Learning Pack title changed after review")

    @staticmethod
    def _inferred_target(
        update: StagedPackUpdate,
    ) -> tuple[str | None, str | None]:
        if update.match.kind == "strong":
            candidate = update.match.candidates[0]
            return candidate.pack_id, candidate.title
        if update.match.kind == "new_pack":
            return None, None
        return None, None

    @staticmethod
    def _apply(
        plan: PromotionPlan,
        update: StagedPackUpdate,
        current: LearningPack | None,
        applied_at: str,
    ) -> LearningPack:
        existing_claims = list(current.claims if current is not None else ())
        known = {claim.claim_id: claim for claim in existing_claims}
        new_claims: list[AcceptedClaim] = []

        for decision in plan.decisions:
            if decision.action == "reject":
                continue
            assert decision.sanitized_text is not None
            assert decision.fact_status is not None
            assert decision.history_action is not None
            if decision.target_claim_id is not None:
                target = known.get(decision.target_claim_id)
                if target is None:
                    raise StalePromotionError(
                        "history decision targets a missing accepted claim"
                    )
                replacement_status = (
                    "contradicted"
                    if decision.history_action == "contradict"
                    else "historical"
                )
                replaced = replace(target, fact_status=replacement_status)
                existing_claims[existing_claims.index(target)] = replaced
                known[target.claim_id] = replaced

            claim_digest = _canonical_sha256(
                {
                    "promotion_id": plan.promotion_id,
                    "proposal_id": decision.proposal_id,
                    "text": decision.sanitized_text,
                }
            )
            claim = AcceptedClaim(
                claim_id=f"claim-{claim_digest[:20]}",
                text=decision.sanitized_text,
                fact_status=decision.fact_status,
                history_action=decision.history_action,
                target_claim_id=decision.target_claim_id,
                provenance=AcceptedProvenance(
                    source_type=update.source.source_type,
                    observed_at=update.source.observed_at,
                    staged_update_id=update.update_id,
                    proposal_id=decision.proposal_id,
                ),
            )
            known[claim.claim_id] = claim
            new_claims.append(claim)

        record = PromotionRecord(
            promotion_id=plan.promotion_id,
            promotion_sha256=plan.promotion_sha256,
            update_id=plan.update_id,
            proposal_sha256=plan.proposal_sha256,
            applied_at=applied_at,
            base_version=plan.expected_base_version,
            base_sha256=plan.expected_base_sha256,
            decisions=plan.decisions,
        )
        version = 1 if current is None else current.version + 1
        return LearningPack.build(
            pack_id=plan.target_pack_id,
            title=plan.target_pack_title,
            version=version,
            claims=tuple(existing_claims + new_claims),
            promotions=tuple(
                (current.promotions if current is not None else ()) + (record,)
            ),
        )
