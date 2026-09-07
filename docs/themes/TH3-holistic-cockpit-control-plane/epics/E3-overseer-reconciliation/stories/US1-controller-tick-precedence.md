---
id: TH3.E3.US1
title: "One-action controller tick and evidence precedence"
type: standard
priority: high
size: M
agents: [developer]
skills: [bdd-stories, e2e-cockpit]
acceptance-criteria:
  - AC1: "A controller tick validates roots, replays new events, reads queue and worker state, and selects at most one state-changing action."
  - AC2: "Human decisions, queue state, durable events, ledger, validated reports, live status, and pane text follow ADR-014 precedence."
  - AC3: "A tick with no valid action persists its observation or escalation state and exits without repeated investigation."
depends-on: [TH3.E1.US7, TH3.E2.US2]
---

As an overseer, I want a deterministic short controller tick so that recurrent
operation is model-independent and token-efficient.

## Acceptance criteria

- [ ] AC1: One tick takes at most one state-changing action.
- [ ] AC2: Conflicting evidence follows documented ownership precedence.
- [ ] AC3: No-action ticks terminate cleanly.

## BDD scenarios

### Happy path: idle worker receives the next mission

Given the active queue item is implementable and one worker is idle
When the controller ticks
Then it persists exactly one dispatch command and exits.

### Edge case: pane and lifecycle status disagree

Given pane text looks available while durable lifecycle state is running
When the controller reconciles
Then lifecycle state wins and no second mission is dispatched.

### Error case: roots disagree

Given queue and control metadata reference different cockpit roots
When the controller ticks
Then dispatch is blocked and one conflict event is persisted.
