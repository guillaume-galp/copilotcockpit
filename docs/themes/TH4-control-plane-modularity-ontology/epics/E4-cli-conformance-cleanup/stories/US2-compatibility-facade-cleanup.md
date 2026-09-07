---
id: TH4.E4.US2
title: "Compatibility facade cleanup and caller migration"
type: standard
priority: medium
size: M
agents: [developer]
skills: [bdd-stories]
acceptance-criteria:
  - AC1: "Repository callers are deliberately migrated to extracted modules only where safe, while `cockpit_control` continues to re-export public symbols required by wrappers, tests, and compatibility notes."
  - AC2: "No public command, output, exit code, schema/state file, dry-run contract, or fail-closed behavior changes during cleanup."
  - AC3: "Any facade symbol removal is deferred unless repository-wide callers are migrated and a compatibility note documents the change; otherwise aliases remain."
  - AC4: "Focused tests cover wrapper imports, repository caller imports, deprecated alias behavior if any, dry-run no-mutation, and unchanged CLI reports."
  - AC5: "Rollback restores facade re-exports without data migration, and touched terminology follows ontology names with compatibility aliases for accepted legacy names."
depends-on: [TH4.E4.US1]
---

As a maintainer, I want facade cleanup to be deliberate and reversible so that
compatibility is not broken by opportunistic import churn.

## Acceptance criteria

- [ ] AC1: Callers migrate only where safe and the facade keeps required re-exports.
- [ ] AC2: Public behavior, schemas, dry-runs, and fail-closed paths are unchanged.
- [ ] AC3: Symbol removal is deferred or documented with compatibility coverage.
- [ ] AC4: Focused caller/facade/import tests prove behavior preservation.
- [ ] AC5: Rollback and alias vocabulary are explicit.

## Compatibility guard

Every implementation session for this story must preserve public
imports/commands, schema/state compatibility, dry-run/fail-closed contracts,
Focused tests named above, and rollback through the `cockpit_control` facade.

## BDD scenarios

### Happy path: internal caller uses an extracted module

Given a repository caller can import a stable extracted module directly
When it is migrated from a facade-only import
Then public wrapper behavior and facade re-export behavior remain compatible.

### Edge case: accepted legacy name remains as an alias

Given a public symbol name is not ontology-perfect but is already accepted
When cleanup runs
Then the alias remains or is documented rather than being removed for aesthetics.

### Error case: external-style import would break

Given `from cockpit_control import <public_symbol>` is still used by a wrapper or test
When cleanup evaluates the symbol
Then the facade keeps the re-export and compatibility tests fail if it is removed.
