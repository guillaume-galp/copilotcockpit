---
id: TH3.E5.US2
title: "Control-plane contract and fault-injection coverage"
type: standard
priority: high
size: M
agents: [developer]
skills: [bdd-stories]
acceptance-criteria:
  - AC1: "Automated tests cover schemas, lifecycle transitions, command replay, reconciliation precedence, wake stop conditions, and migration."
  - AC2: "Fault tests cover interrupted writes, malformed state, lock contention, lost acknowledgements, stale workers, and overlapping wakes."
  - AC3: "The tests run through existing repository test categories and remain portable across supported Linux and macOS behavior."
depends-on: [TH3.E3.US3, TH3.E4.US2, TH3.E4.US3, TH3.E5.US1]
---

As a maintainer, I want contract and fault-injection tests so that control-plane
recovery behavior remains deterministic as tools evolve.

## Acceptance criteria

- [ ] AC1: Every architectural state contract has automated coverage.
- [ ] AC2: Required crash and concurrency failures are injected.
- [ ] AC3: Coverage integrates with the existing portable test gate.

## BDD scenarios

### Happy path: all control contracts pass

Given the VP3 implementation is complete
When the targeted and full repository gates run
Then all lifecycle, command, reconciliation, wake, and migration tests pass.

### Edge case: process stops between event and projection

Given a fault interrupts projection replacement after a valid event append
When recovery starts
Then replay rebuilds the correct ledger revision.

### Error case: duplicate command mutates twice

Given a test redelivers one command ID
When the implementation applies the payload more than once
Then the contract test fails with the duplicate action evidence.
