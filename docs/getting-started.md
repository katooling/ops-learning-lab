# Complete your first learning journey

Use this guide to turn one small piece of synthetic context into reviewed
learning, complete the lesson, resume it after interruption, and prepare a
standalone export.

Do not use real company conversations, credentials, customer data, or private
attachments while learning the product.

## 1. Install the local command

```bash
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -e .
```

The installed `opslearn` command has no network service and no third-party
runtime dependency.

## 2. Create a private Learning Home

Keep it outside the source repository:

```bash
opslearn init --home /tmp/ops-learning-home
```

The Learning Home contains several zones. Raw bytes and learner history are
private. Accepted packs, sanitized snapshots, and exports have separate,
narrow write paths. See the [privacy-boundary map](privacy-boundaries.md).

## 3. Choose Capture or Learn

Use **Capture Mode** while doing other work. It stores the selected context,
stages a proposal, reports identifiers, and stops.

Use **Learn Mode** when you deliberately want to enter the Product Shell. It
performs the same private capture first, then returns the route for one
unambiguous accepted lesson. It resumes exactly one active matching attempt.
It never chooses between ambiguous packs or starts an attempt for you.

```text
Capture: source -> private intake -> staged proposal -> return

Learn:   source -> private intake -> staged proposal
                                            |
                                            v
                                  open/resume lesson route
```

Both are read-only toward the source. Neither automatically promotes or
exports anything.

### Capture a file

```bash
cat >/tmp/synthetic-source.txt <<'EOF'
Synthetic Codex ETL usage and cost context.
Claim: Normalized synthetic cost should be non-negative.
EOF

opslearn capture \
  --home /tmp/ops-learning-home \
  --source-type pasted-text \
  --source-id synthetic-example \
  --input /tmp/synthetic-source.txt
```

The result contains an `intake_id`, immutable `update_id`, and `review_path`.
Repeating the exact capture is idempotent.

### Capture an explicit Codex extract

```bash
opslearn codex-import --home /tmp/ops-learning-home <<'JSON'
{
  "schema_version": 1,
  "mode": "capture",
  "source": {
    "kind": "pasted_text",
    "source_id": "synthetic-codex-note",
    "observed_at": "2026-07-24T12:00:00Z",
    "text": "Synthetic Codex ETL context."
  }
}
JSON
```

Change `"mode": "capture"` to `"mode": "learn"` only when you want the
command to return an open or resume route. Selected Codex task imports must
name exact turns or an inclusive range. Whole-history requests fail closed.
The [Codex import guide](codex-import.md) defines that boundary.

## 4. Review and promote

Start the local Product Shell:

```bash
opslearn serve --home /tmp/ops-learning-home --port 8000
```

Open `http://127.0.0.1:8000/`.

1. Open the staged update.
2. Read why it matched the proposed pack.
3. Accept only independently sanitized claims.
4. Reject private or unsupported material.
5. Preview the exact resulting pack.
6. Confirm Promotion.

Promotion is the only path from private/staged material to accepted pack
content. A preview writes nothing. Confirmation binds the immutable proposal,
decisions, and accepted result.

## 5. Complete the Learning Loop

Open the promoted pack and lesson:

1. **Map** the synthetic system.
2. **Predict** before seeing the result.
3. **Try** the resettable scenario.
4. **Prove** the exact claim with suitable Evidence Cards.
5. **Explain** what happened and why.

Reading alone can introduce a concept. A qualifying completed attempt earns
**Demonstrated** mastery.

## 6. Prove recovery and retention

Stop the server during an active attempt, start it again with the same
Learning Home, and reopen Attempt history. The Product Shell restores the
exact durable checkpoint.

After a successful attempt, a review becomes due seven days later. A
qualifying review earns **Retained** mastery. An unsuccessful review keeps
Demonstrated mastery and schedules a retry. See the [Recovery guide](recovery.md).

## 7. Approve and export one exact snapshot

The lesson page identifies its immutable `bundle_sha256`. Review the complete
canonical bundle under `snapshots/learning-packs/`, then approve that exact
digest:

```bash
opslearn export-approve \
  --home /tmp/ops-learning-home \
  --bundle-sha256 <reviewed-bundle-sha256>

opslearn export \
  --home /tmp/ops-learning-home \
  --bundle-sha256 <reviewed-bundle-sha256> \
  --canary-file /tmp/synthetic-source.txt
```

The result is one offline HTML file under `exports/`. Approval is immutable
and specific to one accepted revision. Export scans the configured canary but
does not claim to detect every possible secret. Follow the
[Standalone export guide](standalone-export.md) before sharing an artifact.

## What proves the product

```bash
./scripts/verify-fast
```

Before a release, run the stronger one-command gate described in
[Release verification](releasing.md). It adds complete-history checks and real
Chromium journeys at a narrow viewport with keyboard operation.
