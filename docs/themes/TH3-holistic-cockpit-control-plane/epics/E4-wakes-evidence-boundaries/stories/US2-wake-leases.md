---
id: TH3.E4.US2
title: "Wake leases and duplicate suppression"
type: standard
priority: high
size: M
agents: [developer]
skills: [bdd-stories, cockpit-wake]
acceptance-criteria:
  - AC1: "Each mission tick acquires a short durable lease before reading or changing mission state."
  - AC2: "An overlapping invocation that loses the lease records one duplicate-skip event and exits without dispatch."
  - AC3: "Expired or abandoned leases are reconciled through explicit freshness rules and cannot be silently deleted."
depends-on: [TH3.E1.US2, TH3.E4.US1]
---

As an operator, I want wake leases so that overlapping cron executions cannot
duplicate overseer actions.

## Acceptance criteria

- [ ] AC1: Mission ticks hold one bounded lease.
- [ ] AC2: Lease losers are safe no-ops with evidence.
- [ ] AC3: Stale leases follow deterministic recovery.

## BDD scenarios

### Happy path: one wake owns the tick

Given no active mission lease
When a scheduled wake starts
Then it acquires the lease, performs one tick, and releases it.

### Edge case: two wakes overlap

Given one tick already owns the lease
When another schedule fires
Then the second records `wake-duplicate-skipped` and exits.

### Error case: prior process died with a lease

Given an expired lease remains after process failure
When the next tick starts
Then reconciliation records recovery before acquiring a new lease.
