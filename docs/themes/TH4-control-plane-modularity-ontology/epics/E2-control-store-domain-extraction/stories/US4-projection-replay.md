---
id: TH4.E2.US4
title: "Projection and replay extraction"
type: standard
priority: high
size: M
agents: [developer]
skills: [bdd-stories]
acceptance-criteria:
  - AC1: "Ledger fold, deterministic projection, derived `events.jsonl` rebuild, replay, and projection repair move behind a projection seam while facade symbols and commands remain compatible."
  - AC2: "Ledger bytes, revision semantics, derived-view output, schema/state compatibility, public commands, and exit codes are unchanged."
  - AC3: "Dry-run replay remains byte-inert and corrupt, ahead, divergent, or malformed authoritative data still fails closed; existing ADR-019 replay durability checks remain in scope where crossed."
  - AC4: "Focused tests cover clean replay, missing/corrupt/stale/divergent ledger repair, derived view rebuild, dry-run, import compatibility, and unchanged preflight reports."
  - AC5: "Rollback restores the prior facade projection path without data migration, and touched terminology distinguishes authority, derived state, projection, and replay."
depends-on: [TH4.E2.US3]
---

As a maintainer, I want projection and replay isolated so that derived state can
be reasoned about separately from committed event authority.

## Acceptance criteria

- [ ] AC1: Projection and replay behavior is extracted behind the facade.
- [ ] AC2: Ledger bytes, derived views, command behavior, and schemas are unchanged.
- [ ] AC3: Dry-run and fail-closed replay behavior remain intact.
- [ ] AC4: Focused projection/replay/preflight/import tests prove equivalence.
- [ ] AC5: Rollback and authority-vs-derived vocabulary are explicit.

## Compatibility guard

Every implementation session for this story must preserve public
imports/commands, schema/state compatibility, dry-run/fail-closed contracts,
Focused tests named above, and rollback through the `cockpit_control` facade.

## BDD scenarios

### Happy path: ledger rebuild is byte-identical

Given a valid committed event sequence
When replay runs through the extracted projection seam
Then `ledger.json` and derived view bytes match a pre-extraction rebuild.

### Edge case: derived ledger is stale

Given committed events are ahead of `ledger.json`
When replay runs with and without `--dry-run`
Then dry-run reports the same planned rebuild without mutation and normal replay replaces only derived state.

### Error case: authoritative event is malformed

Given a committed event file is schema-invalid
When projection replay is requested
Then replay fails closed, names the authoritative repair need, and preserves existing files.
