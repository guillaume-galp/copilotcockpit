---
id: TH3.E2.US2
title: "Idempotent command envelopes and acknowledgements"
type: standard
priority: high
size: M
agents: [developer]
skills: [bdd-stories, e2e-cockpit]
acceptance-criteria:
  - AC1: "Every state-changing worker operation persists a command ID, mission ID, target, trace IDs, schema version, payload digest, and declared boundaries."
  - AC2: "Workers acknowledge accepted, applied, rejected, or duplicate outcomes and the acknowledgement is queryable after restart."
  - AC3: "Repeating an ID with the same digest is idempotent while repeating it with a different digest is rejected as a conflict."
depends-on: [TH3.E2.US1]
---

As an overseer, I want durable idempotent worker commands so that uncertain
delivery can be retried without creating duplicate work.

## Acceptance criteria

- [ ] AC1: Command envelopes contain all required identity and boundary fields.
- [ ] AC2: Acknowledgements survive controller restart.
- [ ] AC3: Replay and digest-conflict behavior is deterministic.

## BDD scenarios

### Happy path: command is accepted and applied

Given an idle capable worker and a new command ID
When dispatch succeeds
Then accepted and applied acknowledgements are persisted for that command.

### Edge case: acknowledgement was lost to the caller

Given the worker already applied the command
When the same ID and digest are retried
Then the stored result is returned without applying the command again.

### Error case: command ID is reused

Given a command ID already belongs to another payload digest
When a new payload uses that ID
Then the protocol rejects it and records a conflict.
