# Agent guidance

Read `CONTEXT.md` before naming domain concepts, and read the relevant records
under `docs/adr/` before changing architecture.

Keep this repository publishable. Use synthetic fixtures only. Never commit raw
conversations, private attachments, screenshots, personal identifiers,
credentials, local learning homes, generated exports, caches, or logs.

Define the narrowest externally visible behavior that proves a change before
editing code. Run `./scripts/verify-fast` before committing. A passing unit
test is not enough when the change crosses capture, persistence, browser, or
export boundaries; add proof at the highest practical seam. A release
candidate must pass the complete `./scripts/verify` gate.

The application is a local observer and simulator. Do not add external writes,
message sending, production access, or silent promotion of private intake.

## Agent skills

### Issue tracker

Work is tracked in GitHub Issues for `katooling/ops-learning-lab`; external pull
requests are not a triage request surface. See `docs/agents/issue-tracker.md`.

### Triage labels

Use the canonical `needs-triage`, `needs-info`, `ready-for-agent`,
`ready-for-human`, and `wontfix` labels. See
`docs/agents/triage-labels.md`.

### Domain docs

Ops Learning Lab uses a single domain context with root `CONTEXT.md` and
`docs/adr/`. See `docs/agents/domain.md`.
