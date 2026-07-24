# Domain docs

Ops Learning Lab uses a single domain context.

## Before exploring

- Read the root `CONTEXT.md`.
- Read ADRs under `docs/adr/` that affect the area being changed.
- Proceed silently if a referenced domain file does not yet exist.

## Vocabulary

Use the canonical terms from `CONTEXT.md` in code, tests, issues, and
documentation. Do not replace precise terms with generic words such as
“content,” “record,” or “status” when the domain distinguishes them.

If a proposed change contradicts an ADR, surface the conflict explicitly rather
than silently overriding it.

## One context per issue

Each implementation issue must fit in one fresh agent context and deliver a
verifiable behavior. Keep dependencies explicit. Prefer a narrow vertical slice
through the public interface over separate schema, service, and interface
tickets.

## Domain changes

- Clarify vocabulary in `CONTEXT.md` when meaning changes.
- Add an ADR only for a durable decision with a real trade-off.
- Keep historical ADRs; supersede them explicitly rather than rewriting the
  decision history.
