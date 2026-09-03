---
id: TH3.E3.US2
title: "Stale-worker recovery and conflict reconciliation"
type: standard
priority: high
size: M
agents: [developer]
skills: [bdd-stories, e2e-cockpit]
acceptance-criteria:
  - AC1: "Stale active workers follow a bounded nudge, troubleshoot, cancel, replace, or escalate decision without being marked failed solely by timeout."
  - AC2: "Two active missions for one worker retain the earliest valid accepted mission and block further dispatch."
  - AC3: "Ledger revision mismatch is repaired by journal replay while authoritative journal corruption requires explicit repair."
depends-on: [TH3.E3.US1, TH3.E2.US3]
---

As an operator, I want deterministic recovery from stale workers and split state
so that the cockpit does not compound uncertainty.

## Acceptance criteria

- [ ] AC1: Stale workers enter bounded recovery.
- [ ] AC2: Duplicate active missions block safely.
- [ ] AC3: Derived state is repairable without masking journal corruption.

## BDD scenarios

### Happy path: stale worker resumes after a nudge

Given a running mission is stale but its worker is reachable
When the controller sends one correlated nudge
Then a fresh lifecycle event returns the mission to running.

### Edge case: two missions claim one worker

Given two active records target the same worker
When reconciliation runs
Then the earliest valid accepted mission is retained and dispatch stops.

### Error case: journal cannot be replayed

Given the journal contains malformed authoritative data
When recovery runs
Then no state is guessed and explicit repair is requested.
