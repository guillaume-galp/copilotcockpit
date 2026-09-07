---
id: TH4.E2.US2
title: "Lock ownership, acquire/release, and repair extraction"
type: standard
priority: high
size: M
agents: [developer]
skills: [bdd-stories]
acceptance-criteria:
  - AC1: "Portable lock acquisition, exact release, owner validation, stale repair, quarantine, and debris diagnostics move behind a lock seam while facade symbols and commands remain compatible."
  - AC2: "Lock directory layout, owner metadata, timeout behavior, repair output, schema/state compatibility, and dry-run no-mutation behavior are unchanged."
  - AC3: "Fail-closed repair still refuses live, unproven, malformed, or replacement-raced locks; ADR-018 safety invariants crossed by the extraction keep their existing R3/fault coverage."
  - AC4: "Focused tests cover acquire/release, contention, stale repair, dry-run repair, replacement preservation, interruption matrix entries, and import compatibility."
  - AC5: "Rollback restores the prior facade implementation without changing lock data, and touched terminology uses lock owner, guarded repair, quarantine, and fail closed."
depends-on: [TH4.E2.US1]
---

As a maintainer, I want lock ownership logic isolated so that concurrency rules
remain reviewable without changing the accepted portable protocol.

## Acceptance criteria

- [ ] AC1: Lock protocol behavior is extracted behind a facade-preserved seam.
- [ ] AC2: Lock layout, metadata, output, state compatibility, and dry-run behavior are unchanged.
- [ ] AC3: Fail-closed repair and existing ADR-018 invariants remain covered.
- [ ] AC4: Focused lock, interruption, and import tests prove equivalence.
- [ ] AC5: Rollback and canonical lock vocabulary are explicit.

## Compatibility guard

Every implementation session for this story must preserve public
imports/commands, schema/state compatibility, dry-run/fail-closed contracts,
Focused tests named above, and rollback through the `cockpit_control` facade.

## BDD scenarios

### Happy path: owner acquires and releases the lock

Given no authoritative `control.lock` exists
When an existing command acquires and releases through the extracted lock seam
Then owner metadata, output, and cleanup match the previous behavior.

### Edge case: dry-run repair sees stale lock debris

Given a diagnosable stale lock and private candidate debris
When repair runs with `--dry-run`
Then the same would-quarantine diagnostics are rendered and no filesystem entry changes.

### Error case: live or replacement lock is repaired

Given owner death is unproven or the lock was replaced after validation
When repair is attempted through the extracted seam
Then the operation fails closed and preserves the authoritative lock.
