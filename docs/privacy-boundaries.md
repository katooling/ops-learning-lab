# Privacy boundaries

Use this map when deciding where an artifact belongs or whether it may be
shared.

```text
SOURCE
  | exact requested bytes through a read-only adapter
  v
private/inbox/                  raw bytes + provenance; never publish
  |
  v
staged/updates/                 generated proposal; still private
  |
  | explicit review + Promotion of sanitized fields only
  v
packs/                          accepted reviewed content
  |
  +--> snapshots/learning-packs/       immutable lesson bundle
  |          |
  |          | exact export approval
  |          v
  |     snapshots/export-approvals/    immutable permission record
  |          |
  |          v
  |     exports/                       standalone public artifact
  |
  +--> private/learner-state/events/   progress history; never export
```

## Zone responsibilities

| Zone | May contain | May not become public automatically |
|---|---|---|
| `private/inbox/` | Exact selected source bytes and provenance | Everything |
| `staged/updates/` | Proposed matches and claims | Every proposal until reviewed |
| `private/learner-state/` | Predictions, explanations, confidence, progress | All learner history |
| `packs/` | Explicitly promoted sanitized content | Unknown or raw fields |
| `snapshots/learning-packs/` | Strict canonical lesson bundles | Raw intake and staging records |
| `snapshots/export-approvals/` | Approval for one exact bundle/revision | General permission for future content |
| `exports/` | Allowlisted offline HTML | Private source, learner history, arbitrary attachments |

The Product Shell can show staged proposals and accepted learning through
separate routes. An Activity Renderer receives only immutable sanitized
scenario input. The exporter opens only publishable stores; it has no private
intake capability.

## Executable checks

`opslearn audit-privacy` checks that exact canary bytes do not occur in
publishable Learning Home zones:

```bash
opslearn audit-privacy \
  --home /tmp/ops-learning-home \
  --canary-file /tmp/private-canary.txt
```

`scripts/audit_publication.py` scans the current repository candidate files.
`scripts/audit_history.py` separately scans release-reachable commits, tags,
paths, modes, and historical blobs. The history scan catches a secret-shaped
value even when a later commit deleted it.

These checks are canaries and pattern-based gates, not universal data-loss
prevention. Human review and typed allowlists remain required.

## Stop conditions

Stop before Promotion or export when:

- you cannot explain why a claim is safe and necessary;
- provenance identifies a private person or system unnecessarily;
- a source contains credentials, customer data, or internal-only material;
- a canary or publication audit fails;
- a filesystem boundary reports a symlink, replacement, permission, or
  ownership error.

Do not “fix” a boundary error by copying raw material into a publishable zone.
