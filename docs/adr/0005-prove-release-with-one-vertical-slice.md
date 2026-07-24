# ADR 0005: Prove release with one complete vertical slice

## Status

Accepted

## Context

The product could expand into many packs, adapters, activities, and export
formats before proving that captured context becomes useful, retained learning.

## Decision

Version one ships only after one synthetic Codex ETL Learning Pack works end to
end:

1. Capture private source material with provenance.
2. Propose a Pack Match without silently merging.
3. Stage, review, and promote a sanitized Pack Update.
4. Complete one full Learning Loop in the Product Shell.
5. Persist the Learner Attempt and advance Mastery State only when earned.
6. Restore an interrupted attempt.
7. Complete a later review.
8. Export a standalone Publishable Artifact.
9. Prove that a private canary is absent from the export.

The Scenario is local and synthetic. It does not access or mutate external
systems.

## Consequences

- Implementation favors one complete journey over a broad feature catalogue.
- Privacy, provenance, recovery, evidence, accessibility, and retention are
  release gates.
- Additional packs and Activity Renderers wait until the slice passes.
