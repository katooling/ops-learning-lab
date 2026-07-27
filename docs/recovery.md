# Recover safely

Use this guide after interruption, a failed command, or a learner-history
error.

## Normal interruption

Restart the Product Shell with the same Learning Home:

```bash
opslearn serve --home /tmp/ops-learning-home --port 8000
```

Open Attempt history. An active attempt resumes from its last immutable
checkpoint. A repeated command ID with identical content is idempotent.

## Restart intentionally

- **Reset this scenario** keeps the same attempt ID and deterministic seed.
- **Restart this whole attempt** marks the old attempt reset and creates a new
  traceable attempt.

Use the first to retry the activity. Use the second to discard the whole
prediction-and-evidence path. Neither action edits earlier events.

## A command failed

1. Read the exact error.
2. Do not delete temporary, event, approval, or snapshot files manually.
3. Retry the exact command only when its documented operation is idempotent.
4. Confirm the prior accepted pack or export is unchanged.
5. Preserve the Learning Home before diagnosis when history reports corruption.

Promotion and export bind immutable inputs. A changed input requires a new
preview or approval; do not reuse an old digest.

## Learner history needs repair

Stop writing. Preserve:

```text
private/learner-state/events/
```

The hash chain detects edited events, reordering, sequence gaps, and deletion
inside the observed chain. Version 0.1.0 has no independent durable head, so a
local Learning Home owner can remove the newest suffix without detection.
Keep an external backup when that threat matters.

Do not rename, delete, or rewrite events as an automatic repair. The detailed
[resume and review guide](resume-and-review.md) explains the event and mastery
model.

## Source import failed

A failed Codex retrieval or invalid strict JSON request creates no partial
source mutation. The response states whether private intake or staging was
already created. Keep those artifacts and retry with an exact supported
selection. Never broaden the request to whole history as a recovery shortcut.

## Release verification failed

Do not tag. Follow the failed gate in order:

1. `verify-fast` — code, contracts, links, current publication, clean install;
2. history audit — complete reachable Git history;
3. browser proof — full local product journey;
4. final publication audit — no unsafe files created during proof.

The [release guide](releasing.md) gives exact commands and cleanup.
