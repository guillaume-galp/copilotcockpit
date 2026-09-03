---
name: "TH2.E2 Cockpit E2E operations"
about: "Deliver overseer FIFO worker loop and queue-scoped E2E clearance"
title: "TH2.E2: Cockpit E2E operations"
labels: ["theme:TH2", "epic:E2", "copilotcockpit", "e2e"]
assignees: ""
---

## Epic

TH2.E2 — Cockpit E2E operations

## Goal

Update cockpit runbooks and skills so the overseer processes one active queue
item at a time and clears items only after queue-scoped governed E2E evidence or
an explicit waiver.

## Stories

- [ ] TH2.E2.US1 — Overseer FIFO worker loop
- [ ] TH2.E2.US2 — E2E operator clearance gate

## Acceptance criteria

- The overseer starts only the oldest eligible queued item when no active item
  exists.
- Busy workers do not receive a second mission.
- `clear-current` fails without worker-test runbook evidence or waiver.
- Failed E2E runs route through `e2e-related-fixing`.

## Verification

- Skill/runbook tests or fixtures demonstrate worker-busy handling, item state
  reporting, E2E evidence capture, and clearance refusal without evidence.
