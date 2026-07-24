"""Semantic HTML views and form decoding for the local Product Shell."""

from __future__ import annotations

from html import escape

from .domain import PackMatch, StagedPackUpdate
from .promotion import PromotionChangeSummary, PromotionService
from .promotion_models import (
    LearningPack,
    PromotionDecision,
    PromotionError,
    PromotionPlan,
)
from .staging import PackUpdateRepository


STYLE = """
:root { color-scheme: light dark; font-family: system-ui, sans-serif; line-height: 1.5; }
body { max-width: 58rem; margin: 0 auto; padding: 1.5rem; }
a { color: LinkText; }
.status { border: 1px solid; border-radius: .3rem; padding: .25rem .5rem; }
.trust, .error { border-inline-start: .35rem solid; padding: .75rem 1rem; }
.error { border-color: #b42318; }
article, section, fieldset { margin-block: 1.5rem; }
fieldset { padding: 1rem; }
legend, dt { font-weight: 700; }
dd { margin-bottom: .75rem; overflow-wrap: anywhere; }
label { display: block; margin-block: .75rem .25rem; font-weight: 650; }
.choice label { display: inline-block; margin-inline-end: 1rem; font-weight: 400; }
input[type=text], textarea, select { box-sizing: border-box; max-width: 100%; width: 100%; padding: .55rem; }
textarea { min-height: 6rem; }
button { font: inherit; padding: .6rem 1rem; }
code { overflow-wrap: anywhere; }
@media (max-width: 20rem) {
  body { padding: .75rem; }
  fieldset { padding: .65rem; }
  button { width: 100%; }
}
"""


def _layout(title: str, body: str) -> bytes:
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{escape(title)} · Ops Learning Lab</title>
<style>{STYLE}</style>
</head>
<body>
<header><a href="/">Ops Learning Lab</a> · <a href="/packs">Accepted packs</a></header>
<main>{body}</main>
</body>
</html>
""".encode("utf-8")


def _match_summary(match: PackMatch) -> str:
    if match.kind == "strong":
        candidate = match.candidates[0]
        return f"Proposed destination: {candidate.title}"
    if match.kind == "selected":
        candidate = match.candidates[0]
        return f"Learner-selected destination: {candidate.title}"
    if match.kind == "ambiguous":
        return "Learner choice required"
    return "Proposed new Learning Pack"


def _index(repository: PackUpdateRepository) -> bytes:
    updates = list(repository.list())
    items = "".join(
        f'<li><a href="/updates/{escape(update.update_id)}">'
        f"{escape(update.update_id)}</a> — {escape(_match_summary(update.match))}</li>"
        for update in updates
    )
    if not items:
        items = "<li>No staged updates yet.</li>"
    return _layout(
        "Staged updates",
        "<h1>Staged Pack Updates</h1>"
        "<p>Review proposals here. Capture Mode never changes Learning Packs "
        "or starts a lesson.</p>"
        f"<ul>{items}</ul>",
    )


def _source_and_match(update: StagedPackUpdate) -> str:
    candidate_items = "".join(
        "<li>"
        f"<strong>{escape(candidate.title)}</strong> "
        f"(<code>{escape(candidate.pack_id)}</code>) — matched "
        f"{escape(', '.join(candidate.matched_terms))}"
        "</li>"
        for candidate in update.match.candidates
    )
    if not candidate_items:
        candidate_items = "<li>No accepted pack candidate.</li>"
    reasons = "".join(
        f"<li>{escape(reason)}</li>" for reason in update.match.reasons
    )
    redactions = "".join(
        f"<li>{escape(redaction.replace(':', ': '))}</li>"
        for redaction in update.redactions
    )
    return (
        '<aside class="trust"><strong>Private staged proposal.</strong> '
        "Raw intake is not available to this page.</aside>"
        '<p><span class="status">staged</span></p>'
        f"<p><strong>{escape(_match_summary(update.match))}</strong></p>"
        "<section><h2>Why this match?</h2>"
        f"<ul>{reasons}</ul><h3>Candidate destinations</h3>"
        f"<ul>{candidate_items}</ul></section>"
        "<section><h2>Safe source provenance</h2><dl>"
        f"<dt>Source type</dt><dd>{escape(update.source.source_type)}</dd>"
        f"<dt>Observed at</dt><dd>{escape(update.source.observed_at)}</dd>"
        f"<dt>Immutable proposal digest</dt><dd><code>{escape(update.proposal_sha256)}</code></dd>"
        "</dl></section>"
        f"<section><h2>Privacy redactions already applied</h2><ul>{redactions}</ul></section>"
    )


def _readonly_detail(update: StagedPackUpdate) -> bytes:
    claims = "".join(
        "<article>"
        f"<h3>{escape(claim.fact_status.title())} candidate</h3>"
        f"<p>{escape(claim.text)}</p>"
        "<dl>"
        f"<dt>Proposal ID</dt><dd><code>{escape(claim.proposal_id)}</code></dd>"
        f"<dt>Source intake</dt><dd><code>{escape(claim.source_intake_id)}</code></dd>"
        f"<dt>Source digest</dt><dd><code>{escape(claim.source_content_sha256)}</code></dd>"
        "</dl></article>"
        for claim in update.proposed_claims
    ) or "<p>No explicit claims were found.</p>"
    return _layout(
        update.update_id,
        "<h1>Staged Pack Update</h1>"
        + _source_and_match(update)
        + f"<section><h2>Proposed claims</h2>{claims}</section>"
        + "<section><h2>Capture result</h2>"
        "<p>Nothing has changed in any Learning Pack. No lesson has started.</p>"
        "<dl>"
        f"<dt>Source type</dt><dd>{escape(update.source.source_type)}</dd>"
        f"<dt>Observed at</dt><dd>{escape(update.source.observed_at)}</dd>"
        f"<dt>Immutable proposal digest</dt><dd><code>{escape(update.proposal_sha256)}</code></dd>"
        "</dl></section>",
    )


def _review_detail(
    update: StagedPackUpdate,
    csrf_token: str,
    signed_review: str = "",
    review_signature: str = "",
    accepted_packs: tuple[LearningPack, ...] = (),
) -> bytes:
    claim_fields = []
    for index, claim in enumerate(update.proposed_claims):
        prefix = f"claim-{index}"
        claim_fields.append(
            f"""<fieldset>
<legend>{escape(claim.fact_status.title())} proposal {index + 1}</legend>
<p>{escape(claim.text)}</p>
<p><small>Proposal <code>{escape(claim.proposal_id)}</code></small></p>
<div class="choice"><strong>Decision</strong>
<label><input type="radio" name="{prefix}-action" value="accept" required> Accept sanitized content</label>
<label><input type="radio" name="{prefix}-action" value="reject" required> Reject</label>
</div>
<label for="{prefix}-text">Independently written accepted text</label>
<textarea id="{prefix}-text" name="{prefix}-text" aria-describedby="{prefix}-text-help"></textarea>
<p id="{prefix}-text-help"><small>This starts blank. Do not copy private source text.</small></p>
<label for="{prefix}-status">Accepted fact status</label>
<select id="{prefix}-status" name="{prefix}-status">
<option value="" selected>Choose a status</option>
<option value="current">Current</option>
<option value="historical">Historical</option>
<option value="contradicted">Contradicted</option>
<option value="unverified">Unverified</option>
</select>
<label for="{prefix}-history">History decision</label>
<select id="{prefix}-history" name="{prefix}-history">
<option value="" selected>Choose how history changes</option>
<option value="add">Add without changing an earlier claim</option>
<option value="supersede">Supersede an earlier claim; preserve it as historical</option>
<option value="contradict">Contradict an earlier claim; preserve it as contradicted</option>
</select>
<label for="{prefix}-target">Earlier claim ID (required for supersede or contradict)</label>
<input id="{prefix}-target" name="{prefix}-target" type="text">
<div class="choice"><strong>Sensitivity review</strong>
<label><input type="checkbox" name="{prefix}-sensitivity" value="reviewed"> I removed private-only details</label>
</div>
<label for="{prefix}-reason">Structured rejection reason (required when rejecting)</label>
<select id="{prefix}-reason" name="{prefix}-reason">
<option value="" selected>Choose a reason</option>
<option value="not-relevant">Not relevant to this pack</option>
<option value="unsupported">Not supported by the evidence</option>
<option value="private">Too private to promote</option>
<option value="duplicate">Already represented safely</option>
</select>
<input type="hidden" name="{prefix}-proposal-id" value="{escape(claim.proposal_id)}">
</fieldset>"""
        )
    claims = "".join(claim_fields) or (
        "<p>No explicit proposals can be promoted from this update.</p>"
    )
    submit = (
        '<button type="submit">Preview without writing</button>'
        if update.proposed_claims
        else ""
    )
    accepted_reference = "".join(
        "<article>"
        f"<h3>{escape(pack.title)} (version {pack.version})</h3><ul>"
        + "".join(
            f"<li><code>{escape(claim.claim_id)}</code> — "
            f"{escape(claim.fact_status)} — {escape(claim.text)}</li>"
            for claim in pack.claims
        )
        + "</ul></article>"
        for pack in accepted_packs
    ) or "<p>No accepted claims exist yet.</p>"
    return _layout(
        f"Review {update.update_id}",
        "<h1>Review staged content</h1>"
        + _source_and_match(update)
        + "<form method=\"post\" "
        f'action="/updates/{escape(update.update_id)}/preview">'
        f'<input type="hidden" name="csrf-token" value="{escape(csrf_token)}">'
        f'<input type="hidden" name="signed-review" value="{escape(signed_review)}">'
        f'<input type="hidden" name="review-signature" value="{escape(review_signature)}">'
        "<fieldset><legend>Choose the accepted Learning Pack</legend>"
        "<p>No destination is selected automatically. Use a candidate shown above "
        "or enter a new pack for a new-pack proposal.</p>"
        '<label for="target-pack-id">Pack ID</label>'
        '<input id="target-pack-id" name="target-pack-id" type="text" '
        'pattern="[a-z0-9]+(?:-[a-z0-9]+)*" required>'
        '<label for="target-pack-title">Pack title</label>'
        '<input id="target-pack-title" name="target-pack-title" type="text" required>'
        "</fieldset>"
        "<section><h2>Decide every proposal</h2>"
        "<p>Accepted text starts blank. Every accepted item needs a fact status, "
        "history decision, and sensitivity review. Rejections need a reason.</p>"
        f"{claims}</section>"
        f"<section><h2>Accepted history reference</h2>{accepted_reference}</section>"
        f"{submit}</form>",
    )


def _decision_from_form(
    fields: dict[str, list[str]],
    index: int,
) -> PromotionDecision:
    prefix = f"claim-{index}"

    def value(name: str) -> str:
        values = fields.get(f"{prefix}-{name}", [""])
        if len(values) != 1:
            raise PromotionError("review form contains repeated fields")
        return values[0].strip()

    action = value("action")
    proposal_id = value("proposal-id")
    if action == "reject":
        return PromotionDecision(
            proposal_id=proposal_id,
            action=action,
            sanitized_text=None,
            fact_status=None,
            history_action=None,
            target_claim_id=None,
            sensitivity_reviewed=None,
            rejection_reason=value("reason") or None,
        )
    return PromotionDecision(
        proposal_id=proposal_id,
        action=action,
        sanitized_text=value("text") or None,
        fact_status=value("status") or None,
        history_action=value("history") or None,
        target_claim_id=value("target") or None,
        sensitivity_reviewed=(
            value("sensitivity") == "reviewed"
            if f"{prefix}-sensitivity" in fields
            else False
        ),
        rejection_reason=None,
    )


def _preview_page(
    update: StagedPackUpdate,
    plan: PromotionPlan,
    changes: PromotionChangeSummary,
    preview_sha256: str,
    csrf_token: str,
    signed_plan: str,
    signature: str,
) -> bytes:
    proposed_by_id = {
        claim.proposal_id: claim for claim in update.proposed_claims
    }
    decisions = "".join(
        "<article>"
        f"<h3>{escape(decision.proposal_id)}</h3>"
        f"<p><strong>Staged proposal:</strong> "
        f"{escape(proposed_by_id[decision.proposal_id].text)}</p>"
        f"<p><strong>Staged status:</strong> "
        f"{escape(proposed_by_id[decision.proposal_id].fact_status)}</p>"
        + (
            "<p><strong>Rejected:</strong> "
            f"{escape((decision.rejection_reason or '').replace('-', ' '))}</p>"
            if decision.action == "reject"
            else "<p><strong>Accepted text:</strong> "
            f"{escape(decision.sanitized_text or '')}</p>"
            f"<p><strong>Status:</strong> {escape(decision.fact_status or '')}; "
            f"<strong>history:</strong> {escape(decision.history_action or '')}"
            + (
                f"; <strong>target:</strong> <code>{escape(decision.target_claim_id)}</code>"
                if decision.target_claim_id
                else ""
            )
            + "</p>"
        )
        + "</article>"
        for decision in plan.decisions
    )

    def summary_items(items: tuple[str, ...]) -> str:
        return "".join(f"<li><code>{escape(item)}</code></li>" for item in items) or (
            "<li>None</li>"
        )

    return _layout(
        "Preview Promotion",
        "<h1>Preview Promotion</h1>"
        '<aside class="trust"><strong>No write has happened.</strong> '
        "Confirm only after checking the exact accepted text and history choices.</aside>"
        f"<p>Target: <strong>{escape(plan.target_pack_title)}</strong> "
        f"(<code>{escape(plan.target_pack_id)}</code>)</p>"
        "<section><h2>Deterministic change summary</h2>"
        f"<h3>Removed</h3><ul>{summary_items(changes.removed)}</ul>"
        f"<h3>Retained</h3><ul>{summary_items(changes.retained)}</ul>"
        f"<h3>Generalized</h3><ul>{summary_items(changes.generalized)}</ul>"
        "</section>"
        f"<section><h2>Exact decisions</h2>{decisions}</section>"
        f'<form method="post" action="/updates/{escape(plan.update_id)}/promote">'
        f'<input type="hidden" name="csrf-token" value="{escape(csrf_token)}">'
        f'<input type="hidden" name="signed-plan" value="{escape(signed_plan)}">'
        f'<input type="hidden" name="plan-signature" value="{escape(signature)}">'
        f'<input type="hidden" name="preview-sha256" value="{escape(preview_sha256)}">'
        '<div class="choice"><label><input type="checkbox" name="confirm" '
        'value="yes" required> Promote exactly this reviewed content</label></div>'
        '<button type="submit">Promote atomically</button>'
        "</form>"
        f'<p><a href="/updates/{escape(plan.update_id)}">Back to review</a></p>',
    )


def _pack_index(service: PromotionService) -> bytes:
    packs = list(service.packs.list())
    items = "".join(
        f'<li><a href="/packs/{escape(pack.pack_id)}">{escape(pack.title)}</a> '
        f"— version {pack.version}</li>"
        for pack in packs
    ) or "<li>No accepted Learning Packs yet.</li>"
    return _layout(
        "Accepted packs",
        "<h1>Accepted Learning Packs</h1>"
        "<p>Only explicitly promoted, sanitized content appears here.</p>"
        f"<ul>{items}</ul>",
    )


def _pack_detail(
    pack: LearningPack,
    lessons: tuple[tuple[str, str], ...] = (),
) -> bytes:
    claims = "".join(
        "<article>"
        f"<h3>{escape(claim.fact_status.title())} claim</h3>"
        f"<p>{escape(claim.text)}</p>"
        "<dl>"
        f"<dt>Claim ID</dt><dd><code>{escape(claim.claim_id)}</code></dd>"
        f"<dt>History decision</dt><dd>{escape(claim.history_action)}</dd>"
        f"<dt>Source type</dt><dd>{escape(claim.provenance.source_type)}</dd>"
        f"<dt>Observed at</dt><dd>{escape(claim.provenance.observed_at)}</dd>"
        "</dl></article>"
        for claim in pack.claims
    ) or "<p>No proposals were accepted in this pack version.</p>"
    promotions = "".join(
        "<article>"
        f"<h3>Promotion <code>{escape(record.promotion_id)}</code></h3>"
        f"<p>Applied {escape(record.applied_at)} from "
        f"<code>{escape(record.update_id)}</code>.</p>"
        f"<p>{len(record.decisions)} complete learner decision(s):</p><ul>"
        + "".join(
            "<li>"
            f"<code>{escape(decision.proposal_id)}</code> — "
            + (
                f"accepted as {escape(decision.fact_status or '')}, "
                f"{escape(decision.history_action or '')}: "
                f"{escape(decision.sanitized_text or '')}"
                if decision.action == "accept"
                else "rejected: "
                f"{escape((decision.rejection_reason or '').replace('-', ' '))}"
            )
            + "</li>"
            for decision in record.decisions
        )
        + "</ul>"
        "</article>"
        for record in pack.promotions
    )
    lesson_items = "".join(
        f'<li><a href="/learn/{escape(pack.pack_id)}/{escape(lesson_id)}">'
        f"{escape(title)}</a></li>"
        for lesson_id, title in lessons
    ) or "<li>No lesson is available for this pack.</li>"
    return _layout(
        pack.title,
        f"<h1>{escape(pack.title)}</h1>"
        f"<p><span class=\"status\">accepted</span> version {pack.version}</p>"
        f"<p>Pack digest: <code>{escape(pack.content_sha256)}</code></p>"
        f"<section><h2>Lessons</h2><ul>{lesson_items}</ul></section>"
        f"<section><h2>Accepted claims</h2>{claims}</section>"
        f"<section><h2>Promotion history</h2>{promotions}</section>",
    )


def _conflict_page(plan: PromotionPlan, message: str) -> bytes:
    preserved = "".join(
        "<li>"
        f"<code>{escape(decision.proposal_id)}</code>: {escape(decision.action)}"
        + (
            f" — {escape(decision.sanitized_text or '')}"
            if decision.action == "accept"
            else f" — {escape(decision.rejection_reason or '')}"
        )
        + "</li>"
        for decision in plan.decisions
    )
    return _layout(
        "Promotion conflict",
        "<h1>Review is stale</h1>"
        f'<aside class="error"><strong>{escape(message)}</strong> '
        "No changes were made.</aside>"
        "<p>The accepted pack or staged update changed after preview. "
        "Review the current state and preview again; this page will not auto-rebase.</p>"
        f"<h2>Your entered decisions</h2><ul>{preserved}</ul>"
        f'<p><a href="/updates/{escape(plan.update_id)}">Review current state</a></p>',
    )
