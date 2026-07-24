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

## Current pre-release blocker

As of 2026-07-24, the history audit intentionally fails. Two reachable squash
commits use the same unapproved author address. The audit reports their object
IDs and a one-way email fingerprint; it does not repeat the address.

This is a real release blocker, not a test exception. Do not add the address to
the allowlist. The history must be rewritten once, reviewed, and force-pushed
before tagging version 0.1.0.

## Safe one-time history rewrite

History rewriting changes every descendant object ID. Freeze merges and ask
collaborators to stop pushing first. Ensure the public remote has no feature
branch or existing tag that still retains the old commits:

```bash
git ls-remote --heads origin
git ls-remote --tags origin
```

Only `refs/heads/main` should remain. If another public ref exists, stop and
decide whether to delete it or include it in the rewrite.

Install `git-filter-repo` from its official package, then work in a fresh
temporary clone:

```bash
release_root=$(mktemp -d)
git clone --mirror https://github.com/katooling/ops-learning-lab.git \
  "$release_root/private-backup.git"
git clone https://github.com/katooling/ops-learning-lab.git \
  "$release_root/rewrite"
cd "$release_root/rewrite"

old_main=$(git rev-parse origin/main)
old_email=$(git show -s --format=%ae 95168f9)
second_email=$(git show -s --format=%ae 2a9f20b)
test "$old_email" = "$second_email"
new_email=72454341+Mohamad-Kamar@users.noreply.github.com
printf 'Mohamad Kamar <%s> <%s>\n' "$new_email" "$old_email" \
  >"$release_root/release.mailmap"

git filter-repo --force --mailmap "$release_root/release.mailmap"
git remote add origin https://github.com/katooling/ops-learning-lab.git
```

The mailmap file is deliberately outside the repository. Keep the mirror
backup private: it contains the history being removed.

Prove the rewritten candidate before changing the remote:

```bash
npm --prefix tests/browser ci
(cd tests/browser && npx playwright install chromium)
./scripts/verify

test "$(git ls-remote origin refs/heads/main | cut -f1)" = "$old_main"
git push --force-with-lease=refs/heads/main:"$old_main" origin main
```

Delete or replace any open local clone after the force-push. Do not merge work
created from the old history.

Clone again and prove what the public remote actually serves:

```bash
cd "$release_root"
git clone https://github.com/katooling/ops-learning-lab.git public-proof
cd public-proof
npm --prefix tests/browser ci
(cd tests/browser && npx playwright install chromium)
./scripts/verify
```

Keep the private mirror until this fresh-clone proof passes. Then remove the
temporary mailmap and rewrite clone. Retain or securely remove the mirror
according to your backup policy.

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
