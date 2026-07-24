# Ops Learning Lab

Ops Learning Lab turns raw work context into private, evidence-aware learning packs.

The first release is local-first. It captures source material into a private learning home, preserves provenance, stages reviewed pack updates, teaches through safe simulations, and exports only explicitly promoted content.

## Current phase

The first vertical slice now reaches one complete evidence-centered lesson:

```text
raw source -> private intake + provenance manifest
        |       (unmarked raw lines stay private)
        v
deterministic match -> immutable staged proposal -> explicit Promotion
                                                    |
                                                    v
                                         accepted Learning Pack
                                                    |
                                                    v
                         Map -> Predict -> Try -> Prove -> Explain -> Review
```

## Product documentation

- [Domain language](CONTEXT.md)
- [Version one product specification](docs/specs/v1.md)
- [Architecture decisions](docs/adr/)
- [Capture Mode guide](docs/capture-mode.md)
- [Staged Pack Update contract](docs/staged-update-contract.md)
- [Promotion guide](docs/promotion.md)
- [Accepted Learning Pack contract](docs/accepted-pack-contract.md)
- [Evidence-centered lesson guide](docs/evidence-centered-lesson.md)
- [Contributor and agent guidance](AGENTS.md)

## Requirements

- Python 3.11 or newer
- No third-party runtime dependencies

## Try the Capture Mode tracer

```bash
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -e .

opslearn init --home /tmp/ops-learning-home
cat >/tmp/source.txt <<'EOF'
Synthetic Codex ETL usage and cost context.
Claim: Normalized synthetic cost should be non-negative.
EOF
opslearn capture \
  --home /tmp/ops-learning-home \
  --source-type pasted-text \
  --source-id example-1 \
  --input /tmp/source.txt
opslearn serve --home /tmp/ops-learning-home --port 8000
```

Open `http://127.0.0.1:8000/`. The shell reviews only the staged proposal, then
requires a no-write preview and explicit confirmation before Promotion. After
Promotion, the accepted Codex ETL pack opens one public synthetic lesson. The
shell has no route for raw intake.

In another terminal, the privacy check remains available:

```bash
opslearn audit-privacy \
  --home /tmp/ops-learning-home \
  --canary-file /tmp/source.txt
```

The final command succeeds only when the source bytes are absent from `packs/`, `snapshots/`, and `exports/`.

## Verify

```bash
./scripts/verify
```

The verification command runs the black-box CLI and domain tests, Python
compilation, relative-file link checks, the publication audit, and a clean
editable-install smoke test.

The optional test-only Chromium journey has its own explicit dependency step:

```bash
npm --prefix tests/browser ci
(cd tests/browser && npx playwright install chromium)
./scripts/verify-browser
```

CI runs this browser proof in a separate job. Playwright is not a product
runtime dependency.

## Safety

- Use only synthetic or sanitized material in this public repository.
- Keep real conversations, screenshots, attachments, and internal documents outside the repository.
- Capture and audit commands operate only on the learning home passed with `--home`.
- The application does not send messages or mutate external systems.

## License

[MIT](LICENSE)
