# Version 0.1.0 limitations

Version 0.1.0 is one personal, local learning lab. It proves the architecture
with one synthetic Codex ETL lesson; it is not yet a general course platform.

## Product limits

- One local learner; no accounts, sync, collaboration, or hosted service.
- One built-in Activity Renderer and one synthetic Learning Pack.
- No automatic Codex hook. `codex-import` accepts explicit pasted text or an
  exact caller-supplied turn selection.
- The stock CLI cannot read Codex tasks itself. A separately invoked read-only
  host adapter must enforce task selection.
- Ambiguous Pack Matches require learner choice.
- Promotion, export approval, and sharing are manual decisions.
- No external-system mutation or message sending.

## Safety limits

- Canary checks prove only that exact configured bytes are absent.
- Pattern scans catch common secret shapes and absolute home paths, not every
  possible sensitive fact.
- Sanitized content still needs human review.
- Local Learning Home owners control the underlying files.
- The learner event chain has no independent durable head; deletion of the
  newest event suffix is not detectable.
- Directory `fsync` support varies by filesystem. The store verifies visible
  bytes but cannot promise survival after sudden power loss on every host.

## Learning limits

- Retained mastery means one qualifying later attempt under the current
  deterministic rules. It is not a broad measure of expertise.
- The seven-day review and one-day retry schedule are fixed in version 0.1.0.
- Accessibility proof covers keyboard use and a 320-pixel Chromium journey.
  It is not a full assistive-technology certification.

## Publication limits

- A standalone export is one static offline HTML artifact.
- Export approval applies to one exact canonical bundle and accepted revision.
- Complete-history auditing covers `HEAD` and release tags available in a
  non-shallow checkout. Hosting-provider caches and unreachable objects are
  outside that proof.
