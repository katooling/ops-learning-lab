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
exact paste OR bounded task extract OR selected turns through a read-only port
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
Attempt yet. When durable history contains exactly one active learning attempt
for that pack and lesson, the command instead returns
`learning_disposition: resumed`, its `attempt_id`, and
`/attempts/<attempt_id>`. Completed, reset, review, other-pack, and other-lesson
attempts are not resume candidates.

More than one matching active attempt is treated as inconsistent state. The
command returns `learning_incomplete` instead of choosing one silently. Its
Intake Bundle and staged Pack Update remain available.

Learn Mode opens a descriptor-bound, read-only history projection while
choosing the route, then closes it before returning. It can run while
`opslearn serve` owns the exclusive writer lock. The reader has no append,
complete, restart, or repair methods: the Product Shell remains the only
writer. It snapshots immutable event names, so an atomic append is either
included completely or left for the next read. Capture Mode does not inspect
learner history and may also run while the shell is open.

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

The stock CLI accepts a caller-supplied extract only when it includes an
explicit list or range. This is the path for a
`codex-conversation-query`-style skill: the skill reads only the agreed turns,
then sends their exact text and provenance to the learning adapter.

```json
{
  "schema_version": 1,
  "mode": "capture",
  "source": {
    "kind": "task_turns_extract",
    "task_id": "synthetic-task-7",
    "turn_ids": ["turn-2", "turn-4"],
    "observed_at": "2026-07-24T12:30:00Z",
    "text": "Exact text from only turn 2 and turn 4."
  }
}
```

The range form is equally strict:

```json
{
  "schema_version": 1,
  "mode": "learn",
  "source": {
    "kind": "task_turn_range_extract",
    "task_id": "synthetic-task-7",
    "start_turn_id": "turn-2",
    "end_turn_id": "turn-4",
    "observed_at": "2026-07-24T12:30:00Z",
    "text": "Exact text from the inclusive turn range."
  }
}
```

There is deliberately no `all`, omitted selection, mixed list-and-range, or
whole-history form. The private manifest retains the task ID, a canonical JSON
representation of the selected turn IDs or range, observation time, and
SHA-256 content identity. The canonical scope prevents delimiter-shaped turn
IDs from colliding. Repeating the exact extract is idempotent.

The inline `text` is caller-attested. The CLI proves its exact bytes and
declared selection after receipt, but it cannot prove that a caller extracted
only those turns. The explicitly invoked conversation-query skill owns that
retrieval boundary and must show it before reading. Use the host read-only port
when the host needs to enforce the selection itself.

A host integration may instead send `task_turns` or `task_turn_range` without
inline text and supply the read-only conversation port. That port exposes only
`read_selected(task_id, selection)`, and its result must echo the exact task
and scope before any local write occurs. The stock CLI deliberately rejects
these port-backed forms because it has no direct Codex task authority.

## Codex skill wrapper contract

An explicitly invoked Codex skill may prepare and pipe the JSON request. The
skill should:

1. ask whether the Learner wants Capture Mode or Learn Mode;
2. ask for exact pasted text or explicit task turn IDs/range;
3. show the retrieval boundary before reading;
4. retrieve only that boundary and invoke `opslearn codex-import` with the
   bounded inline extract through standard input;
5. report only the concise command result;
6. never start an exercise in Capture Mode;
7. return `learner_choice_required` unchanged instead of picking a pack;
8. stop on adapter errors and report which local artifact, if any, exists.

The wrapper is an explicit caller, not an automatic hook. It does not own
matching, persistence, Product Shell learning state, or source-task mutation.
For Learn Mode, `/learn/...` is a ready-to-start overview and `/attempts/...`
is a durable resume route. The wrapper never presses Start on the Learner's
behalf.

## What proves success

Run:

```bash
PYTHONPATH=src python3 -m unittest tests/test_codex_import.py -v
./scripts/verify
```

The focused tests prove exact pasted import, real CLI import of bounded task
extracts, targeted synthetic turn retrieval, provenance, strict schemas,
idempotency, Capture/Learn separation, ambiguous choice handling, durable
Product Shell open/resume routing, fail-closed multiple-attempt handling, CLI
lock release, unchanged prior state on retrieval errors, and a task port with
no mutation capability.
