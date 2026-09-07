# TH4.E3: Runtime Orchestration Domain Extraction

## Goal

Extract the runtime orchestration bounded contexts after the control-store seams
are stable: worker lifecycle, command acknowledgements, mission transitions,
controller reconciliation, and wake leases/scheduling.

## Stories

- TH4.E3.US1 — Worker lifecycle and freshness extraction
- TH4.E3.US2 — Command envelopes and acknowledgements extraction
- TH4.E3.US3 — Mission command, question, and recovery transition extraction
- TH4.E3.US4 — Controller evidence, decision, and reconciliation extraction
- TH4.E3.US5 — Wake lease and scheduling extraction

## Completion gate

Runtime orchestration logic is modularized by bounded context, controller ticks
remain one-action and fail-closed, wake duplicate suppression remains safe, and
all changed seams have focused behavior-preservation coverage.
