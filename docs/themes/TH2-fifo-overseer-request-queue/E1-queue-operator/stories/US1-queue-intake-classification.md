---
id: TH2.E1.US1
title: "Queue intake and classification"
type: standard
priority: high
size: M
agents: [developer]
skills: [bdd-stories, e2e-cockpit]
acceptance-criteria:
  - AC1: "The queue operator accepts build-method-tagged or overseer-approved build requests into FIFO state."
  - AC2: "The queue operator rejects or classifies non-build requests without adding them to the active FIFO queue."
  - AC3: "Each accepted item records source text, actor, timestamp, scope, initial state, and eligibility rationale."
depends-on: []
---

As an overseer, I want buildable ideas classified at intake so that only valid
delivery work enters the FIFO queue.

## Acceptance criteria

- [ ] AC1: Build-method-tagged or explicitly approved build requests enter FIFO state.
- [ ] AC2: Non-build requests are rejected/classified without active queue insertion.
- [ ] AC3: Accepted items record source text, actor, timestamp, scope, initial state, and eligibility rationale.

## BDD scenarios

### Happy path: build-method idea is queued

Given a human submits an idea associated with `/the-copilot-build-method`
When the overseer runs queue intake
Then a new queue item is created in `queued` state
And the item appears after existing queued items.

### Edge case: explicit overseer approval

Given an idea does not mention `/the-copilot-build-method`
And the overseer explicitly marks it as buildable product work
When queue intake runs
Then the item is accepted with an eligibility rationale.

### Error case: unrelated request

Given a request is a question, diagnostic, reminder, or unrelated command
When queue intake runs
Then the request is classified as non-build
And no active FIFO item is created.
