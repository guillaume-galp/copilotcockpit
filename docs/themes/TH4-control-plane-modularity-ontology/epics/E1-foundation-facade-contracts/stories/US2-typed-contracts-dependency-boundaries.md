---
id: TH4.E1.US2
title: "Typed contracts and dependency-boundary rules"
type: standard
priority: high
size: M
agents: [developer]
skills: [bdd-stories]
acceptance-criteria:
  - AC1: "Module seams for ADR-020 bounded contexts are introduced with explicit typed data contracts while the facade preserves existing dict/on-wire schemas."
  - AC2: "Dependency direction is checked so domain modules do not import CLI, rendering, tmux, queue, or scheduler adapters, and no cycles are introduced."
  - AC3: "Public imports/commands, schema/state compatibility, dry-run behavior, and fail-closed diagnostics are unchanged while contracts are introduced."
  - AC4: "Focused tests or static checks cover contract conversion, import compatibility, and dependency direction without requiring a full suite for this story."
  - AC5: "Rollback leaves callers on the existing facade implementation, and ontology vocabulary is used for all new seam names and comments."
depends-on: [TH4.E1.US1]
---

As a maintainer, I want typed boundaries and dependency rules defined up front so
that future extraction stories stay small and acyclic.

## Acceptance criteria

- [ ] AC1: Typed contracts exist at planned bounded-context seams.
- [ ] AC2: Dependency direction and no-cycle rules are checkable.
- [ ] AC3: Existing behavior, imports, schemas, dry-runs, and fail-closed paths remain unchanged.
- [ ] AC4: Focused contract and dependency checks are added.
- [ ] AC5: Rollback and vocabulary alignment are documented.

## Compatibility guard

Every implementation session for this story must preserve public
imports/commands, schema/state compatibility, dry-run/fail-closed contracts,
Focused tests named above, and rollback through the `cockpit_control` facade.

## BDD scenarios

### Happy path: a contract converts without changing the wire payload

Given an existing schema-valid event or command payload
When it is converted through a new typed contract boundary
Then the emitted dict/on-wire representation is byte-compatible with the prior behavior.

### Edge case: adapter dependency is attempted from a domain seam

Given a lower-level domain module
When dependency checks inspect its imports
Then CLI, rendering, tmux, queue, and scheduler adapter imports are refused.

### Error case: malformed payload reaches a contract boundary

Given malformed or future-version control data
When the typed boundary validates it
Then the existing fail-closed diagnostic is preserved and no state is mutated.
