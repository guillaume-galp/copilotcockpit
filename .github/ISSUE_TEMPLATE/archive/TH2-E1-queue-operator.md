---
name: "TH2.E1 Queue operator"
about: "Deliver the cockpit FIFO queue intake, persistence, and CLI operations"
title: "TH2.E1: Queue operator"
labels: ["theme:TH2", "epic:E1", "copilotcockpit", "queue"]
assignees: ""
---

## Epic

TH2.E1 — Queue operator

## Goal

Build the cockpit-owned FIFO queue operator so the overseer can accept build
ideas, reject non-build requests, inspect queue state, and persist transitions
without loading the entire backlog.

## Stories

- [ ] TH2.E1.US1 — Queue intake and classification
- [ ] TH2.E1.US2 — Queue persistence and CLI operations

## Acceptance criteria

- Queue intake accepts only build-method-tagged or explicitly approved build
  work.
- Queue items persist as `docs/queue/items/<id>.yaml`.
- Queue transitions append to `docs/queue/events.jsonl`.
- Invalid transitions fail without mutating state.

## Verification

- CLI/unit tests cover intake, rejection, FIFO ordering, state persistence, and
  transition logging.
