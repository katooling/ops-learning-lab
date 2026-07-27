# Export a standalone Learning Pack

Use standalone export when an accepted Learning Pack and its sanitized lesson
bundle should become one portable HTML file.

Do not use export to sanitize raw or staged material. Promotion and bundle
assembly must already have completed.

```text
canonical stored bundle digest -> explicit immutable approval
                               -> exact accepted snapshot binding
                               -> explicit field allowlist
                               -> public fixture + deterministic result
                               -> logical and rendered canary scans
                               -> home-fd-bound atomic write
                               -> privacy receipt
```

## Approve the exact snapshot

Saving a bundle does not approve it. First review the complete canonical bundle
stored under:

```text
snapshots/learning-packs/<bundle-sha256>.json
```

Then create one explicit immutable approval:

```bash
opslearn export-approve \
  --home /tmp/ops-learning-home \
  --bundle-sha256 <reviewed-bundle-sha256>
```

At this point only, the command checks the bundle against the current accepted
Pack ID, title, version, digest, and Claims. It stores a strict record under:

```text
snapshots/export-approvals/<bundle-sha256>.json
```

The record binds the digest of the complete bundle to the exact accepted Pack
revision. Repeating the same approval is idempotent. A canonical save alone
never creates approval authority.

## Export

```bash
opslearn export \
  --home /tmp/ops-learning-home \
  --bundle-sha256 <canonical-bundle-sha256> \
  --canary-file /tmp/private-canary.txt
```

The export command accepts only the approved bundle digest, not an arbitrary
bundle pathname. It does not compare the bundle to whatever Pack version is
current today. A later Promotion creates a new current Pack revision but does
not invalidate, alter, or race an already approved historical export source.
New content needs a new bundle and a new approval.

`--canary-file` may be repeated. Each file supplies exact bytes that must be
absent. The CLI verifies the initialized Learning Home and canonical bundle.
The `StandaloneExporter` itself receives only that validated bundle, its exact
approval, the configured canaries, and a capability bound to the exports
directory. It cannot discover or read Private Intake or staged updates.

The result is one file under `exports/`:

```text
<pack-id>--export-<digest-prefix>.html
```

It has inline styling and no external assets, scripts, local paths, or network
dependencies. A visible identity section records the exact bundle, accepted
Pack snapshot, export approval, Lesson, Learning Outcome, and Activity input
revisions. Copying that file outside the learning home does not change its
content or usefulness.
The first lesson also includes all three public synthetic input records and the
deterministic validation result, so the evidence remains inspectable offline.

## Fail-closed behavior

Export stops before the final write when:

- the accepted pack and bundle identities differ;
- the complete bundle has no exact immutable export approval;
- the approval file is missing, changed, or bound to another revision;
- canonical bundle JSON is missing, malformed, or stored under the wrong
  digest;
- an unknown or unapproved bundle field is present;
- a configured canary appears in logical content, escaped content, or rendered
  bytes;
- the artifact exceeds its size limit;
- the Learning Home, any publishable child directory, or the target is
  replaced, a symbolic link, or a non-regular file;
- an identifier could escape the export directory.

The filename derives from the rendered bytes. Repeating identical export
content returns the same artifact. An ordinary interrupted write leaves no
partial target and does not change an earlier export. Writes and verification
stay below one retained Learning Home descriptor. Bundle, approval,
accepted-Pack, and export stores are opened relative to it. Replacing the home
or any visible ancestor after it opens makes the operation fail; it cannot
redirect the commit.

## Proof artifact

The command prints a JSON `ExportReceipt`. Success requires:

```text
privacy_status = passed
files_scanned = 1
artifact_sha256 = the actual HTML digest
approval_sha256 = the exact immutable approval digest
```

The receipt records only canary digests, never their bytes.

This check proves the exact configured canaries are absent. It is not a
universal secret or personal-data detector; explicit review and the typed
allowlist remain required.

Run the fast development proof:

```bash
./scripts/verify-fast
```

Before release, install the test-only browser dependencies and run the single
release gate:

```bash
npm --prefix tests/browser ci
(cd tests/browser && npx playwright install chromium)
./scripts/verify
```

The highest-seam Python proof captures a private canary, promotes independently
sanitized content, stores a canonical bundle, explicitly approves it, exports
through the CLI, and audits every generated public file. A separate real CLI
regression replaces the Learning Home after its descriptor opens and proves
neither tree receives an export. The Playwright proof opens the artifact
through `file://` at 320 pixels, checks the public records, deterministic
result, keyboard skip link, and document width, and observes zero HTTP
requests.
