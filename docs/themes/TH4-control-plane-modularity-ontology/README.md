# TH4: Control-Plane Modularity and Ontology Adoption

## Goal

Plan and execute the follow-up work from
[VP4](../../vision_of_product/VP4-control-plane-modularity-ontology/VP4.md):
incrementally extract bounded control-plane modules from
`bin/cockpit_control.py` while preserving the accepted TH3 behavior and adopting
the canonical vocabulary from the
[control-plane ontology](../../architecture/cockpit-control-plane-ontology.md)
and [ADR-020](../../ADRs/ADR-020-incremental-control-plane-module-extraction.md).

TH4 is intentionally non-functional. It improves maintainability, reviewability,
and dependency clarity without changing public commands, imports, schemas,
state files, or accepted orchestration mechanisms.

## Epics

| Epic | Capability | Depends on |
|---|---|---|
| TH4.E1 | Foundation facade and typed contracts | None |
| TH4.E2 | Control-store domain extraction | TH4.E1 |
| TH4.E3 | Runtime orchestration domain extraction | TH4.E2 |
| TH4.E4 | CLI thinning and conformance proof | TH4.E3 |

## Recommended execution order

1. Pin the public compatibility facade and dependency rules before moving logic.
2. Extract control-root/schema, lock, journal, and projection seams.
3. Extract lifecycle, command, mission-control, controller, and wake seams.
4. Thin CLI parsing/rendering/adapters and prove dependency, vocabulary, and
   integration conformance.

At every commit the `cockpit_control` facade remains usable, dry-runs remain
byte-inert, fail-closed diagnostics remain explicit, and rollback requires no
data migration.

## Architecture references

- [Cockpit Control-Plane Ontology](../../architecture/cockpit-control-plane-ontology.md)
- [VP3 control-plane architecture](../../architecture/overseer-control-plane.md)
- [ADR-020: Incremental Control-Plane Module Extraction](../../ADRs/ADR-020-incremental-control-plane-module-extraction.md)
- ADR-011 through ADR-019 for accepted mechanisms that TH4 must preserve.

## Definition of Done

- All TH4 stories are done and reviewed.
- `cockpit_control` import compatibility and public CLI behavior are preserved.
- Extracted modules follow the ADR-020 dependency direction with no cycles.
- Control-store schemas, event publication, ledger replay, locks, lifecycle,
  commands, mission control, controller ticks, wake leases, and CLI rendering
  have focused behavior-preservation coverage at changed seams.
- Full repository gate passes at final theme boundary.
- Vocabulary touched by TH4 matches the ontology or documents compatibility
  aliases.
