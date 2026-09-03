# TH3: Holistic Cockpit Control Plane

## Goal

Deliver the restart-safe, model-agnostic control plane specified by
[VP3](../../vision_of_product/VP3-overseer-orchestration-resilience/VP3.md) and
the [control-plane architecture](../../architecture/overseer-control-plane.md).
The cockpit must preserve intent and mission state outside LLM context, operate
workers through managed protocols, reconcile contradictory evidence
deterministically, and terminate recurrent wakes within bounded limits.

## Epics

| Epic | Capability | Depends on |
|---|---|---|
| TH3.E1 | Control store and preflight foundations | None |
| TH3.E2 | Worker lifecycle and idempotent mission protocol | TH3.E1 |
| TH3.E3 | Overseer reconciliation and bounded recovery | TH3.E1, TH3.E2 |
| TH3.E4 | Intent-aware wakes, evidence, and boundaries | TH3.E1, selected TH3.E2/TH3.E3 stories |
| TH3.E5 | Compatibility, integration, and resilience validation | TH3.E1-TH3.E4 |

## Recommended Execution Order

1. Establish the control root, event journal, locking, ledger replay, and
   preflight.
2. Add structured worker lifecycle and idempotent commands.
3. Implement one-action controller ticks, reconciliation, and bounded recovery.
4. Make wakes mission-aware and connect traces and declared boundaries.
5. Migrate legacy surfaces and prove the complete restart/replacement flow.

Safe parallelism is encoded in `docs/plan/backlog.yaml`; workers must still
execute one story per session.

## Architecture Decisions

- [ADR-011](../../ADRs/ADR-011-control-plane-ownership.md)
- [ADR-012](../../ADRs/ADR-012-control-store-persistence.md)
- [ADR-013](../../ADRs/ADR-013-worker-lifecycle-command-protocol.md)
- [ADR-014](../../ADRs/ADR-014-deterministic-reconciliation.md)
- [ADR-015](../../ADRs/ADR-015-intent-aware-wake-leases.md)
- [ADR-016](../../ADRs/ADR-016-trace-and-boundary-correlation.md)
- [ADR-017](../../ADRs/ADR-017-control-plane-compatibility.md)

## Definition of Done

- All stories are done and individually reviewed.
- Control state survives overseer restart and projection rebuild.
- Worker commands are acknowledged and idempotent.
- Reconciliation follows the documented precedence without pane-derived state
  transitions.
- Recurrent wakes suppress duplicates, escalate within three blocked ticks, and
  terminate on completion or suspension.
- Existing queue, trace, wake, launcher, and overlay data migrate
  non-destructively.
- Contract, fault-injection, portability, and end-to-end resilience tests pass.
- Full repository test gate and release-readiness checks pass.
