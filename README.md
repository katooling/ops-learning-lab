# Ops Learning Lab

Ops Learning Lab turns selected work context into private, evidence-aware
learning packs. It is a local-first personal lab: raw context stays in a
private Learning Home, reviewed material becomes a Learning Pack, and progress
comes from practice rather than reading alone.

Version 0.1.0 proves one complete synthetic Codex ETL lesson:

```text
Capture context                         Learn deliberately
      |                                       |
      v                                       v
private intake -> staged proposal -> review + Promotion
                                           |
                                           v
                                accepted Learning Pack
                                           |
                                           v
                     Map -> Predict -> Try -> Prove -> Explain
                                           |
                                           v
                           later review -> Retained mastery
                                           |
                                           v
                              explicitly approved export
```

Raw intake, staged proposals, learner history, accepted content, and exports
are separate trust zones. Nothing publishes automatically.

## Install

Requirements:

- Python 3.11 or newer;
- no third-party runtime dependencies.

```bash
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -e .
```

## Try one safe learning journey

Create the Learning Home outside this Git repository:

```bash
opslearn init --home /tmp/ops-learning-home
cat >/tmp/synthetic-source.txt <<'EOF'
Synthetic Codex ETL usage and cost context.
Claim: Normalized synthetic cost should be non-negative.
EOF
opslearn capture \
  --home /tmp/ops-learning-home \
  --source-type pasted-text \
  --source-id synthetic-example \
  --input /tmp/synthetic-source.txt
opslearn serve --home /tmp/ops-learning-home --port 8000
```

Open `http://127.0.0.1:8000/`. Review the staged proposal, preview and confirm
Promotion, open the lesson, and follow:

```text
Map -> Predict -> Try -> Prove -> Explain
```

Restarting the server with the same Learning Home restores an active attempt.
A successful attempt schedules a later review; only a qualifying later attempt
earns **Retained** mastery.

The [complete first journey](docs/getting-started.md) explains every step,
including explicit Codex Capture/Learn requests, recovery, and export.

## Capture or Learn?

| Choice | What it does | What it never does |
|---|---|---|
| **Capture Mode** | Stores selected context and stages a proposal, then returns. | Starts or resumes an exercise. |
| **Learn Mode** | Performs the same capture, then opens or resumes one matching lesson. | Chooses between ambiguous packs or presses Start for you. |

Both paths preserve provenance. Neither path modifies the source conversation
or silently promotes raw material. See
[Capture and Learn semantics](docs/getting-started.md#choose-capture-or-learn).

## Documentation

- [Complete first journey](docs/getting-started.md)
- [Privacy boundaries](docs/privacy-boundaries.md)
- [Recovery guide](docs/recovery.md)
- [Release verification](docs/releasing.md)
- [Version 0.1.0 limitations](docs/limitations.md)
- [Domain language](CONTEXT.md)
- [Product specification](docs/specs/v1.md)
- [Architecture decisions](docs/adr/)
- [Explicit Codex import](docs/codex-import.md)
- [Promotion](docs/promotion.md)
- [Evidence-centered lesson](docs/evidence-centered-lesson.md)
- [Resume and retained mastery](docs/resume-and-review.md)
- [Standalone export](docs/standalone-export.md)
- [Changelog](CHANGELOG.md)

## Verify

For ordinary development:

```bash
./scripts/verify-fast
```

Before release, install the test-only browser tools once and run the single
release gate:

```bash
npm --prefix tests/browser ci
(cd tests/browser && npx playwright install chromium)
./scripts/verify
```

`verify` runs fast checks, complete-history safety, real Chromium journeys,
and a final publication audit. Playwright is a test dependency, not a product
dependency. See [Release verification](docs/releasing.md) for the exact gates
and the current pre-release history blocker.

## Safety

- Use only synthetic or sanitized material in this public repository.
- Keep Learning Homes, real conversations, screenshots, attachments, and
  internal documents outside the repository.
- Treat Promotion and export as explicit review decisions.
- The application sends no messages and mutates no external systems.

## License

[MIT](LICENSE)
