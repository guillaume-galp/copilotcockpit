---
id: TH3.E2.US3
title: "Managed questions, cancellation, and replacement"
type: standard
priority: high
size: M
agents: [developer]
skills: [bdd-stories, e2e-cockpit]
acceptance-criteria:
  - AC1: "Questions, replies, and access-prompt responses are commands correlated to the active mission instead of unscoped temporary files or pane input."
  - AC2: "The overseer can request cooperative cancellation and observe acknowledgement or timeout through the protocol."
  - AC3: "Replacement terminates the prior mission as replaced, creates a new mission ID, and refuses a second active slot for the same worker."
depends-on: [TH3.E2.US2]
---

As an overseer, I want managed question, cancellation, and replacement commands
so that blocked or obsolete missions can recover without raw tmux operations.

## Acceptance criteria

- [ ] AC1: Worker interactions remain correlated to one active mission.
- [ ] AC2: Cancellation has a durable observable outcome.
- [ ] AC3: Replacement creates one new mission without mission bleed.

## BDD scenarios

### Happy path: blocked worker receives an answer

Given a worker has a pending mission question
When the overseer replies through the protocol
Then the answer is acknowledged and the worker may return to running.

### Edge case: cancellation precedes replacement

Given a running mission becomes obsolete
When cancellation is acknowledged and replacement is issued
Then the old mission is replaced and only the new mission is active.

### Error case: replacement targets a busy second slot

Given the worker already owns another valid active mission
When replacement would create two active slots
Then the command is rejected and the conflict is recorded.
