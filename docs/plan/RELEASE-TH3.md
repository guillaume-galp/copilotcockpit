# Release: Holistic cockpit control plane

## Summary

TH3 delivers the VP3 control plane for resilient, auditable cockpit operation. It adds a versioned control root, immutable event publication, replayable projections, structured worker lifecycle and command protocols, deterministic overseer reconciliation, bounded stale-worker recovery, intent-aware wake execution, evidence-boundary enforcement, additive migration, and deterministic resilience tests.

## Epics Delivered

- **TH3.E1 — Control store and preflight foundations:** versioned schemas, portable lock acquisition/release, guarded stale-lock repair, immutable event publication, ledger projection/replay, crash-consistency coverage, and preflight diagnostics.
- **TH3.E2 — Worker lifecycle and idempotent mission protocol:** structured lifecycle/freshness, idempotent command envelopes and acknowledgements, managed questions, cancellation, and replacement.
- **TH3.E3 — Overseer reconciliation and bounded recovery:** one-action controller ticks, evidence precedence, stale-worker recovery, conflict reconciliation, queue-linked bounded escalation, and human-decision suspension.
- **TH3.E4 — Intent-aware wakes, evidence, and boundaries:** mission-aware wake metadata, controller-tick wake jobs, durable wake leases, duplicate suppression, stale lease recovery, trace/evidence correlation, and declared-boundary fail-closure.
- **TH3.E5 — Compatibility, integration, and resilience validation:** additive migration and doctor guidance, contract/fault coverage through repository categories, deterministic integration resilience scenarios, and generated wake-script injection hardening.

## Breaking Changes

- VP3 managed wake jobs require mission/owner metadata for controller-backed execution; legacy wakes without required metadata are blocked with migration guidance instead of silently pasting prompts.
- Generated wake jobs now route through the managed controller and wake lease wrapper, so overlapping schedules produce duplicate-skip evidence rather than concurrent dispatch.

## Migration Notes

- Run `./bootstrap.sh doctor` to inspect prerequisites, installed-tool drift, legacy control-plane capability, and migration guidance.
- Use `cockpit-wake migrate` with `COCKPIT_CONTROL_ROOT` set to add VP3 control state non-destructively. Migration is idempotent, backs up malformed metadata before refusing unsafe rewrite, and refuses unknown future schema versions without mutation.
- Existing queues, traces, wake schedules, project-owned launchers, and overlays are preserved during additive adoption.
- Operators should run a non-dry-run global install after release if local `doctor` reports drifted installed skills or cockpit tools.
