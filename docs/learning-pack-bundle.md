# Learning Pack Bundle contract

A Learning Pack Bundle is the strict, sanitized handoff shared by the Product
Shell and standalone export.

Use it after Promotion has produced an immutable `AcceptedPackSnapshot` and a
public lesson blueprint is available. Do not use it to carry raw intake,
staged proposals, Promotion records, arbitrary attachments, or generic
metadata.

```text
AcceptedPackSnapshot ─┐
                      ├─ validated assembly ─> LearningPackBundle
public lesson blueprint┘
```

Once assembled, a bundle is saved by digest under:

```text
snapshots/learning-packs/<bundle-sha256>.json
```

Lesson preparation is the only bundle write path. Teaching binds a canonical
bundle to the current accepted Pack snapshot. Standalone export adds a separate
explicit approval that binds the complete bundle digest to the exact accepted
snapshot revision. Consumers do not accept an arbitrary external bundle file.

Canonical storage is not approval. Export approval creates a separate immutable
record under `snapshots/export-approvals/`. A later Promotion may make a newer
Pack current, but it cannot change the exact bundle and accepted revision named
by an existing approval.

## Identity

The bundle records the exact accepted pack ID, version, and content digest.
Every Learning Outcome and Lesson has a stable identifier and a content
revision digest. The complete bundle has a canonical `bundle_sha256`.

Changing accepted content, a learning outcome, lesson wording, the fixed
synthetic Scenario input, evidence scope, or an evaluation rule changes the
corresponding digest.

## Allowed content

Version one contains only explicit schemas for:

- accepted sanitized Claims;
- Concepts;
- system Map stages;
- one deterministic Activity specification;
- a prediction question;
- Evidence Cards and an explicit verdict for every card;
- a free-text explanation prompt and deterministic qualification question.

An Activity identifies a fixed public synthetic input by
`input_revision_sha256`. It declares deterministic reset, keyboard operation,
and evidence-producing result capabilities.

Evidence Cards state their source, scope, sensitivity, observation time, what
they prove, and what they do not prove. Only `public-synthetic` and `sanitized`
sensitivity values are publishable.

## Validation

Loading or constructing a bundle fails when:

- any unknown field is present;
- a lesson references a missing Concept or accepted Claim;
- an outcome, lesson, or bundle digest does not match its content;
- evidence rules omit a card or give one card conflicting verdicts;
- an Activity omits a required Renderer Capability;
- text contains a structural private-storage marker.

The bundle remains sanitized input, not permission to serialize every field.
Each consumer still uses an explicit allowlist. Standalone export also requires
an exact `ExportApproval`; a schema-valid canonical bundle without one fails.
