# Release: Control-plane modularity and ontology adoption

## Summary

TH4 delivers behavior-preserving modularization of the accepted TH3 cockpit control plane. The stable `cockpit_control` facade, `cockpit-control`, `cockpit-overseer`, and `cockpit-wake` public contracts remain compatible while bounded-context seams now own control root/schema compatibility, locks, immutable journal publication, projection/replay, lifecycle/freshness, command acknowledgements, mission-control transitions, controller reconciliation, wake scheduling, and CLI/adapters.

This is a maintainability and ontology-alignment delivery, not a new user-facing runtime capability.

## Epics Delivered

- TH4.E1 — Foundation facade and typed contracts.
- TH4.E2 — Control-store domain extraction.
- TH4.E3 — Runtime orchestration domain extraction.
- TH4.E4 — CLI thinning and conformance cleanup.

## Breaking Changes

None. Existing imports, wrappers, CLI verbs/help/output/exit codes, schemas, event/ledger/lock bytes, dry-run no-mutation behavior, and fail-closed diagnostics are preserved.

## Migration Notes

No control-store data migration is required. Rollback remains facade-based: revert the extracted module wiring and exact `MANAGED_RUNTIME_MODULES` changes, then reinstall/update managed runtime artifacts so stale installed modules cannot shadow the facade.

## Release/Version Rationale

The intended squash commit type is `refactor:` because TH4 is behavior-preserving modularization. Under the repo-managed `copilotcockpit-dev` runbook, a pure `refactor:` squash merge has no semver bump and no GitHub release.

## Gate Evidence

- Story-level focused tests and independent reviews completed for all 14 TH4 stories.
- Epic gates: TH4.E1 full `./run-tests.sh all` unit `1..233`; TH4.E2 full `./run-tests.sh all` unit `1..233`; TH4.E3 full `./run-tests.sh all` unit `1..236`; TH4.E4 full `./run-tests.sh all` unit `1..238`.
- Final theme gate: `./run-tests.sh all` exit 0 with unit `1..238`, template, skills, integration, and codex checks passing.
- Final conformance: `tests/unit/check-th4-e4-us3-conformance.sh` passed with 51 Python conformance/seam tests and 68 lock/journal/projection/controller/wake/resilience invariants.
- Correct branch-local cold-install smoke passed from a locally built release artifact with source `PYTHONPATH` absent; `--from-release latest` was intentionally not used as branch readiness evidence because it resolves to published v0.9.0.
- Product-owner revalidation accepted VP4/TH4 after clarified `refactor:`/no-release runbook context; TH4 is done and locked in `docs/plan/backlog.yaml`.
