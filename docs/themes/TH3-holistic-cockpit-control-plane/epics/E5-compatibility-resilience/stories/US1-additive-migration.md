---
id: TH3.E5.US1
title: "Additive migration and legacy compatibility"
type: standard
priority: medium
size: M
agents: [developer]
skills: [bdd-stories]
acceptance-criteria:
  - AC1: "Existing queue items, trace archives, wake schedules, project-owned launchers, and overlays remain intact during VP3 adoption."
  - AC2: "Doctor identifies legacy wakes and workers and reports the exact migration or capability limitation."
  - AC3: "Schema migration creates backups, is idempotent, and refuses unknown future versions without mutation."
depends-on: [TH3.E1.US7, TH3.E2.US3, TH3.E4.US1]
---

As an existing cockpit operator, I want additive migration so that resilience
improvements do not destroy project-specific configuration or history.

## Acceptance criteria

- [ ] AC1: Existing durable and project-owned artefacts are preserved.
- [ ] AC2: Legacy capability is explicit and actionable.
- [ ] AC3: Migration is backed up, idempotent, and version-safe.

## BDD scenarios

### Happy path: legacy cockpit is upgraded

Given an existing queue, traces, wakes, and project-owned launchers
When VP3 migration runs
Then control state is added and prior artefacts remain unchanged.

### Edge case: migration is rerun

Given the cockpit already uses the current schema
When migration runs again
Then it reports current state and creates no destructive changes.

### Error case: future schema is present

Given control metadata has a newer unsupported version
When migration starts
Then it exits without rewriting the control root.
