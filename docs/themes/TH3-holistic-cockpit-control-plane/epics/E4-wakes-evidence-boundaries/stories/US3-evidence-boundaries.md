---
id: TH3.E4.US3
title: "Evidence correlation and declared mission boundaries"
type: standard
priority: medium
size: M
agents: [developer]
skills: [bdd-stories, e2e-cockpit]
acceptance-criteria:
  - AC1: "Queue, mission, command, trace, report, test, review, and human-decision references can be reconstructed as one causal mission tree."
  - AC2: "Every mission declares planning, queue, control, implementation, and protected runtime boundaries before dispatch."
  - AC3: "Undeclared path access or repository, image, CI/CD, IAM, or deployment boundary crossing blocks work pending explicit re-scope."
depends-on: [TH3.E2.US2, TH3.E3.US1]
---

As a reviewer, I want correlated evidence and declared boundaries so that I can
audit delivery and detect architectural scope expansion before it spreads.

## Acceptance criteria

- [ ] AC1: Evidence renders as one correlated mission tree.
- [ ] AC2: Mission roots and runtime boundaries are explicit.
- [ ] AC3: Undeclared boundary crossing blocks safely.

## BDD scenarios

### Happy path: completed mission evidence is reconstructed

Given a queue item with worker, review, and test evidence
When the trace tree is requested
Then all matching child events appear under the root mission trace.

### Edge case: one mission declares multiple repositories

Given all repository roots were declared and approved before dispatch
When workers operate within those roots
Then evidence remains correlated without an escalation.

### Error case: worker needs an undeclared deployment image

Given the mission omitted image changes
When the worker reports that boundary requirement
Then implementation blocks and an architecture-boundary event is emitted.
