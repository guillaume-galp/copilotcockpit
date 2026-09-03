---
name: "TH3.E1 Control store and preflight"
about: "Build the durable VP3 control state and root validation foundations"
title: "TH3.E1: Control store and preflight foundations"
labels: ["theme:TH3", "epic:E1", "copilotcockpit", "control-plane"]
assignees: ""
---

## Goal

Implement the explicit control root, schema-versioned event journal, portable
atomic locking, materialized ledger replay, and preflight diagnostics defined by
ADR-012.

## Architecture

- `docs/architecture/overseer-control-plane.md` sections 7, 8, 14, and 17
- ADR-011, ADR-012, ADR-017

## Stories

- [ ] TH3.E1.US1 - Control root and versioned schemas
- [ ] TH3.E1.US2 - Atomic journal writes and portable locking
- [ ] TH3.E1.US3 - Ledger replay and control-plane preflight

## Dependencies

None. This epic gates the runtime-control epics.

## Completion Gate

Control state is explicit, atomic, replayable, schema-validated, and diagnosed
without inferring roots from the working directory.
