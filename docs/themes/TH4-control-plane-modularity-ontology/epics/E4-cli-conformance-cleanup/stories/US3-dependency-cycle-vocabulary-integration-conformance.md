---
id: TH4.E4.US3
title: "Dependency, cycle, vocabulary, and integration conformance"
type: standard
priority: high
size: M
agents: [developer]
skills: [bdd-stories]
acceptance-criteria:
  - AC1: "Final checks prove ADR-020 dependency direction, no circular imports, and bounded-context ownership across extracted modules."
  - AC2: "Full import compatibility, public commands, output contracts, exit codes, schemas, state compatibility, dry-run no-mutation, and fail-closed diagnostics remain unchanged for TH4-touched boundaries."
  - AC3: "Ontology vocabulary is aligned in files changed by TH4, with accepted legacy aliases documented rather than speculative renames."
  - AC4: "Focused conformance checks plus the full epic/theme gate cover import graph, CLI golden contracts, schema/replay equivalence, lock/journal/projection invariants crossed by TH4, controller/wake behavior, and integration resilience."
  - AC5: "Rollback guidance identifies the facade as the stable recovery point and confirms no control-store data migration was introduced."
depends-on: [TH4.E4.US2]
---

As a product owner, I want a final modularity and behavior-preservation proof so
that TH4 can be accepted without claiming new runtime functionality.

## Acceptance criteria

- [ ] AC1: Dependency direction and no-cycle conformance are proven.
- [ ] AC2: Imports, commands, outputs, schemas, dry-runs, and fail-closed behavior remain compatible.
- [ ] AC3: Ontology vocabulary is aligned in TH4-touched files.
- [ ] AC4: Focused conformance checks and the full theme gate pass.
- [ ] AC5: Rollback remains facade-based with no data migration.

## Compatibility guard

Every implementation session for this story must preserve public
imports/commands, schema/state compatibility, dry-run/fail-closed contracts,
Focused tests named above, and rollback through the `cockpit_control` facade.

## BDD scenarios

### Happy path: final theme gate confirms preserved behavior

Given all extraction stories are complete
When the final conformance checks and full theme gate run
Then dependency checks are acyclic and public behavior remains compatible.

### Edge case: vocabulary mismatch appears in a touched file

Given a TH4 change introduces a synonym for a canonical ontology term
When vocabulary conformance runs
Then the mismatch is corrected in that touched file or documented as a compatibility alias.

### Error case: domain module imports a CLI adapter

Given a lower-level module imports rendering, tmux, queue, scheduler, or CLI code
When dependency conformance runs
Then the check fails and TH4 cannot complete until the dependency direction is restored.
