---
name: TH4.E2 Control-store domain extraction
about: Extract root/schema, lock, journal, and projection seams behind the facade
labels: [epic, TH4, E2]
assignees: [copilot]
---

## Stories

- [ ] US1 — Control-root schemas, validation, and compatibility extraction: Move root/schema compatibility logic behind a stable seam.
- [ ] US2 — Lock ownership, acquire/release, and repair extraction: Isolate the portable lock protocol without changing ADR-018 behavior.
- [ ] US3 — Immutable event journal extraction: Isolate committed event reading and publication without changing ADR-019 behavior.
- [ ] US4 — Projection and replay extraction: Isolate deterministic ledger projection and derived-view replay.

Full stories: `docs/themes/TH4-control-plane-modularity-ontology/epics/E2-control-store-domain-extraction/stories/`
