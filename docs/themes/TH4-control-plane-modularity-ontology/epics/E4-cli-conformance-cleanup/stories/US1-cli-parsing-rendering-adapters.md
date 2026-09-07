---
id: TH4.E4.US1
title: "CLI parsing, rendering, and adapter thinning"
type: standard
priority: high
size: M
agents: [developer]
skills: [bdd-stories]
acceptance-criteria:
  - AC1: "CLI parsing, human-readable rendering, subprocess/tmux/queue adapters, and wrapper glue move last into thin adapter seams while `main()` and facade imports remain compatible."
  - AC2: "Public commands, help text, output shape, exit codes, environment handling, schema/state compatibility, dry-run behavior, and fail-closed diagnostics are unchanged."
  - AC3: "Domain modules do not import CLI/rendering/adapters after thinning, and adapter errors translate into existing fail-closed messages without guessing authority."
  - AC4: "Focused tests cover golden output for affected subcommands, wrapper invocation, adapter error translation, dry-run no-mutation, import compatibility, and unchanged schema/state fixtures."
  - AC5: "Rollback restores the prior facade CLI path without state migration, and touched terminology uses CLI, adapter, rendering, diagnostic observation, authority, and fail closed consistently."
depends-on: [TH4.E3.US5]
---

As a maintainer, I want the CLI thinned only after domains are extracted so that
presentation and adapters cannot drive domain behavior.

## Acceptance criteria

- [ ] AC1: CLI, rendering, and adapter glue is extracted behind stable `main()`.
- [ ] AC2: Commands, help/output, exit codes, schemas, dry-runs, and fail-closed diagnostics are unchanged.
- [ ] AC3: Domain-to-adapter dependency direction is preserved.
- [ ] AC4: Focused CLI/rendering/import tests prove behavior preservation.
- [ ] AC5: Rollback and adapter vocabulary are explicit.

## Compatibility guard

Every implementation session for this story must preserve public
imports/commands, schema/state compatibility, dry-run/fail-closed contracts,
Focused tests named above, and rollback through the `cockpit_control` facade.

## BDD scenarios

### Happy path: existing command renders identical output

Given a representative successful `cockpit-control` or `cockpit-overseer` command
When it runs through the thinned CLI adapter
Then stdout, stderr, and exit code match the pre-thinning golden contract.

### Edge case: adapter reports diagnostic-only pane data

Given tmux or queue adapter evidence is diagnostic-only
When the CLI renders it
Then output labels it consistently and no domain module treats it as authority.

### Error case: adapter dependency fails

Given a subprocess, tmux, or queue adapter call fails
When the CLI handles the error
Then the existing fail-closed diagnostic and exit semantics are preserved without mutating state.
