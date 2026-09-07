---
name: "TH3.E4 Wakes, evidence, and boundaries"
about: "Make recurrent wakes safe and correlate mission evidence and scope"
title: "TH3.E4: Intent-aware wakes, evidence, and boundaries"
labels: ["theme:TH3", "epic:E4", "copilotcockpit", "wake", "trace"]
assignees: ""
---

## Goal

Bind recurrent wakes to durable mission intent, suppress duplicate ticks,
correlate evidence, and enforce declared repository/runtime boundaries.

## Architecture

- `docs/architecture/overseer-control-plane.md` sections 12-14
- ADR-015, ADR-016

## Stories

- [ ] TH3.E4.US1 - Mission-aware wake scheduling and termination
- [ ] TH3.E4.US2 - Wake leases and duplicate suppression
- [ ] TH3.E4.US3 - Evidence correlation and declared boundaries

## Dependencies

Depends on control-store foundations, command identity, and controller ticks.

## Completion Gate

Wakes are restart-safe and self-terminating, evidence is causally linked, and
undeclared scope expansion blocks before worker execution.
