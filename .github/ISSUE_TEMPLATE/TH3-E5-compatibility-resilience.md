---
name: "TH3.E5 Compatibility and resilience"
about: "Migrate existing cockpits and prove the complete resilient control loop"
title: "TH3.E5: Compatibility, integration, and resilience validation"
labels: ["theme:TH3", "epic:E5", "copilotcockpit", "migration", "testing"]
assignees: ""
---

## Goal

Adopt VP3 non-destructively, align skills and generated guidance, and prove the
end-to-end restart, stall, replacement, evidence, and wake-termination flow.

## Architecture

- `docs/architecture/overseer-control-plane.md` sections 15-18
- ADR-012 through ADR-017

## Stories

- [ ] TH3.E5.US1 - Additive migration and legacy compatibility
- [ ] TH3.E5.US2 - Contract and fault-injection test coverage
- [ ] TH3.E5.US3 - End-to-end control-plane resilience proof

## Dependencies

Depends on the completed runtime capabilities from TH3.E1 through TH3.E4.

## Completion Gate

Existing project-owned data is preserved, all control-plane failure contracts
are tested, and the full resilience scenario passes the repository gate.
