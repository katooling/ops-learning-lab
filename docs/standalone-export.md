# Export a standalone Learning Pack

Use standalone export when an accepted Learning Pack and its sanitized lesson
bundle should become one portable HTML file.

Do not use export to sanitize raw or staged material. Promotion and bundle
assembly must already have completed.

```text
validated bundle -> explicit field allowlist -> logical canary scan
                 -> escaped semantic HTML -> rendered canary scan
                 -> atomic export write -> privacy receipt
```

## Export

The current command accepts one strict bundle JSON file. The bundle must match
the current accepted pack ID, title, version, digest, and Claims.

```bash
opslearn export \
  --home /tmp/ops-learning-home \
  --pack-id synthetic-etl \
  --bundle /tmp/synthetic-etl-bundle.json \
  --canary-file /tmp/private-canary.txt
```

`--canary-file` may be repeated. Each file supplies exact bytes that must be
absent. The exporter receives those bytes explicitly; it has no capability to
discover or read the Private Intake Store or staged updates.

The result is one file under `exports/`:

```text
<pack-id>--export-<digest-prefix>.html
```

It has inline styling and no external assets, scripts, local paths, or network
dependencies. Copying that file outside the learning home does not change its
content or usefulness.

## Fail-closed behavior

Export stops before the final write when:

- the accepted pack and bundle identities differ;
- strict bundle JSON is malformed;
- an unknown or unapproved bundle field is present;
- a configured canary appears in logical content, escaped content, or rendered
  bytes;
- the artifact exceeds its size limit;
- the export directory or target is a symbolic link or non-regular file;
- an identifier could escape the export directory.

The filename derives from the rendered bytes. Repeating identical export
content returns the same artifact. An ordinary interrupted write leaves no
partial target and does not change an earlier export.

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

`StandaloneExporterTests.test_cli_exports_without_private_or_staged_capabilities`
is the highest-seam export proof.

