# TH4.E2: Control-Store Domain Extraction

## Goal

Extract the foundational control-store bounded contexts behind the stable facade:
control-root schemas and compatibility, lock ownership, immutable journal
publication, and ledger projection/replay.

## Stories

- TH4.E2.US1 — Control-root schemas, validation, and compatibility extraction
- TH4.E2.US2 — Lock ownership, acquire/release, and repair extraction
- TH4.E2.US3 — Immutable event journal extraction
- TH4.E2.US4 — Projection and replay extraction

## Completion gate

Schemas, locks, journal, and projections live behind explicit seams while public
imports, command output, event/ledger bytes, dry-run behavior, and fail-closed
repair diagnostics remain compatible.
