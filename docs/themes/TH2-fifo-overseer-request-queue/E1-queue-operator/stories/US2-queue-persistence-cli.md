---
id: TH2.E1.US2
title: "Queue persistence and CLI operations"
type: standard
priority: high
size: M
agents: [developer]
skills: [bdd-stories]
acceptance-criteria:
  - AC1: "Queue items persist as one YAML file per item under docs/queue/items/."
  - AC2: "Every state transition appends a JSONL event with previous state, next state, actor, timestamp, and reason."
  - AC3: "The CLI supports enqueue, list, inspect, classify, pause, resume, reject, start-next, and clear-current."
  - AC4: "Invalid transitions fail loudly and do not mutate item state or event history."
depends-on: [TH2.E1.US1]
---

As an overseer, I want a token-efficient queue CLI so that queue management does
not require repeatedly loading large backlog files or boilerplate prompts.

## Acceptance criteria

- [ ] AC1: Queue items persist as `docs/queue/items/<id>.yaml`.
- [ ] AC2: State transitions append events to `docs/queue/events.jsonl`.
- [ ] AC3: CLI supports the MVP queue commands.
- [ ] AC4: Invalid transitions fail without mutation.

## BDD scenarios

### Happy path: inspect one queue item

Given multiple queue items exist
When the overseer inspects one item
Then only that item state and relevant events are returned.

### Edge case: paused queue

Given the queue is paused
When the overseer runs `start-next`
Then no new item starts
And the CLI reports that the queue is paused.

### Error case: invalid transition

Given an item is in `queued` state
When the CLI attempts to clear it directly
Then the command fails
And no item file or event log mutation occurs.
