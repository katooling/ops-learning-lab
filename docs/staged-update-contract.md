# Staged Pack Update contract

This contract is the handoff from Capture Mode to review and Promotion.

## Storage and immutability

Each proposal is one validated JSON file:

```text
<learning-home>/staged/updates/<update_id>.json
```

The `staged/` tree is local and private. Its directories use mode `0700`; files
use mode `0600`. Writers use atomic replacement. Repeating an identical capture
returns the existing object.

A Staged Pack Update is immutable. Review must record a separate decision; it
must never mutate the proposal. The artifact has no mutable review-status field.
Its location and type identify it as staged.

## Identity

`proposal_sha256` is the SHA-256 digest of canonical JSON containing the
compiler version, intake surrogate and digest, safe source provenance, proposed
match, proposed claims, and redaction categories.

`update_id` is the first 20 hexadecimal characters of that digest prefixed with
`update-`. Loading a proposal recalculates the digest and rejects tampering.

Each proposed claim has a `proposal_id`, not an accepted Claim identity. Capture
Mode currently proposes additions only. Promotion owns stable accepted Claim
identifiers and any later replace, supersede, or contradict relationship.

## Pack match and stale-review protection

Every match candidate carries:

- proposed pack identifier and title from the destination profile;
- exact shared match terms;
- a digest of the matching profile;
- an optional expected base version and content digest.

The matching profile identifies only the terms used to propose a destination.
It is not canonical Learning Pack state. Version one does not yet persist an
accepted pack artifact, so both expected-base fields are `null`. Promotion must
resolve or create the real target pack and bind its own decision to that
canonical state. Once a persisted pack exists, both expected-base fields must
be present together. An ambiguous match has no selected target. When the
Learner explicitly chooses one ambiguous candidate, a new immutable proposal
records a `selected` match with exactly that candidate and proposed target.

## Strict JSON

Persisted manifests and staged updates use one strict JSON decoder. It rejects
duplicate object keys, non-finite numbers such as `NaN` or `Infinity`, invalid
UTF-8, and non-object roots before domain validation. Malformed persisted
updates produce one generic schema error; raw parser exceptions are not exposed
through the Product Shell.

## Privacy boundary

The staged source view contains source type and observation time. It deliberately
omits the private source identifier and raw filesystem path. The intake ID is a
safe, content-addressed surrogate.

Only explicitly marked `Claim:` lines become proposed text. Unmarked raw lines
are not copied. Proposed claim text remains private staged material; it is not
automatically safe to publish. Promotion must sanitize and explicitly accept
the exact text that enters a Learning Pack.

The Product Shell receives only the validated staged object. It has no
capability or route for the Private Intake Store.
