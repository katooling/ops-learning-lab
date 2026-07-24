"""Deterministic matching and conservative claim extraction for Capture Mode."""

from __future__ import annotations

from hashlib import sha256
import re
from typing import Iterable

from .domain import (
    FACT_STATUSES,
    IntakeManifest,
    MatchCandidate,
    PackMatch,
    PackProfile,
    ProposedClaim,
    SchemaError,
    StagedSource,
    StagedPackUpdate,
)


COMPILER_VERSION = 1
WORD_PATTERN = re.compile(r"[a-z0-9]+")
CLAIM_PATTERN = re.compile(
    r"^\s*(?:[-*]\s*)?claim(?:\s*\[([a-z]+)\])?\s*:\s*(.+?)\s*$",
    re.IGNORECASE,
)


DEFAULT_PACK_PROFILES = (
    PackProfile(
        pack_id="codex-etl",
        title="Synthetic Codex ETL",
        match_terms=("codex", "cost", "credits", "etl", "tokens", "usage"),
    ),
    PackProfile(
        pack_id="workflow-validation",
        title="Workflow data validation",
        match_terms=(
            "freshness",
            "quality",
            "rules",
            "uniqueness",
            "validation",
            "workflow",
        ),
    ),
)


def _tokens(text: str) -> set[str]:
    return set(WORD_PATTERN.findall(text.lower()))


def propose_pack_match(
    text: str,
    profiles: Iterable[PackProfile] = DEFAULT_PACK_PROFILES,
) -> PackMatch:
    """Return a visible proposal; never silently pick among plausible packs."""

    words = _tokens(text)
    candidates = []
    for profile in sorted(profiles, key=lambda item: item.pack_id):
        matched = tuple(sorted(words.intersection(profile.match_terms)))
        if len(matched) >= 2:
            candidates.append(
                MatchCandidate(
                    pack_id=profile.pack_id,
                    title=profile.title,
                    matched_terms=matched,
                    expected_base_version=1,
                    expected_base_sha256=profile.content_sha256,
                )
            )

    if len(candidates) == 1:
        candidate = candidates[0]
        return PackMatch(
            kind="strong",
            candidates=(candidate,),
            proposed_pack_id=candidate.pack_id,
            reasons=(
                f"Matched {len(candidate.matched_terms)} accepted pack terms: "
                + ", ".join(candidate.matched_terms),
            ),
        )
    if len(candidates) > 1:
        return PackMatch(
            kind="ambiguous",
            candidates=tuple(candidates),
            proposed_pack_id=None,
            reasons=(
                "Several accepted packs share at least two terms with this intake.",
                "Learner choice is required; accepted pack content is unchanged.",
            ),
        )
    return PackMatch(
        kind="new_pack",
        candidates=(),
        proposed_pack_id=None,
        reasons=(
            "No accepted pack shares enough terms with this intake.",
            "A new Learning Pack is proposed; no existing pack was selected.",
        ),
    )


def validate_capture_text(text: str) -> None:
    """Validate every explicitly marked claim before any persistence occurs."""

    for line in text.splitlines():
        match = CLAIM_PATTERN.fullmatch(line)
        if match is None:
            continue
        status = (match.group(1) or "unverified").lower()
        if status not in FACT_STATUSES:
            raise SchemaError(
                f"unsupported Claim status {status!r}; expected one of "
                + ", ".join(sorted(FACT_STATUSES))
            )


def extract_claims(text: str, manifest: IntakeManifest) -> tuple[ProposedClaim, ...]:
    """Extract only explicitly marked claims; unmarked raw lines stay private."""

    validate_capture_text(text)
    claims = []
    seen = set()
    for line in text.splitlines():
        match = CLAIM_PATTERN.fullmatch(line)
        if match is None:
            continue
        status = (match.group(1) or "unverified").lower()
        claim_text = match.group(2).strip()
        identity = (status, claim_text)
        if identity in seen:
            continue
        seen.add(identity)
        digest = sha256(
            b"\0".join(
                (
                    manifest.intake_id.encode("ascii"),
                    status.encode("ascii"),
                    claim_text.encode("utf-8"),
                )
            )
        ).hexdigest()
        claims.append(
            ProposedClaim(
                proposal_id=f"proposal-{digest[:20]}",
                text=claim_text,
                fact_status=status,
                source_intake_id=manifest.intake_id,
                source_content_sha256=manifest.content_sha256,
            )
        )
    return tuple(claims)


def compile_update(content: bytes, manifest: IntakeManifest) -> StagedPackUpdate:
    try:
        text = content.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise SchemaError("Capture Mode accepts UTF-8 text only") from exc
    match = propose_pack_match(text)
    claims = extract_claims(text, manifest)
    source = StagedSource(
        source_type=manifest.source.source_type,
        observed_at=manifest.source.observed_at,
    )
    redactions = (
        "raw-unmarked-lines:not-copied",
        "private-source-identifier:not-copied",
    )
    digest = StagedPackUpdate.calculate_proposal_sha256(
        compiler_version=COMPILER_VERSION,
        intake_id=manifest.intake_id,
        content_sha256=manifest.content_sha256,
        source=source,
        match=match,
        proposed_claims=claims,
        redactions=redactions,
    )
    return StagedPackUpdate(
        update_id=f"update-{digest[:20]}",
        intake_id=manifest.intake_id,
        content_sha256=manifest.content_sha256,
        source=source,
        match=match,
        proposed_claims=claims,
        proposal_sha256=digest,
        redactions=redactions,
        compiler_version=COMPILER_VERSION,
    )
