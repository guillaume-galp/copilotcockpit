---
id: TH3.E1.US5
title: "Materialized ledger projection and replay"
type: standard
priority: high
size: M
agents: [developer]
skills: [bdd-stories]
acceptance-criteria:
  - AC1: "ledger.json is derived only from the contiguous sequence of immutable committed events and records the matching latest revision."
  - AC2: "After committing an event, ledger.json.tmp is flushed and atomically replaces the projection without changing event authority."
  - AC3: "A missing, stale, corrupt, or interrupted ledger projection is rebuilt deterministically without duplicating committed events."
depends-on: [TH3.E1.US4]
---

As an overseer, I want a replayable ledger projection so that controller state
recovers exactly after interruption without becoming a second authority.

## Acceptance criteria

- [ ] AC1: The ledger is solely a projection of committed events.
- [ ] AC2: Projection replacement is atomic.
- [ ] AC3: Replay repairs derived state exactly once.

## BDD scenarios

### Happy path: committed event updates the ledger

Given a new immutable event was committed
When projection succeeds
Then the ledger atomically advances to the event's revision and derived state.

### Edge case: process exits after event commit

Given the event is committed but ledger replacement did not occur
When the next controller starts
Then replay advances the ledger exactly once without creating another event.

### Error case: ledger claims an unknown revision

Given the ledger revision is not represented by committed events
When reconciliation validates the projection
Then it discards the projection as derived corruption and rebuilds it.
