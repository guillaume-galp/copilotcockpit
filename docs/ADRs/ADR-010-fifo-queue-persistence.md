# ADR-010: FIFO Queue Persistence

## Status

Accepted

## Context

The cockpit overseer needs a durable request queue for build-method-tagged ideas.
The queue must support FIFO processing, explicit rejection of non-build work,
human review, low token use, low CPU/memory overhead, and traceability of every
state transition. The human operator explicitly approved the queue directory
approach during VP2 kickstart.

## Decision

Use a repository-managed queue directory:

- `docs/queue/items/<queue-id>.yaml` is the canonical state for each queue item.
- `docs/queue/events.jsonl` is the append-only transition log.
- The queue operator reads only the current item plus a compact index/list view
  for normal operation.
- Every transition appends one JSONL event with timestamp, actor, previous state,
  next state, reason, and optional cockpit trace / E2E run references.

The initial queue item state machine is:

`queued -> shaping -> planned -> implementing -> testing -> fixing -> delivered -> e2e-testing-runbooks -> e2e-related-fixing -> cleared`

with `blocked` and `rejected` as escape states.

## Consequences

### Positive

- Token-efficient: an overseer can read one item file without loading the whole
  queue.
- Git-traceable: item diffs and event history are reviewable in pull requests.
- Low overhead: no daemon or database server is required.
- Conflict-aware: multi-file state reduces contention compared with one large
  queue YAML file.

### Negative

- Queue queries are less powerful than a database unless a future index/cache is
  added.
- JSONL append discipline must be enforced by the queue operator.
- Concurrent writers need a simple lock or atomic-write protocol.

### Risks

- Manual edits can desynchronize item state and events unless validation exists.
- Large event logs may eventually need rotation or monthly partitioning.

## Alternatives Considered

### Single YAML file

Rejected for MVP. It is simple and readable, but grows quickly, increases token
cost, and creates merge conflicts.

### SQLite database

Rejected for canonical state. It offers efficient queries, but is harder to diff,
review, and merge in Git.

### Hybrid YAML/JSONL plus generated SQLite cache

Deferred. It may be useful once queue queries become complex, but it adds moving
parts before the queue semantics are proven.
