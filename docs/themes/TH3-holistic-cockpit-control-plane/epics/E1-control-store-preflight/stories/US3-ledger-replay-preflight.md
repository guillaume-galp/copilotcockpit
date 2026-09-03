---
id: TH3.E1.US3
title: "Ledger replay and control-plane preflight"
type: standard
priority: high
size: M
agents: [developer]
skills: [bdd-stories]
acceptance-criteria:
  - AC1: "The materialized ledger can be rebuilt deterministically from valid journal events after deletion or corruption."
  - AC2: "Preflight reports root, schema, lock, queue, worker capability, and declared-path readiness without changing state."
  - AC3: "Malformed authoritative journal data blocks mutation and identifies the failing record for explicit repair."
depends-on: [TH3.E1.US2]
---

As an operator, I want replay and preflight diagnostics so that a cleared or
crashed overseer can recover safely before dispatching work.

## Acceptance criteria

- [ ] AC1: Ledger replay reproduces the latest valid revision.
- [ ] AC2: Read-only preflight reports all required control-plane readiness.
- [ ] AC3: Journal corruption fails closed with actionable evidence.

## BDD scenarios

### Happy path: missing ledger is rebuilt

Given a complete valid journal and no ledger file
When reconciliation starts
Then the ledger is atomically rebuilt to the journal's latest revision.

### Edge case: legacy worker capability is detected

Given a reachable worker without VP3 lifecycle support
When preflight runs
Then it reports `legacy-observed` and the unsupported managed operations.

### Error case: malformed journal entry

Given one authoritative journal line is invalid
When replay runs
Then mutation is blocked and the line location is reported.
