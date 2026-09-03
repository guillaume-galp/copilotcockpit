---
name: "TH3.E1 Control store and preflight"
about: "Build the durable VP3 control state and root validation foundations"
title: "TH3.E1: Control store and preflight foundations"
labels: ["theme:TH3", "epic:E1", "copilotcockpit", "control-plane"]
assignees: ""
---

## Goal

Implement the explicit control root, schema-versioned event journal, portable
lock ownership and repair, immutable events, materialized ledger replay, and
preflight diagnostics defined by ADR-012, ADR-018, and ADR-019.

## Architecture

- `docs/architecture/overseer-control-plane.md` sections 7, 8, 14, and 17
- ADR-011, ADR-012, ADR-017, ADR-018, ADR-019

## Stories

- [ ] TH3.E1.US1 - Control root and versioned schemas
- [ ] TH3.E1.US2 - Portable lock acquisition and exact release
- [ ] TH3.E1.US3 - Recoverable guarded stale-lock repair
- [ ] TH3.E1.US4 - Immutable authoritative event publication
- [ ] TH3.E1.US5 - Materialized ledger projection and replay
- [ ] TH3.E1.US6 - Deterministic crash-consistency and interleaving proof
- [ ] TH3.E1.US7 - Control-plane preflight and guarded repair diagnostics

## Dependencies

None. This epic gates the runtime-control epics.

## Completion Gate

Control state is explicit, atomically owned and published, replayable,
schema-validated, crash-tested, and diagnosed without inferring roots from the
working directory.
