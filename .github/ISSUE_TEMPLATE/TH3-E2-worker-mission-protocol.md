---
name: "TH3.E2 Worker mission protocol"
about: "Add lifecycle events and idempotent managed worker commands"
title: "TH3.E2: Worker lifecycle and idempotent mission protocol"
labels: ["theme:TH3", "epic:E2", "copilotcockpit", "protocol"]
assignees: ""
---

## Goal

Replace pane-derived worker state with structured lifecycle events and durable,
acknowledged mission commands.

## Architecture

- `docs/architecture/overseer-control-plane.md` sections 9 and 10
- ADR-011, ADR-013, ADR-016

## Stories

- [ ] TH3.E2.US1 - Structured worker lifecycle and freshness
- [ ] TH3.E2.US2 - Idempotent command envelopes and acknowledgements
- [ ] TH3.E2.US3 - Managed questions, cancellation, and replacement

## Dependencies

Depends on TH3.E1 control-store foundations.

## Completion Gate

Every active worker mission has structured state and every state-changing
command is safely retryable and auditable.
