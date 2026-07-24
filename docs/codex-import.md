# Explicitly import Codex context

Use `opslearn codex-import` when you want one explicit piece of Codex context
to enter Capture Mode or Learn Mode.

Do not use it as a background hook or a conversation archive. Version one has
no automatic Codex lifecycle plugin. The command reads one strict JSON object
from standard input, retrieves only the named source boundary, and never writes
to the source task.

```text
explicit request
      |
      v
strict stdin schema ---- invalid or broad request ---> stop, write nothing
      |
      v
exact paste OR selected task turns through a read-only port
      |
      v
private Intake Bundle -> staged Pack Update
                              |
               +--------------+--------------+
               |                             |
          Capture Mode                  Learn Mode
       concise summary only       existing Product Shell route
       no exercise starts         open/resume through one port
```

## Capture exact pasted text

Initialize a learning home outside the repository:

```bash
opslearn init --home /tmp/ops-learning-home
```

Then send one explicit request:

```bash
opslearn codex-import --home /tmp/ops-learning-home <<'JSON'
{
  "schema_version": 1,
  "mode": "capture",
  "source": {
    "kind": "pasted_text",
    "source_id": "synthetic-codex-note",
    "observed_at": "2026-07-24T12:00:00Z",
    "text": "Synthetic Codex ETL usage and cost context.\nClaim: Synthetic normalized cost is non-negative.\n"
  }
}
JSON
```

The returned `intake_id` proves that the exact UTF-8 text and its provenance
were stored in the Private Intake Store. The returned `update_id` and
`review_path` identify the immutable staged Pack Update. Running the same
request again produces the same identifiers and does not duplicate either
artifact.

Capture Mode always returns with `lesson_started: false`. It does not open,
resume, or create a Learner Attempt.

## Enter Learn Mode

Change only the mode:

```json
{
  "schema_version": 1,
  "mode": "learn",
  "source": {
    "kind": "pasted_text",
    "source_id": "synthetic-codex-note",
    "observed_at": "2026-07-24T12:00:00Z",
    "text": "Synthetic Codex ETL usage and cost context."
  }
}
```

The adapter still creates the Intake Bundle and staged Pack Update first. For
one unambiguous accepted pack, it returns a `learning_path` owned by the
existing Product Shell. It does not implement a second lesson engine.

Start the shell separately:

```bash
opslearn serve --home /tmp/ops-learning-home --port 8000
```

Open the returned path under `http://127.0.0.1:8000`. A result with
`learning_disposition: opened` points to the lesson overview and has no Learner
Attempt yet. The resume-capable integration boundary can instead return
`learning_disposition: resumed` and an `attempt_id` after durable attempt
recovery is available.

If the accepted pack has no single supported lesson, the result is
`learning_incomplete`. The Intake Bundle and staged Pack Update remain
available, and the response explains the missing step.

## Resolve an ambiguous Pack Match

An ambiguous request returns `learner_choice_required` and lists
`candidate_pack_ids`. It never chooses silently. Re-run the exact request with
one listed identifier:

```json
{
  "schema_version": 1,
  "mode": "learn",
  "selected_pack_id": "workflow-validation",
  "source": {
    "kind": "pasted_text",
    "source_id": "synthetic-ambiguous-note",
    "observed_at": "2026-07-24T12:00:00Z",
    "text": "Codex ETL cost usage plus workflow validation freshness quality."
  }
}
```

The retry is content-addressed. It reuses the same raw intake when the source,
scope, observation time, and content are identical. It creates a new immutable
staged update whose `selected` Pack Match records the explicit destination,
then routes learning.

## Target selected Codex task turns

The adapter contract supports only an explicit list or range:

```json
{
  "schema_version": 1,
  "mode": "capture",
  "source": {
    "kind": "task_turns",
    "task_id": "synthetic-task-7",
    "turn_ids": ["turn-2", "turn-4"]
  }
}
```

or:

```json
{
  "schema_version": 1,
  "mode": "capture",
  "source": {
    "kind": "task_turn_range",
    "task_id": "synthetic-task-7",
    "start_turn_id": "turn-2",
    "end_turn_id": "turn-4"
  }
}
```

There is deliberately no `all`, omitted selection, or whole-history mode. The
read-only conversation port exposes only `read_selected(task_id, selection)`.
Its result must echo the requested task and scope before any local write occurs.
The private manifest retains the task ID, a canonical JSON representation of
the selected turn IDs or range, observation time, and SHA-256 content identity.
The canonical scope prevents delimiter-shaped turn IDs from colliding.

The stock CLI does not have direct Codex task access yet. It rejects these
requests with `targeted Codex task reads are not configured` and writes
nothing. A host integration must supply the read-only port; it must not work
around this by reading the whole task or by using append, resume, rename,
delete, fork, archive, or message APIs.

## Codex skill wrapper contract

An explicitly invoked Codex skill may prepare and pipe the JSON request. The
skill should:

1. ask whether the Learner wants Capture Mode or Learn Mode;
2. ask for exact pasted text or explicit task turn IDs/range;
3. show the retrieval boundary before reading;
4. invoke `opslearn codex-import` once through standard input;
5. report only the concise command result;
6. never start an exercise in Capture Mode;
7. return `learner_choice_required` unchanged instead of picking a pack;
8. stop on adapter errors and report which local artifact, if any, exists.

The wrapper is an explicit caller, not an automatic hook. It does not own
matching, persistence, Product Shell learning state, or source-task mutation.

## What proves success

Run:

```bash
PYTHONPATH=src python3 -m unittest tests/test_codex_import.py -v
./scripts/verify
```

The focused tests prove exact pasted import, targeted synthetic turn retrieval,
provenance, strict schemas, idempotency, Capture/Learn separation, ambiguous
choice handling, Product Shell routing, unchanged prior state on retrieval
errors, and a task port with no mutation capability.
