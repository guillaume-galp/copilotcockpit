---
id: TH3.E4.US1
title: "Mission-aware wake scheduling and termination"
type: standard
priority: high
size: M
agents: [developer]
skills: [bdd-stories, cockpit-wake]
acceptance-criteria:
  - AC1: "A VP3 wake stores mission, queue item, owner, intent, stop condition, cadence, blocker threshold, and lifecycle state."
  - AC2: "Scheduled jobs invoke a controller tick rather than directly pasting an autonomous mission prompt."
  - AC3: "Completion, cancellation, supersession, terminal queue state, fulfilled stop condition, or human suspension terminates or skips future action."
depends-on: [TH3.E1.US3, TH3.E3.US1]
---

As an overseer, I want recurrent wakes bound to durable mission intent so that
they survive context loss and stop when their purpose ends.

## Acceptance criteria

- [ ] AC1: Wake records preserve complete mission intent.
- [ ] AC2: Wakes trigger the managed controller.
- [ ] AC3: All documented stop conditions prevent further mission action.

## BDD scenarios

### Happy path: recurrent wake advances an active mission

Given an active wake and unfinished mission
When its schedule fires
Then one controller tick runs with the stored mission identity.

### Edge case: mission completed between schedules

Given the queue item became terminal after the previous wake
When the next schedule fires
Then the wake terminates without dispatching.

### Error case: wake lacks an owner

Given a legacy or malformed wake has no owner or mission
When it attempts a VP3 tick
Then it is blocked and migration guidance is reported.
