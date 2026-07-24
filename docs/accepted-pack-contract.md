# Accepted Learning Pack contract

The canonical accepted artifact is one strict JSON file:

```text
<learning-home>/packs/<pack-id>/pack.json
```

## Identity and integrity

Every pack has:

- a stable lowercase `pack_id`;
- a positive version;
- a canonical `content_sha256`;
- stable accepted claim IDs;
- one complete Promotion record per version.

The digest covers the accepted claims and Promotion history. Loading a pack
recalculates the digest and rejects unknown fields, malformed values, duplicate
JSON keys, non-finite numbers, symbolic links, and non-regular files.

## Content and decision history

Accepted claims contain sanitized text, fact status, an explicit history
relationship, an optional earlier claim target, and narrow provenance.

Promotion records contain the immutable update identity, expected base
identity, application time, and every accepted or rejected learner decision.
Rejected source text stays in private staging and is not copied to the pack.

The single file is intentional in version one: accepted state and the decision
record cannot be partially committed.

## Read-only consumer seam

Consumers that teach or export call:

```python
snapshot = pack_repository.snapshot(pack_id)
```

`AcceptedPackSnapshot` contains only pack identity, version, canonical digest,
and accepted claims. It does not expose Promotion methods, decision history,
staged proposals, or raw intake.

Exports must still use an explicit field allowlist. A snapshot is trusted
accepted input, not permission to serialize implementation objects.

Future sanitized lessons, scenarios, and evidence can be added through an
explicit schema version and snapshot field. They must not be smuggled through
unknown fields or a generic metadata dictionary.
