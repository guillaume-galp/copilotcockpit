---
id: TH4.E3.US5
title: "Wake lease and scheduling extraction"
type: standard
priority: high
size: M
agents: [developer]
skills: [bdd-stories]
acceptance-criteria:
  - AC1: "Wake intent, wake lease records, duplicate suppression, stop-condition checks, scheduler-facing adapters, and wake-to-controller integration move behind a wake/scheduling seam while facade symbols and commands remain compatible."
  - AC2: "Wake metadata, generated command behavior, output, exit codes, dry-run behavior, schema/state compatibility, and controller one-action semantics are unchanged."
  - AC3: "Overlapping wakes, stale leases, terminal queue states, human suspension, malformed wake metadata, and future schemas fail closed or skip according to existing contracts; R3 coverage remains limited to crossed duplicate/lease safety invariants."
  - AC4: "Focused tests cover schedule/list/cancel, generated job guard, duplicate lease skip, stale lease recovery, stop conditions, dry-run, import compatibility, and unchanged wake/controller reports."
  - AC5: "Rollback restores the prior facade wake integration without migrating scheduled jobs, and touched terminology uses wake intent, wake lease, stop condition, duplicate suppression, and bounded recovery."
depends-on: [TH4.E3.US4]
---

As a maintainer, I want wake and scheduling behavior isolated so that scheduler
adapters do not own controller decisions.

## Acceptance criteria

- [ ] AC1: Wake lease and scheduling behavior is extracted behind the facade.
- [ ] AC2: Wake metadata, generated jobs, output, dry-runs, and state compatibility are unchanged.
- [ ] AC3: Duplicate, stale, suspended, terminal, and malformed wake cases remain safe.
- [ ] AC4: Focused wake/controller/import tests prove behavior preservation.
- [ ] AC5: Rollback and wake vocabulary are explicit.

## Compatibility guard

Every implementation session for this story must preserve public
imports/commands, schema/state compatibility, dry-run/fail-closed contracts,
Focused tests named above, and rollback through the `cockpit_control` facade.

## BDD scenarios

### Happy path: scheduled wake invokes one controller tick

Given an active wake intent and no competing lease
When the generated schedule fires through the extracted seam
Then it invokes the controller tick once and preserves the existing report format.

### Edge case: overlapping wake loses the lease

Given another tick already owns the wake lease
When an overlapping invocation runs
Then it records or reports duplicate-skip behavior exactly as before without dispatching.

### Error case: terminal queue state is observed

Given the linked queue item is terminal or the wake is human-suspended
When the wake guard runs
Then it fails closed or skips according to the existing contract and does not advance mission state.
