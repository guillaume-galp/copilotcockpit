# ADR-014: Deterministic Control-Plane Reconciliation

## Status

Accepted

## Context

Queue items, ledger projections, worker reports, live status, and pane text can
disagree after crashes, stale prompts, or duplicated dispatch. An overseer
cannot safely improvise which source to trust.

## Decision

Use this precedence:

1. explicit human decision for the addressed action;
2. terminal and product state owned by `cockpit-queue`;
3. durable command acknowledgements and lifecycle events;
4. replayed control journal;
5. materialized ledger projection;
6. validated worker reports matching mission, command, and trace;
7. live protocol observation;
8. raw pane output for diagnostics only.

`cockpit-overseer tick` reconciles new evidence, chooses at most one
state-changing action, persists it, and exits. Conflicts emit structured
reconciliation events. Stale observation triggers bounded recovery but does not
automatically mean mission failure.

## Consequences

### Positive

- Different models produce equivalent outcomes from the same evidence.
- Restart and partial-write behavior is testable.
- Human suspension and terminal queue state cannot be undone by a stale worker.

### Negative

- Some conflicts intentionally block progress instead of guessing.
- Reconciliation rules must evolve compatibly with schemas.

### Risks

- Overly broad automatic repair could hide corruption; authoritative journal
  repair remains explicit.

## Alternatives Considered

### Latest timestamp wins

Rejected because clocks and late observations do not establish ownership.

### Pane output wins

Rejected because prompts and presentation can be stale or misleading.
