# ADR-016: Evidence Correlation and Declared Mission Boundaries

## Status

Accepted

## Context

Reviews and escalations need to connect human intent, queue state, worker
commands, reports, tests, and repository changes. Missions also need explicit
access and runtime boundaries so workers do not silently cross architecture or
deployment scope.

## Decision

Use one root trace per product mission and child traces for commands and focused
worker dialogs. Control events carry queue item, mission, command, trace, worker,
test, review, and human-decision references as applicable.

Every mission declares planning, queue, control, and implementation roots plus
runtime/image/CI/IAM/deployment boundaries. Preflight validates paths and
capabilities. Access outside declared roots or crossing a protected runtime
boundary blocks the mission pending explicit re-scope and emits an architecture
boundary event.

`cockpit-trace` remains a read-only derived renderer. Canonical control events
store structured references rather than secrets, full prompts, or source bodies.

## Consequences

### Positive

- Delivery and escalation evidence is causally reconstructable.
- Multi-repository work is explicit before implementation expands.
- Trace tooling can be rebuilt without losing authority.

### Negative

- Missions need a small boundary declaration.
- Evidence producers must propagate correlation IDs.

### Risks

- Storing verbose payloads could leak secrets; canonical events are deliberately
  metadata-only.

## Alternatives Considered

### Correlate by timestamps and pane sessions

Rejected because concurrent work makes the relationship ambiguous.

### Allow workers to discover arbitrary roots

Rejected because it recreates access prompts and uncontrolled scope expansion.
