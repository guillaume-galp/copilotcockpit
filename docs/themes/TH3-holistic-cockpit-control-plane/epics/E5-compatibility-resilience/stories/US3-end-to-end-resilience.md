---
id: TH3.E5.US3
title: "End-to-end control-plane resilience proof"
type: standard
priority: high
size: M
agents: [developer]
skills: [bdd-stories, e2e-cockpit, e2e-operator]
acceptance-criteria:
  - AC1: "A governed scenario creates a queue-backed mission, dispatches it, resets the overseer, and reconstructs the active state without chat history."
  - AC2: "The scenario detects a stalled worker, performs managed cancel or replacement, and completes or reaches bounded human escalation."
  - AC3: "Completion links worker and governed test evidence, clears the queue item, and proves the recurrent wake terminated."
depends-on: [TH3.E5.US2]
---

As a product owner, I want an end-to-end resilience proof so that VP3 is accepted
for real cockpit operation rather than isolated unit behavior.

## Acceptance criteria

- [ ] AC1: Overseer restart recovery is demonstrated.
- [ ] AC2: Stalled mission recovery or bounded escalation is demonstrated.
- [ ] AC3: Evidence-backed clearance and wake termination are demonstrated.

## BDD scenarios

### Happy path: restart and replacement complete the mission

Given a queue-backed mission with an active recurrent wake
When the overseer is reset and the worker later stalls
Then state is reconstructed, the worker is replaced through the protocol, and
the replacement completes with governed test evidence.

### Edge case: human decision is required

Given three blocked ticks cannot recover the mission
When the final escalation runs
Then the wake suspends and the pending decision contains complete evidence.

### Error case: required clearance evidence is absent

Given the worker reports completion without the configured governed test result
When queue clearance is attempted
Then clearance fails and the wake does not report successful delivery.
