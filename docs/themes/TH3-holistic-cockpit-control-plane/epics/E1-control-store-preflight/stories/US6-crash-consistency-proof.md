---
id: TH3.E1.US6
title: "Deterministic crash-consistency and interleaving proof"
type: standard
priority: high
size: M
agents: [developer]
skills: [bdd-stories]
acceptance-criteria:
  - AC1: "Deterministic fault hooks cover every lock, repair, event, and projection interruption boundary defined by architecture section 8.4."
  - AC2: "Process-level barriers cover acquire/acquire, acquire/repair, release/acquire, repair/acquire, bounded timeout, and replacement preservation."
  - AC3: "Every injected interruption proves both safety and subsequent liveness without relying on sleep-only race timing."
depends-on: [TH3.E1.US3, TH3.E1.US5]
---

As a maintainer, I want deterministic crash and interleaving tests so that the
control store is proven safe at the exact filesystem boundaries reviewers found.

## Acceptance criteria

- [ ] AC1: Every documented interruption boundary is executable.
- [ ] AC2: Concurrent ownership transitions use deterministic coordination.
- [ ] AC3: Tests prove no corruption and eventual valid reacquisition.

## BDD scenarios

### Happy path: all protocol boundaries recover

Given fault hooks for each publication and quarantine boundary
When each interruption scenario runs
Then authoritative state is valid and a later writer can complete a mutation.

### Edge case: repair and acquisition race

Given repair holds the transition guard at stale-owner validation
When a second process attempts acquisition
Then it cannot replace the lock until repair completes or releases the guard.

### Error case: implementation uses timing-only coordination

Given a concurrency regression depends only on arbitrary sleeps
When the deterministic test gate validates coverage
Then the test is rejected until a barrier or fault hook targets the boundary.
