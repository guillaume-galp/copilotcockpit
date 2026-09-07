# ADR-012: Versioned File-Backed Control Store

## Status

Accepted

## Context

The overseer must recover after context loss and concurrent wake invocations.
Current helper state is split across user configuration, `/tmp`, pane output,
and queue files. VP3 needs durable local state without adding a database server.

## Decision

Use an explicit absolute `COCKPIT_CONTROL_ROOT` containing:

- versioned root metadata;
- immutable committed control event records;
- a replayable JSON ledger projection;
- per-command JSON envelopes and acknowledgements;
- escalation records;
- a portable lock with owner metadata.

Committed event records are authoritative for mission runtime history. The
ledger is a materialized projection with a monotonic revision. Writers share one
bounded lock, atomically publish an event, then atomically replace the ledger
through a sibling temporary file.

Malformed authoritative event data fails closed. A corrupt ledger is rebuilt by
replay. Schema migration creates a backup, and unknown future versions block
mutation.

ADR-018 refines the lock acquisition, release, and repair protocol. ADR-019
refines event publication and makes any JSONL representation a derived
compatibility view rather than canonical authority.

## Consequences

### Positive

- Restart recovery is deterministic and reviewable.
- No new runtime service or package is required.
- Atomic replacement and a shared lock work on Linux and macOS.

### Negative

- File-backed event queries are less flexible than database queries.
- Lock contention serializes state-changing operations.

### Risks

- Network filesystems may provide weaker atomicity guarantees; the supported
  default is a local filesystem.
- Manual journal edits can stop the controller until repaired.

## Alternatives Considered

### SQLite

Rejected for the MVP because it complicates review, portability, and migration
without a demonstrated query requirement.

### Ledger-only JSON

Rejected because crash recovery and causal audit would depend on the last
successful snapshot.
