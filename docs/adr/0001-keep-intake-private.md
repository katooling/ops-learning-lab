# ADR 0001: Keep intake private and promote content explicitly

## Status

Accepted

## Context

Raw conversations, files, screenshots, and research may contain private details
that help personal learning but do not belong in a public repository or export.
Treating capture as publication would make disclosure an ordinary side effect.

## Decision

Raw Intake Bundles live in a local Private Intake Store outside the public
repository.

Only Promotion can turn selected material into sanitized Learning Pack content.
Promotion preserves safe provenance while removing unnecessary private details.
Exporters read promoted content and sanitized Historical Snapshots; they never
read the Private Intake Store.

## Consequences

- Capture can remain quick without making raw material public.
- Publication requires an explicit review.
- Private storage, backup, and retention remain separate from repository
  history.
- Export verification must include a private-content canary.
