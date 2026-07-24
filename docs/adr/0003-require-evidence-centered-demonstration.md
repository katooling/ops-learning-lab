# ADR 0003: Require evidence-centered demonstration for mastery

## Status

Accepted

## Context

Importing material, generating explanations, and reading lessons measure
exposure. They do not prove that the Learner can predict, apply, or retain an
idea.

## Decision

Mastery progresses through:

```text
Captured -> Introduced -> Demonstrated -> Retained
```

`Demonstrated` requires a successful Learner Attempt. `Retained` requires a
later successful attempt.

Every interactive lesson follows:

```text
Map -> Predict -> Try -> Prove -> Explain -> Review
```

The Product Shell evaluates whether selected Evidence Cards support the exact
Claim. An Activity Renderer reports observations and results but cannot award
mastery.

## Consequences

- Progress represents demonstrated ability rather than content volume.
- Lessons need explicit, machine-checkable success criteria.
- Attempts and later reviews become canonical product records.
- Small lessons may combine steps, but they cannot remove prediction,
  evidence, or explanation.
