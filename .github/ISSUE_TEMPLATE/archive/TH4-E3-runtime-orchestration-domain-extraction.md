---
name: TH4.E3 Runtime orchestration domain extraction
about: Extract lifecycle, command, mission, controller, and wake bounded contexts
labels: [epic, TH4, E3]
assignees: [copilot]
---

## Stories

- [ ] US1 — Worker lifecycle and freshness extraction: Isolate lifecycle vocabulary, transitions, heartbeat, freshness, and stale observations.
- [ ] US2 — Command envelopes and acknowledgements extraction: Isolate idempotent command envelopes, digests, acknowledgements, and conflicts.
- [ ] US3 — Mission command, question, and recovery transition extraction: Isolate questions, access prompts, replies, cancellations, replacements, and recovery commands.
- [ ] US4 — Controller evidence, decision, and reconciliation extraction: Isolate precedence, one-action ticks, bounded recovery, and controller reports.
- [ ] US5 — Wake lease and scheduling extraction: Isolate wake intent, leases, duplicate suppression, stop conditions, and scheduler adapters.

Full stories: `docs/themes/TH4-control-plane-modularity-ontology/epics/E3-runtime-orchestration-domain-extraction/stories/`
