# Issue tracker: GitHub

Issues and product specifications for this repository live in GitHub Issues at
`katooling/ops-learning-lab`. Use the `gh` CLI for issue operations.

## Pull requests as a triage surface

External pull requests are not treated as incoming feature requests by the
automated triage workflow. Issues are the request surface; pull requests are
reviewed through the normal contribution workflow.

## Conventions

- Create, read, comment on, label, and close issues with `gh issue`.
- Infer the repository from the configured Git remote when possible.
- When a skill says to publish to the issue tracker, create a GitHub issue.
- Use one issue per fresh implementation context.
- Apply `ready-for-agent` only when acceptance criteria and blockers are clear.
- Use GitHub's native issue dependencies when available. Otherwise, include
  explicit `Blocked by` references in the issue body.
- Do not encode private source material in issue titles, bodies, or attachments.
