# Verify and release

Use this guide only for a release candidate. Ordinary development should run
`./scripts/verify-fast`.

Do not tag or publish a release when any gate below fails.

## The one release gate

Install the test-only browser dependencies:

```bash
npm --prefix tests/browser ci
(cd tests/browser && npx playwright install chromium)
```

Then run:

```bash
./scripts/verify
```

The command runs these gates in order:

```text
verify-fast
  |- unit and contract tests
  |- Python syntax compilation
  |- Markdown links
  |- current-tree publication audit
  `- clean editable-install smoke test
        |
        v
complete-history audit
  |- author, committer, and annotated-tag identities
  |- commit and tag messages
  `- every release-reachable historical blob
        |
        v
real Chromium journeys
  |- full Product Shell paths
  |- keyboard-only operation
  `- 320-pixel viewport
        |
        v
final current-tree publication audit
```

`verify` checks for both the Playwright package and the Chromium binary before
starting. Its error prints the exact missing installation command.

CI checks out complete Git history. A shallow checkout fails closed because it
cannot prove deleted historical content is safe.

## If the history gate fails

Treat a history-audit failure as a release blocker. Do not weaken the allowlist
to make a private identity, secret, internal path, or unsafe historical blob
pass.

1. Stop release work and identify every public ref that reaches the unsafe
   object.
2. Make and verify a private Git bundle or mirror before changing history.
3. Repair the narrow cause in a separate clone. Rewriting history changes every
   descendant object ID, so do not reuse branches based on the old history.
4. Prove that the rewritten tree is unchanged when the repair is metadata-only.
5. Run `./scripts/verify` in the rewritten clone.
6. Update the remote only with an exact force-with-lease against the public
   commit you inspected.
7. Clone the public remote into a new directory and run `./scripts/verify`
   again.
8. Remove obsolete local branches and temporary rewrite files. Retain the
   private backup according to your backup policy.

If other people or public branches depend on the old history, coordinate the
rewrite before updating the remote. Hosting-provider caches and unreachable
objects are outside the local history audit; contact the provider when strict
server-side removal is required.

## Tag version 0.1.0

Use an annotated tag so the history audit can verify a tagger identity:

```bash
git config user.name "Mohamad Kamar"
git config user.email \
  "72454341+Mohamad-Kamar@users.noreply.github.com"
git tag -a v0.1.0 -m "Ops Learning Lab 0.1.0"
./scripts/verify
git push origin v0.1.0
```

Lightweight tags fail the release audit because they have no tagger identity.

## Evidence to retain

- exact release commit and annotated tag;
- successful `./scripts/verify` output from the fresh public clone;
- successful hosted CI run for the rewritten `main`;
- clean `git status --short`;
- confirmation that public heads and tags contain only intended refs.

The private learning home and browser test artifacts are not release evidence
and must not be committed.
