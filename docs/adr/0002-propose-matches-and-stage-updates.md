# ADR 0002: Propose pack matches and stage updates

## Status

Accepted

## Context

New material may fit one existing Learning Pack, several packs, or a new topic.
Silently choosing a destination can mix unrelated concepts. Applying generated
changes immediately can also replace accepted knowledge with unsupported or
private claims.

## Decision

The Pack Compiler proposes a Pack Match with reasons.

- An explicitly named pack receives a proposed update.
- One strong inferred match is shown before application.
- Several plausible matches require Learner selection.
- No strong match produces a proposed new pack.

Capture Mode saves intake immediately but creates a staged Pack Update. Changes
to accepted facts, contradictions, ambiguous matches, and sensitive promotions
always require review.

Intake Adapters are read-only and retrieve only the material needed for the
request.

## Consequences

- Ambiguity becomes visible instead of corrupting a pack.
- Accepted learning material remains stable during fast capture.
- The product needs a review queue and clear staged-versus-accepted states.
- Source provenance remains attached throughout matching and review.
