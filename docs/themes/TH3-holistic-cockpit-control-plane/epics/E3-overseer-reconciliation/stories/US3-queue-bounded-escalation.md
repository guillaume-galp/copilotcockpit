---
id: TH3.E3.US3
title: "Queue-linked bounded escalation"
type: standard
priority: high
size: M
agents: [developer]
skills: [bdd-stories, e2e-cockpit]
acceptance-criteria:
  - AC1: "The active mission remains linked to its queue item and controller actions cannot silently reopen or clear terminal queue state."
  - AC2: "Consecutive blocked ticks persist blocker count and execute troubleshoot, escalation-record, then human-decision suspension actions."
  - AC3: "Escalation records contain evidence, impact, attempted recovery, options, and the pending human decision."
depends-on: [TH3.E3.US2]
---

As a product owner, I want queue-linked bounded escalation so that recurrent
oversight either changes state or stops for a human decision.

## Acceptance criteria

- [ ] AC1: Queue authority is preserved through mission recovery.
- [ ] AC2: Three blocked ticks execute the fixed escalation ladder.
- [ ] AC3: Human escalation is actionable and auditable.

## BDD scenarios

### Happy path: first blocked tick dispatches troubleshooting

Given the active mission made no progress since the prior tick
When the first blocked tick runs
Then one focused troubleshooting action is persisted.

### Edge case: queue item became terminal externally

Given a worker remains running after the queue item was cancelled or cleared
When reconciliation runs
Then cancellation is requested and the queue item is not reopened.

### Error case: blocker reaches the third tick

Given two prior blocked ticks did not resolve the mission
When the third blocked tick runs
Then a human decision is requested and recurrent action is suspended.
