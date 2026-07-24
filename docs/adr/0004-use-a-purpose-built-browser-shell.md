# ADR 0004: Use a purpose-built local browser shell

## Status

Accepted

## Context

The product needs one continuous journey across capture review, lessons,
evidence, progress, interruption recovery, and later review. A presentation
framework should not define pack meaning or canonical learner state.

## Decision

Version one uses:

- a deterministic standard-library Python core;
- a small command-line interface;
- a local Product Shell built with semantic HTML, CSS, and minimal JavaScript;
- Markdown and JSON for reviewed content and canonical state;
- one built-in semantic Activity Renderer.

The Product Shell owns navigation, the Learning Loop, Learner Attempts, evidence
evaluation, mastery, review scheduling, persistence, accessibility framing, and
export.

An Activity Renderer receives immutable sanitized inputs and returns structured
observations, checkpoints, results, and a state hash. Learning Packs declare
Renderer Capabilities instead of naming presentation technologies.

## Consequences

- The runtime needs only Python and a browser.
- Learning Pack meaning remains independent of interface implementation.
- The project must build and test a small set of accessible learning
  primitives.
- Additional renderers remain outside version one until a real learning need
  justifies them.
