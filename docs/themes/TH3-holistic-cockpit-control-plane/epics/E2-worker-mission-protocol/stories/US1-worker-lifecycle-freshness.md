---
id: TH3.E2.US1
title: "Structured worker lifecycle and freshness"
type: standard
priority: high
size: M
agents: [developer]
skills: [bdd-stories, e2e-cockpit]
acceptance-criteria:
  - AC1: "Workers emit versioned accepted, running, blocked, completed, failed, cancelled, and replaced lifecycle events with mission and trace identifiers."
  - AC2: "Lifecycle sequence numbers cannot regress a terminal or newer materialized state."
  - AC3: "Heartbeat expiry produces a stale observation with a recoverable reason and never directly claims mission failure."
depends-on: [TH3.E1.US3]
---

As an overseer, I want structured worker lifecycle evidence so that availability
is not inferred from pane prompts.

## Acceptance criteria

- [ ] AC1: All lifecycle states are machine-readable and correlated.
- [ ] AC2: Late events cannot regress mission state.
- [ ] AC3: Freshness expiry is distinct from failure.

## BDD scenarios

### Happy path: worker completes a mission

Given a worker accepted and started the active mission
When it emits a matching completion event
Then the mission becomes completed with its evidence references.

### Edge case: a late running event arrives

Given the mission is already completed at a higher sequence
When an older running event is received
Then it is retained for audit but does not change current state.

### Error case: heartbeat expires

Given a running worker has passed `fresh_until`
When status is reconciled
Then it is reported stale and awaits a bounded recovery action.
