# ADR-011: Cockpit Control-Plane Ownership

## Status

Accepted

## Context

The existing queue, protocol, overseer, wake, and trace tools each solve part of
cockpit orchestration. VP3 requires them to behave as one control plane without
introducing a daemon or allowing multiple tools to claim authority over the
same state.

## Decision

Assign ownership by concern:

- `cockpit-queue` owns FIFO product-work state.
- the control event journal owns mission runtime history;
- `cockpit-overseer` owns reconciliation and the materialized mission ledger;
- `cockpit-protocol` owns worker command delivery, acknowledgements, lifecycle,
  and question exchange;
- `cockpit-wake` owns schedules but only triggers controller ticks;
- `cockpit-trace` owns no canonical state and renders derived evidence;
- doctor/preflight validates configuration and repairs only derived state unless
  an explicit guarded repair is requested.

All components communicate through versioned command, event, and status
interfaces. Pane text is diagnostic-only.

## Consequences

### Positive

- Every entity has one owner.
- Existing tools evolve without a platform rewrite.
- A fresh LLM can reconstruct state through stable interfaces.

### Negative

- Cross-tool contracts require compatibility tests.
- Some current pane-derived behavior must be migrated.

### Risks

- An implementation that bypasses ownership boundaries could recreate split
  authority; reviews must reject direct state mutation by non-owners.

## Alternatives Considered

### Make `cockpit-overseer` own queue state

Rejected because ADR-010 already gives the queue an independent durable product
state machine.

### Introduce a workflow service

Rejected as operationally disproportionate to a local tmux cockpit.
