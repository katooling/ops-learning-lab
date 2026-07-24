"""Semantic, no-JavaScript views for one evidence-centered lesson."""

from __future__ import annotations

from html import escape

from .learning_service import LearningView
from .shell_views import _layout


STEPS = ("map", "predict", "try", "prove", "explain", "complete")


def lesson_overview(view: LearningView, csrf_token: str) -> bytes:
    lesson = view.lesson
    stages = "".join(
        f"<li><strong>{escape(stage.title)}</strong> — "
        f"{escape(stage.description)}</li>"
        for stage in lesson.map_stages
    )
    return _layout(
        lesson.title,
        (
            f"<h1>{escape(lesson.title)}</h1>"
            f"<p>{escape(lesson.outcome.statement)}</p>"
            f"<p><span class=\"status\">"
            f"{escape(view.mastery.state.title())}</span></p>"
        )
        + _review_status(view, csrf_token)
        + (
            "<p><strong>Learning loop:</strong> Map → Predict → Try → Prove → "
            "Explain → Review.</p>"
            "<section><h2>What you will trace</h2>"
            f"<ol>{stages}</ol></section>"
            "<aside class=\"trust\"><strong>Public synthetic lesson.</strong> "
            "The activity uses fixed local records. It does not read private "
            "intake or any live production system.</aside>"
            f'<form method="post" action="/learn/{escape(view.bundle.pack_id)}/'
            f'{escape(lesson.lesson_id)}/begin">'
            f'<input type="hidden" name="csrf-token" '
            f'value="{escape(csrf_token)}">'
            '<button type="submit">Begin lesson</button></form>'
        )
        + _history(view),
    )


def attempt_page(view: LearningView, csrf_token: str) -> bytes:
    attempt = view.attempt
    if attempt is None:
        raise ValueError("attempt_page requires a Learner Attempt")
    lesson = view.lesson
    return _layout(
        lesson.title,
        f"<h1>{escape(lesson.title)}</h1>"
        + _progress(attempt.next_step)
        + _step(view, csrf_token)
        + (
            _form(
                view,
                csrf_token,
                "restart",
                '<button type="submit">Restart this whole attempt</button>',
            )
            if not attempt.completed
            else ""
        )
        + "<hr><details><summary>Attempt identity</summary><dl>"
        f"<dt>Learner Attempt ID</dt><dd><code>{escape(attempt.attempt_id)}</code></dd>"
        f"<dt>Lesson revision</dt><dd><code>{escape(attempt.lesson_revision_sha256)}</code></dd>"
        f"<dt>Bundle snapshot</dt><dd><code>{escape(attempt.bundle_sha256)}</code></dd>"
        "</dl></details>",
    )


def _progress(current: str) -> str:
    items = "".join(
        "<li"
        + (' aria-current="step"' if step == current else "")
        + f">{escape(step.title())}</li>"
        for step in STEPS
    )
    return f'<nav aria-label="Learning loop"><ol>{items}</ol></nav>'


def _step(view: LearningView, csrf_token: str) -> str:
    attempt = view.attempt
    assert attempt is not None
    dispatch = {
        "map": _map,
        "predict": _predict,
        "try": _try,
        "prove": _prove,
        "explain": _explain,
        "complete": _complete,
    }
    return dispatch[attempt.next_step](view, csrf_token)


def _form(view: LearningView, csrf_token: str, action: str, body: str) -> str:
    attempt = view.attempt
    assert attempt is not None
    return (
        f'<form method="post" action="/attempts/{escape(attempt.attempt_id)}/{action}">'
        f'<input type="hidden" name="csrf-token" value="{escape(csrf_token)}">'
        f"{body}</form>"
    )


def _map(view: LearningView, csrf_token: str) -> str:
    stages = "".join(
        f"<li><h3>{escape(stage.title)}</h3>"
        f"<p>{escape(stage.description)}</p></li>"
        for stage in view.lesson.map_stages
    )
    return (
        "<section><h2>Map</h2>"
        "<p>Follow one record path before predicting the result.</p>"
        f"<ol>{stages}</ol>"
        + _form(
            view,
            csrf_token,
            "map",
            '<button type="submit">I have traced the map</button>',
        )
        + "</section>"
    )


def _predict(view: LearningView, csrf_token: str) -> str:
    choices = "".join(
        '<div class="choice"><label>'
        f'<input type="radio" name="choice-id" value="{escape(choice.choice_id)}" required> '
        f"{escape(choice.text)}</label></div>"
        for choice in view.lesson.prediction.choices
    )
    confidence = "".join(
        f'<option value="{value}">{value}</option>' for value in range(1, 6)
    )
    return (
        "<section><h2>Predict</h2>"
        f"<p>{escape(view.lesson.prediction.prompt)}</p>"
        + _form(
            view,
            csrf_token,
            "predict",
            f"<fieldset><legend>Your prediction</legend>{choices}</fieldset>"
            '<label for="prediction-confidence">Confidence before the result</label>'
            '<select id="prediction-confidence" name="confidence" required>'
            '<option value="" selected>Choose 1–5</option>'
            f"{confidence}</select>"
            '<button type="submit">Lock prediction</button>',
        )
        + "</section>"
    )


def _try(view: LearningView, csrf_token: str) -> str:
    attempt = view.attempt
    assert attempt is not None
    result = attempt.renderer.result
    return (
        "<section><h2>Try</h2>"
        f"<p>{escape(view.lesson.activity.instructions)}</p>"
        "<dl>"
        f"<dt>Input records</dt><dd>{result.source_rows}</dd>"
        f"<dt>Seed</dt><dd>{result.seed}</dd>"
        f"<dt>Initial state hash</dt><dd><code>{escape(result.state_sha256)}</code></dd>"
        "</dl>"
        + _form(
            view,
            csrf_token,
            "run",
            '<button type="submit">Run the pipeline</button>',
        )
        + "</section>"
    )


def _prove(view: LearningView, csrf_token: str) -> str:
    attempt = view.attempt
    assert attempt is not None
    result = attempt.renderer.result
    cards = "".join(_card(card) for card in view.lesson.evidence.cards)
    return (
        "<section><h2>Prove</h2>"
        "<h3>Observed result</h3><dl>"
        f"<dt>Job completed</dt><dd>{_yes_no(result.job_completed)}</dd>"
        f"<dt>Validation passed</dt><dd>{_yes_no(result.validation_passed)}</dd>"
        f"<dt>Duplicate excess rows</dt><dd>{result.duplicate_excess_rows}</dd>"
        f"<dt>Published total</dt><dd>{result.downstream_cost_cents} cents</dd>"
        f"<dt>Unique total</dt><dd>{result.unique_cost_cents} cents</dd>"
        f"<dt>State hash</dt><dd><code>{escape(result.state_sha256)}</code></dd>"
        "</dl>"
        f"<p><strong>Claim to prove:</strong> {escape(view.lesson.evidence.claim)}</p>"
        + _form(
            view,
            csrf_token,
            "prove",
            f"{cards}<button type=\"submit\">Submit evidence decisions</button>",
        )
        + _form(
            view,
            csrf_token,
            "reset",
            '<button type="submit">Reset this scenario</button>',
        )
        + "</section>"
    )


def _card(card) -> str:
    name = f"evidence-{card.evidence_id}"
    return (
        "<article><h3>"
        f"{escape(card.title)}</h3><dl>"
        f"<dt>Proves</dt><dd>{escape(card.proves)}</dd>"
        f"<dt>Does not prove</dt><dd>{escape(card.does_not_prove)}</dd>"
        f"<dt>Source</dt><dd>{escape(card.source)}</dd>"
        f"<dt>Scope</dt><dd>{escape(card.scope)}</dd>"
        f"<dt>Sensitivity</dt><dd>{escape(card.sensitivity)}</dd>"
        f"<dt>Observed at</dt><dd>{escape(card.observed_at)}</dd>"
        "</dl><fieldset><legend>Use this card?</legend>"
        '<div class="choice"><label>'
        f'<input type="radio" name="{escape(name)}" value="supports" required> '
        "Supports the claim</label></div>"
        '<div class="choice"><label>'
        f'<input type="radio" name="{escape(name)}" value="rejects" required> '
        "Reject as insufficient or misleading</label></div>"
        "</fieldset></article>"
    )


def _explain(view: LearningView, csrf_token: str) -> str:
    choices = "".join(
        '<div class="choice"><label>'
        f'<input type="radio" name="mechanism-choice-id" '
        f'value="{escape(choice.choice_id)}" required> '
        f"{escape(choice.text)}</label></div>"
        for choice in view.lesson.explanation.qualification.choices
    )
    confidence = "".join(
        f'<option value="{value}">{value}</option>' for value in range(1, 6)
    )
    return (
        "<section><h2>Explain</h2>"
        f"<p>{escape(view.lesson.explanation.prompt)}</p>"
        + _form(
            view,
            csrf_token,
            "explain",
            f"<fieldset><legend>Mechanism</legend>{choices}</fieldset>"
            '<label for="explanation">Your explanation</label>'
            '<textarea id="explanation" name="explanation" required></textarea>'
            '<label for="uncertainty">What remains uncertain?</label>'
            '<textarea id="uncertainty" name="uncertainty" required></textarea>'
            '<label for="confidence-after">Confidence after the evidence</label>'
            '<select id="confidence-after" name="confidence-after" required>'
            '<option value="" selected>Choose 1–5</option>'
            f"{confidence}</select>"
            '<button type="submit">Complete attempt</button>',
        )
        + "</section>"
    )


def _complete(view: LearningView, _csrf_token: str) -> str:
    attempt = view.attempt
    evaluation = view.evaluation
    record = view.record
    assert attempt is not None and evaluation is not None and record is not None
    feedback = "".join(
        f"<li>{escape(reason.replace('-', ' '))}</li>"
        for reason in evaluation.feedback
    ) or "<li>No qualification gaps.</li>"
    return (
        "<section><h2>Review</h2>"
        f"<p><strong>Mastery: {escape(view.mastery.state.title())}</strong></p>"
        f"<p>{'This attempt demonstrated the outcome.' if evaluation.qualifies else 'This attempt introduced the outcome; revise the gaps below.'}</p>"
        + _review_status(view, "")
        + (
            f"<ul>{feedback}</ul>"
            "<dl>"
            f"<dt>Learner Attempt ID</dt><dd><code>"
            f"{escape(attempt.attempt_id)}</code></dd>"
            f"<dt>Attempt checkpoint</dt><dd><code>"
            f"{escape(attempt.checkpoint_sha256)}</code></dd>"
            f"<dt>Evaluation</dt><dd><code>"
            f"{escape(evaluation.evaluation_sha256)}</code></dd>"
            f"<dt>Terminal record</dt><dd><code>"
            f"{escape(record.record_sha256)}</code></dd>"
            "</dl></section>"
        )
    )


def _review_status(view: LearningView, csrf_token: str) -> str:
    review = view.review
    if review.status == "not-scheduled":
        return "<p>Review is not scheduled yet.</p>"
    if review.status == "retained":
        return (
            "<p><strong>Retained.</strong> A later qualifying review "
            "proved this outcome again.</p>"
        )
    if review.status == "in-progress":
        return "<p><strong>Review in progress.</strong></p>"
    due = escape(review.due_at or "unknown")
    if review.status == "due":
        action = (
            f"/learn/{escape(view.bundle.pack_id)}/"
            f"{escape(view.lesson.lesson_id)}/review"
        )
        form = (
            f'<form method="post" action="{action}">'
            f'<input type="hidden" name="csrf-token" value="{escape(csrf_token)}">'
            '<button type="submit">Begin due review</button></form>'
            if csrf_token
            else ""
        )
        return f"<p><strong>Review due now.</strong> Due {due}.</p>{form}"
    label = "Retry scheduled" if review.status == "retry-scheduled" else (
        "Review scheduled"
    )
    return f"<p><strong>{label}:</strong> {due}.</p>"


def _history(view: LearningView) -> str:
    matching = tuple(
        entry
        for entry in view.history
        if entry.checkpoint.pack_id == view.bundle.pack_id
        and entry.checkpoint.lesson_id == view.lesson.lesson_id
    )
    if not matching:
        return "<section><h2>Attempt history</h2><p>No attempts yet.</p></section>"
    items = "".join(
        "<li>"
        f'<a href="/attempts/{escape(entry.checkpoint.attempt_id)}">'
        f"{escape(entry.checkpoint.attempt_id)}</a> — "
        f"{escape(entry.attempt_kind)} — {escape(entry.status)}"
        + (
            f" — reset by {escape(entry.reset_by_attempt_id)}"
            if entry.reset_by_attempt_id is not None
            else ""
        )
        + "</li>"
        for entry in matching
    )
    return f"<section><h2>Attempt history</h2><ol>{items}</ol></section>"


def _yes_no(value: bool) -> str:
    return "yes" if value else "no"
