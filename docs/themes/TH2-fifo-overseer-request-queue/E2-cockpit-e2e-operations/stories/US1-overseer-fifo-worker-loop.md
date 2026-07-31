---
id: TH2.E2.US1
title: "Overseer FIFO worker loop"
type: standard
priority: high
size: M
agents: [developer]
skills: [e2e-cockpit, bdd-stories]
acceptance-criteria:
  - AC1: "The overseer runbook starts only the next eligible FIFO item when no current item is active."
  - AC2: "The overseer reports current item, queue depth, state, worker assignment, blockers, and next item."
  - AC3: "The runbook prevents sending a second mission to a busy worker."
depends-on: [TH2.E1.US2]
---

As an overseer, I want a FIFO worker loop so that ideas are delivered one at a
time without disrupting active worker missions.

## Acceptance criteria

- [ ] AC1: The overseer starts only the next eligible FIFO item.
- [ ] AC2: The overseer reports queue state and worker assignment.
- [ ] AC3: The runbook prevents worker mission bleed.

## BDD scenarios

### Happy path: start next item

Given no current item is active
And the queue has at least one `queued` item
When the overseer starts the next item
Then the oldest eligible item transitions to `shaping`
And the overseer reports queue depth and current item.

### Edge case: worker busy

Given a queue item is ready for implementation
And worker-dev is busy
When the overseer evaluates dispatch
Then no new mission is sent to worker-dev
And the queue item remains waiting with a blocker.

### Error case: active item exists

Given an item is already active
When the overseer runs `start-next`
Then no second item starts
And the CLI reports the active item.
