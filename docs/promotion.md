# Review and promote staged content

Use Promotion after Capture Mode has staged a Pack Update and you are ready to
decide what may become accepted learning content.

Do not use Promotion to archive a source. Accepted text must be rewritten for
the Learning Pack, supported by the shown provenance, and free of private-only
details. Reject a proposal when that cannot be done safely.

## The short path

Start the local Product Shell:

```bash
opslearn serve --home /tmp/ops-learning-home --port 8000
```

Open `http://127.0.0.1:8000/` and choose a staged update.

The browser journey has four explicit steps:

```text
Review -> Preview without writing -> Confirm -> Accepted pack
```

Nothing is accepted during Review or Preview.

## 1. Review the immutable proposal

The review page shows:

- the proposed destination and why it matched;
- safe source type, observation time, and proposal digest;
- the staged text and fact status;
- private material categories that Capture Mode omitted;
- accepted claims that may be preserved as history.

No destination or decision is preselected. Enter the target pack ID and title,
then decide every proposal.

The page carries a signed snapshot of the exact accepted pack versions and
digests visible at Review time. Preview uses that snapshot. It does not silently
refresh to a newer base.

For an accepted proposal:

1. Write the accepted text independently in the blank field.
2. Choose its accepted fact status.
3. Choose `add`, `supersede`, or `contradict`.
4. Enter the earlier claim ID for `supersede` or `contradict`.
5. Confirm that private-only details were removed.

For a rejected proposal, choose one structured reason. Rejected staged text
does not enter the accepted pack, but the decision remains in Promotion
history.

## 2. Preview the exact change

Choose **Preview without writing**.

The preview places every staged proposal beside its exact accepted wording or
rejection. A deterministic summary separately lists removed proposals, retained
safe provenance and statuses, and generalized text or status. Check the status
and history relationship. The accepted pack still has not changed.

If another Promotion changes the pack before confirmation, the server returns
a conflict. It shows the decisions you entered, writes nothing, and asks you to
review and preview again. It never rebases a review silently.

## 3. Confirm once

The confirmation box starts unchecked. Check it only after the preview is
correct, then choose **Promote atomically**.

Promotion validates the immutable update, the expected pack version and
digest, every proposal decision, the sanitized text, and history targets under
one Promotion-wide lock. It writes the complete next pack to a temporary file,
syncs it, replaces the canonical file, and syncs the parent directory.

An identical retry returns the existing result. A different decision for an
already promoted update fails without writing.

Replacement is the commit point. If the later directory sync reports an error,
Promotion re-reads the canonical file. Exact intended bytes count as committed;
an unreadable or different result is reported as an uncertain commit instead of
being mislabeled as a clean failure.

## 4. Inspect the accepted artifact

The browser redirects to the accepted pack. The canonical artifact is:

```text
<learning-home>/packs/<pack-id>/pack.json
```

It contains accepted claims and the complete structured Promotion history in
one atomically replaced document. Safe claim provenance includes only:

- source type;
- observation time;
- staged update ID;
- proposal ID.

It excludes private source identifiers, raw paths, raw bytes, intake IDs, and
source content hashes.

Lesson assembly and export approval use `PackRepository.snapshot(pack_id)`.
That read-only snapshot exposes validated accepted content and its canonical
version and digest, without Promotion history or a capability to read staged or
private content. An export later uses its immutable bundle-and-snapshot
approval, not the mutable current Pack pointer.

## Command-line automation

The Product Shell is the normal learner path. The same service is available to
local automation:

```bash
opslearn promotion-review \
  --home /tmp/ops-learning-home \
  --update-id update-0123456789abcdef0123

opslearn promotion-preview \
  --home /tmp/ops-learning-home \
  --plan /tmp/promotion-plan.json

opslearn promotion-commit \
  --home /tmp/ops-learning-home \
  --plan /tmp/promotion-plan.json \
  --preview-sha256 <digest-from-preview>
```

The plan uses the exact base version and digest returned by
`promotion-review`. Each decision includes an action and either independently
sanitized accepted fields or a structured rejection reason. Unknown fields,
duplicate JSON keys, non-finite numbers, malformed decisions, and stale bases
fail closed.

### Optional exact canaries

Structural validation always rejects private storage markers such as intake
IDs, raw-file paths, and source content hashes. It cannot recognize every
possible private name or sentence.

For a known high-risk marker, configure its exact UTF-8 text:

```bash
opslearn serve \
  --home /tmp/ops-learning-home \
  --port 8000 \
  --forbidden-canary-file /tmp/private-canary.txt
```

`promotion-preview` and `promotion-commit` accept the same repeatable option.
Promotion scans every logical string in the complete resulting pack before JSON
serialization and blocks before write when a configured exact canary appears.
Newlines, quotes, and backslashes therefore cannot evade the check by becoming
JSON escape sequences.

These checks complement explicit human review. They are not a universal
sensitive-data detector.

## What proves success

Run:

```bash
./scripts/verify-fast
```

`PromotionHttpJourneyTests` is the highest-seam proof. It opens the real HTTP
review page, previews accepted and rejected decisions, confirms with Origin,
CSRF, and preview-integrity checks, follows the accepted pack, and proves a raw
private canary is absent.

`PromotionServiceTests` additionally prove:

- exact retry idempotency and one-use staged updates;
- exact retry recovery even when private staging is later unavailable;
- stale and concurrent decision conflicts;
- contradiction history;
- interrupted-write recovery;
- post-replacement sync ambiguity handling;
- deterministic removed, retained, and generalized summaries;
- configured exact-canary blocking;
- strict malformed-input handling;
- symlink and named-pipe rejection.

The release gate includes the test-only real-browser proof:

```bash
npm --prefix tests/browser ci
(cd tests/browser && npx playwright install chromium)
./scripts/verify
```

It uses Chromium and keyboard input only to prove native required-field
blocking, blank choices, Review-to-Preview focus order, explicit confirmation,
the final `303` pack redirect, and no horizontal overflow at 320 pixels. The
fixture creates only synthetic temporary state and removes it when the server
stops.
