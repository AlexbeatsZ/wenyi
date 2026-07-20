# Domain Docs

How engineering skills should consume this repository's domain documentation.

## Before exploring

- Read `CONTEXT.md` at the repository root when it exists.
- If `CONTEXT-MAP.md` exists, follow it to the context relevant to the task.
- Read relevant ADRs under `docs/adr/` and any context-local `src/<context>/docs/adr/` directories.
- If these files do not exist, proceed silently; they are created only when needed.

## Layout

This is a single-context repository. The conventional locations are root `CONTEXT.md` and `docs/adr/`.

## Vocabulary and decisions

Use the domain terms defined in `CONTEXT.md`. Surface any conflict with an existing ADR instead of silently overriding it.
