# Export a standalone Learning Pack

Use standalone export when an accepted Learning Pack and its sanitized lesson
bundle should become one portable HTML file.

Do not use export to sanitize raw or staged material. Promotion and bundle
assembly must already have completed.

```text
canonical stored bundle digest -> current accepted Pack check
                               -> explicit field allowlist
                               -> logical canary scan
                               -> escaped semantic HTML
                               -> rendered canary scan
                               -> directory-fd-bound atomic write
                               -> privacy receipt
```

## Export

The command accepts only the digest of a canonical bundle already stored under:

```text
snapshots/learning-packs/<bundle-sha256>.json
```

It does not accept an arbitrary bundle pathname. The canonical bundle must
match the current accepted Pack ID, title, version, digest, and Claims.

```bash
opslearn export \
  --home /tmp/ops-learning-home \
  --bundle-sha256 <canonical-bundle-sha256> \
  --canary-file /tmp/private-canary.txt
```

`--canary-file` may be repeated. Each file supplies exact bytes that must be
absent. The CLI verifies the initialized Learning Home and canonical bundle.
The `StandaloneExporter` itself receives only that validated bundle, the
configured canaries, and a capability bound to the exports directory. It
cannot discover or read Private Intake or staged updates.

The result is one file under `exports/`:

```text
<pack-id>--export-<digest-prefix>.html
```

It has inline styling and no external assets, scripts, local paths, or network
dependencies. A visible identity section records the exact bundle, accepted
Pack snapshot, Lesson, Learning Outcome, and Activity input revisions. Copying
that file outside the learning home does not change its content or usefulness.

## Fail-closed behavior

Export stops before the final write when:

- the accepted pack and bundle identities differ;
- canonical bundle JSON is missing, malformed, or stored under the wrong
  digest;
- an unknown or unapproved bundle field is present;
- a configured canary appears in logical content, escaped content, or rendered
  bytes;
- the artifact exceeds its size limit;
- the Learning Home, export directory, or target is replaced, a symbolic link,
  or a non-regular file;
- an identifier could escape the export directory.

The filename derives from the rendered bytes. Repeating identical export
content returns the same artifact. An ordinary interrupted write leaves no
partial target and does not change an earlier export. Writes and verification
stay relative to one approved directory descriptor; a pathname swap cannot
redirect the commit.

## Proof artifact

The command prints a JSON `ExportReceipt`. Success requires:

```text
privacy_status = passed
files_scanned = 1
artifact_sha256 = the actual HTML digest
```

The receipt records only canary digests, never their bytes.

This check proves the exact configured canaries are absent. It is not a
universal secret or personal-data detector; explicit review and the typed
allowlist remain required.

Run the complete verification:

```bash
./scripts/verify
```

Run the real-browser proof after installing its test-only dependencies:

```bash
npm --prefix tests/browser ci
(cd tests/browser && npx playwright install chromium)
./scripts/verify-browser
```

The highest-seam Python proof captures a private canary, promotes independently
sanitized content, stores a canonical bundle, exports through the CLI, and
audits every generated public file. The Playwright proof opens the artifact
through `file://`, checks its headings and keyboard skip link, and observes zero
HTTP requests.
