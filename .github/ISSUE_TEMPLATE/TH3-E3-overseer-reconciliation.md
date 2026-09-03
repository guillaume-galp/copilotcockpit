---
name: "TH3.E3 Overseer reconciliation"
about: "Implement deterministic controller ticks and bounded recovery"
title: "TH3.E3: Overseer reconciliation and bounded recovery"
labels: ["theme:TH3", "epic:E3", "copilotcockpit", "overseer"]
assignees: ""
---

## Goal

Make `cockpit-overseer` reconcile durable evidence, take one valid action per
tick, recover stale missions, and escalate within finite limits.

## Architecture

- `docs/architecture/overseer-control-plane.md` sections 6 and 11
- ADR-011, ADR-014

## Stories

- [ ] TH3.E3.US1 - One-action controller tick and precedence
- [ ] TH3.E3.US2 - Stale-worker recovery and conflict reconciliation
- [ ] TH3.E3.US3 - Queue-linked bounded escalation

## Dependencies

Depends on TH3.E1 and TH3.E2.

## Completion Gate

The controller reconstructs state without chat history, resolves documented
conflicts deterministically, and never loops indefinitely on a blocker.
