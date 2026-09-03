---
id: TH3.E1.US2
title: "Atomic event journal and portable locking"
type: standard
priority: high
size: M
agents: [developer]
skills: [bdd-stories]
acceptance-criteria:
  - AC1: "Every mutation acquires one bounded portable lock containing owner, host, command, and acquisition time."
  - AC2: "A successful mutation appends one revisioned event and atomically replaces any affected materialized record."
  - AC3: "Lock contention, stale-lock ambiguity, or write interruption surfaces an error without silently discarding authoritative data."
depends-on: [TH3.E1.US1]
---

As an overseer, I want serialized atomic control writes so that overlapping
commands and recurrent wakes cannot corrupt mission history.

## Acceptance criteria

- [ ] AC1: Mutations use one bounded `mkdir` lock with owner metadata.
- [ ] AC2: Events and projections advance through one monotonic revision.
- [ ] AC3: Contention and interrupted writes fail visibly and preserve evidence.

## BDD scenarios

### Happy path: mutation advances one revision

Given a valid control root with no active writer
When a lifecycle event is persisted
Then the journal gains one valid event and the projection has the same revision.

### Edge case: two writers contend

Given one process holds the control lock
When a second writer reaches its bounded wait
Then it exits without appending a duplicate event.

### Error case: lock ownership is ambiguous

Given a lock appears old but its owner cannot be proven absent
When automatic recovery runs
Then the lock is retained and guarded repair is required.
