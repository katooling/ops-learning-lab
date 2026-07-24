# Ops Learning Lab domain language

Use these terms consistently in code, tests, issues, and documentation.

## Learning Lab

A local environment that turns work context into reviewed learning material,
safe practice, and evidence-backed progress. It observes and simulates; it does
not operate external systems.

## Learner

The person developing durable judgment through prediction, practice, evidence,
explanation, and later review. Version one supports one local Learner.

## Learning Pack

A reusable, versioned teaching unit for one technical journey. A Learning Pack
contains reviewed concepts, dated facts, safe Scenarios, synthetic fixtures,
Evidence Cards, lessons, references, and completion criteria.

_Avoid:_ course dump, transcript archive.

## Intake Bundle

A provenance-preserving collection of source material submitted for learning.
Each item records its source type, source identifier, capture time, and content
hash. Intake is material to evaluate, not truth to copy.

## Intake Adapter

A read-only boundary that converts one source type into Intake Bundle items.
Adapters retrieve only the requested material, state their source boundary, and
never mutate the source.

## Private Intake Store

The local area holding raw Intake Bundles. It stays outside the public
repository and is never read by exporters or Activity Renderers.

## Pack Compiler

The process that extracts candidate concepts, claims, hazards, evidence, and
learning opportunities from an Intake Bundle. It proposes a Pack Match and a
staged Pack Update. Deterministic validation owns structure, identifiers,
provenance requirements, and contradiction checks.

## Pack Match

A proposed relationship between an Intake Bundle and a Learning Pack. One
strong match may be proposed with reasons. Several plausible matches require
Learner selection. No strong match produces a proposed new pack. Ambiguous
material is never silently merged.

## Pack Update

A versioned proposal to add, correct, or supersede Learning Pack content. A Pack
Update remains staged until reviewed. It shows sources, changed claims,
historical or contradicted knowledge, and affected learning material.

## Promotion

The explicit review step that turns selected private material into sanitized,
accepted Learning Pack content. Promotion removes unnecessary private details
while retaining safe provenance.

## Publishable Artifact

A reviewed export produced only from promoted Learning Pack content and
sanitized snapshots. Raw intake is never a Publishable Artifact.

## Capture Mode

The lightweight path used during ongoing work. It saves an Intake Bundle,
proposes a Pack Match, stages a Pack Update, reports a concise summary, and
returns without starting a lesson.

## Learn Mode

The deliberate path that ingests relevant context and routes the Learner into
the appropriate Product Shell lesson. It resumes exactly one active matching
attempt. When none exists, it opens the ready-to-start lesson overview; the
Learner owns the explicit Start action, so the import never creates an attempt
silently.

## Stable Concept

An idea intended to remain useful when a current implementation changes.

## Operational Fact

A dated claim about a system. Every Operational Fact has provenance,
observation time, and one status: `current`, `historical`, `contradicted`, or
`unverified`.

## Historical Snapshot

An immutable, sanitized view of facts and evidence observed at a specific time.
Historical Snapshots can support Scenarios after they stop describing the
current system.

## Scenario

A resettable local exercise with deterministic consequences. A Scenario uses
synthetic or sanitized data and performs no external writes.

## Evidence Card

A learner-facing description of an artifact. It states what the artifact
proves, what it does not prove, its source, scope, sensitivity, and observation
time.

## Claim

A statement linked to Evidence Cards sufficient for its exact scope. An
operation completing successfully does not by itself prove a neighboring
Claim.

## Learner Attempt

A record of a prediction, decision, confidence, activity result, evidence
selection, explanation, feedback, hints, and review schedule.

## Mastery State

The Learner's demonstrated relationship with a concept:

1. **Captured** — relevant material exists.
2. **Introduced** — the Learner explored an explanation.
3. **Demonstrated** — a Learner Attempt proves correct application or reasoning.
4. **Retained** — a later Learner Attempt proves the learning persisted.

Reading or generating content cannot produce `Demonstrated` or `Retained`.

## Learning Loop

The common lesson sequence:

1. **Map** the system and its boundaries.
2. **Predict** an outcome before seeing it.
3. **Try** a safe Scenario.
4. **Prove** the exact Claim with suitable Evidence Cards.
5. **Explain** the outcome and uncertainty.
6. **Review** the idea later.

## Product Shell

The purpose-built local browser experience. It owns navigation, Learning Loop
state, canonical Learner Attempts, evidence evaluation, Mastery State, review
scheduling, persistence, accessibility framing, and export.

## Activity Renderer

A bounded component that presents one safe activity from immutable sanitized
inputs and returns observations, checkpoints, results, and a state hash. It
cannot read private intake, change mastery, judge evidence sufficiency, navigate
elsewhere, or write canonical progress.

## Renderer Capability

A versioned property required by an activity, such as deterministic reset,
keyboard operation, or an evidence-producing result. Learning Packs declare
capabilities and outcomes rather than presentation technologies.
