---
id: TH4.E3.US1
title: "Worker lifecycle and freshness extraction"
type: standard
priority: high
size: M
agents: [developer]
skills: [bdd-stories]
acceptance-criteria:
  - AC1: "Worker lifecycle vocabulary, transition validation, lifecycle event builders, heartbeat, freshness, stale observation, and lifecycle folding move behind a lifecycle seam while facade symbols and commands remain compatible."
  - AC2: "Lifecycle schemas, state values, event payloads, command output, exit codes, dry-run behavior, and materialized state compatibility are unchanged."
  - AC3: "Invalid transitions, regressing sequences, missing freshness, malformed evidence, and future lifecycle schemas fail closed without mutating authority."
  - AC4: "Focused tests cover transition table behavior, heartbeat refresh, stale observation, sequence regression, dry-run, import compatibility, and unchanged lifecycle CLI reports."
  - AC5: "Rollback restores the prior facade lifecycle implementation, and touched terminology uses worker lifecycle, heartbeat, freshness, stale observation, authority, and diagnostic observation."
depends-on: [TH4.E2.US4]
---

As a maintainer, I want lifecycle logic isolated so that worker state vocabulary
and freshness rules are owned by one bounded context.

## Acceptance criteria

- [ ] AC1: Lifecycle and freshness logic is extracted behind the facade.
- [ ] AC2: Lifecycle schemas, output, dry-runs, and state compatibility are unchanged.
- [ ] AC3: Invalid lifecycle data fails closed without mutation.
- [ ] AC4: Focused lifecycle/import tests prove behavior preservation.
- [ ] AC5: Rollback and lifecycle vocabulary are explicit.

## Compatibility guard

Every implementation session for this story must preserve public
imports/commands, schema/state compatibility, dry-run/fail-closed contracts,
Focused tests named above, and rollback through the `cockpit_control` facade.

## BDD scenarios

### Happy path: running heartbeat refreshes freshness

Given a worker mission is already running
When a valid running lifecycle record is emitted through the extracted seam
Then the materialized freshness and CLI report match prior behavior.

### Edge case: stale is only an observation

Given `fresh_until` has expired
When lifecycle state is observed through the extracted seam
Then the mission is reported stale without claiming failure or advancing queue state.

### Error case: lifecycle sequence regresses

Given a lower sequence lifecycle event arrives after a newer record
When folding runs through the extracted seam
Then the older event is retained as evidence but cannot regress materialized state.
