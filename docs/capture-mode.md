# Capture context and review a staged Pack Update

Use Capture Mode when useful work context should become a learning proposal
without changing an accepted Learning Pack or starting a lesson.

Do not use it to publish content. Staged proposals stay local and private until
a later Promotion flow sanitizes and accepts selected material.

## 1. Initialize a learning home

Keep the learning home outside a Git worktree:

```bash
opslearn init --home /tmp/ops-learning-home
```

The command creates a private raw inbox and a separate private staging area.

## 2. Prepare a synthetic text source

Matching considers ordinary words. Only explicitly marked `Claim:` lines become
claim proposals. Unmarked lines remain in raw private intake.

```text
Synthetic Codex ETL usage and cost context.
Claim: Normalized synthetic cost should be non-negative.
Claim [historical]: A synthetic trial once reported zero cost.
```

Allowed statuses are `current`, `historical`, `contradicted`, and `unverified`.
An omitted status defaults to `unverified`.
Capture accepts UTF-8 regular files up to 1 MiB and rejects symbolic links,
named pipes, and devices before persistence.

## 3. Capture and stage

```bash
opslearn capture \
  --home /tmp/ops-learning-home \
  --source-type pasted-text \
  --source-id synthetic-example \
  --input /tmp/source.txt
```

The JSON result reports the immutable `update_id`, match kind, proposed pack,
and browser `review_path`. It never returns source bytes or filesystem paths.
Running the same command again returns the same result and creates no duplicate
intake or proposal.

Matching is intentionally conservative:

- one plausible pack produces a proposed destination;
- several plausible packs require learner choice;
- no plausible pack proposes a new pack.

All cases leave accepted content unchanged.

## 4. Open the local Product Shell

```bash
opslearn serve --home /tmp/ops-learning-home --port 8000
```

Open `http://127.0.0.1:8000/`, then choose the staged update. The page shows:

- why each pack matched;
- proposed claims and their fact status;
- safe provenance and immutable proposal digest;
- categories of private material that were not copied.

The server binds only to the local loopback interface. It has no raw-intake,
file, static-directory, or lesson route. Its only mutation is the explicit
review, preview, and Promotion flow described in the
[Promotion guide](promotion.md).

## What proves success

Run:

```bash
./scripts/verify
```

`CaptureModeJourneyTests.test_capture_stages_once_and_shell_never_serves_raw_intake`
is the highest-seam proof. It captures a private canary and an HTML payload
twice, opens the real HTTP review page, and proves:

- one intake and one staged update exist;
- accepted packs remain byte-for-byte unchanged;
- the canary, raw path, learning-home path, and executable HTML are not served;
- mutation methods and private paths are rejected;
- a restarted Product Shell can read the same immutable proposal.
