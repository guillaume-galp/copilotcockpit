---
id: TH2.E2.US2
title: "E2E operator clearance gate"
type: standard
priority: high
size: M
agents: [developer]
skills: [e2e-operator, e2e-cockpit, bdd-stories]
acceptance-criteria:
  - AC1: "A queue item cannot transition to cleared until worker-test reports governed runbook evidence."
  - AC2: "E2E failures route through e2e-related-fixing before the item can clear."
  - AC3: "The clear-current report includes run ID, scope, result, failure summary, and waiver if applicable."
depends-on: [TH2.E2.US1]
---

As a human operator, I want queue items cleared only after governed E2E evidence
so that delivered ideas are not marked done prematurely.

## Acceptance criteria

- [ ] AC1: Clear-current requires worker-test runbook evidence.
- [ ] AC2: E2E failures enter e2e-related-fixing before clearance.
- [ ] AC3: Clearance reports include runbook evidence and waiver details.

## BDD scenarios

### Happy path: E2E green clears item

Given an item is delivered locally
And worker-test reports a green governed runbook
When the overseer clears the item
Then the item transitions to `cleared`
And the report includes run ID, scope, and result.

### Edge case: explicit waiver

Given worker-test reports a non-critical documented failure
And the human explicitly waives it
When the overseer clears the item
Then the item records the waiver and transitions to `cleared`.

### Error case: no runbook evidence

Given an item is delivered locally
But no worker-test runbook evidence exists
When the overseer runs `clear-current`
Then the command fails
And the item remains uncleared.
