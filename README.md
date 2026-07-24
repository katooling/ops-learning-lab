# Ops Learning Lab

Ops Learning Lab turns raw work context into private, evidence-aware learning packs.

The first release is local-first. It captures source material into a private learning home, preserves provenance, stages reviewed pack updates, teaches through safe simulations, and exports only explicitly promoted content.

## Current phase

Phase one establishes the trust boundary:

```text
raw source -> private intake + provenance manifest
                         |
                         +-> privacy audit -> publishable areas
```

It does not yet generate lessons or provide the browser learner experience.

## Product documentation

- [Domain language](CONTEXT.md)
- [Version one product specification](docs/specs/v1.md)
- [Architecture decisions](docs/adr/)
- [Contributor and agent guidance](AGENTS.md)

## Requirements

- Python 3.11 or newer
- No third-party runtime dependencies

## Try the phase-one tracer

```bash
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -e .

opslearn init --home /tmp/ops-learning-home
printf 'synthetic private note\n' > /tmp/source.txt
opslearn capture \
  --home /tmp/ops-learning-home \
  --source-type pasted-text \
  --source-id example-1 \
  --input /tmp/source.txt
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

## Safety

- Use only synthetic or sanitized material in this public repository.
- Keep real conversations, screenshots, attachments, and internal documents outside the repository.
- Capture and audit commands operate only on the learning home passed with `--home`.
- The application does not send messages or mutate external systems.

## License

[MIT](LICENSE)
