# ADR-019: Immutable Event Publication and Projection Replay

## Status

Accepted

## Context

ADR-012 selected an append-only event history and replayable ledger. During
TH3.E1.US2 review, a single JSONL journal was found to have an unspecified torn
write boundary: interruption can expose a malformed final line without a clear
commit marker, quarantine rule, or safe truncation authority.

The ledger must remain derived state and recovery must distinguish committed
events from private partial publication.

## Decision

Persist each authoritative event as one immutable JSON file:

`events/<zero-padded-revision>-<event-id>.json`.

While holding the control lock, a writer creates a unique private file under
`pending/`, flushes and validates it, then atomically renames it into `events/`.
Only the rename commits the event. Where supported, the writer also flushes the
containing directory.

Committed event revisions must be contiguous and unique, and each file's name,
declared revision, event ID, and schema must agree. Gaps, duplicates, or invalid
committed events fail closed.

After committing an event, the writer builds `ledger.json.tmp` from committed
events and atomically replaces `ledger.json`. If interruption occurs after event
commit but before projection replacement, replay applies the event exactly once.
Private pending files and interrupted ledger temporary files are
non-authoritative and require diagnosis or explicit cleanup.

`events.jsonl` may exist only as a rebuildable compatibility or inspection view.
It is never used to allocate revisions or recover canonical state.

## Consequences

### Positive

- Commit state is unambiguous across partial writes.
- Replay can identify the exact first invalid or missing revision.
- Event publication and ledger projection have separate, testable crash
  boundaries.
- No checksum or truncation heuristic is needed to recognize a committed event.

### Negative

- A large mission creates many small files.
- Listing and sorting event filenames replaces sequential JSONL reading.
- Compatibility tooling may need to rebuild a JSONL view.

### Risks

- Directory durability after rename varies by filesystem; the supported default
  is a local filesystem and directory flush is used where available.
- Manual deletion of a committed event creates a revision gap and blocks
  mutation until explicit repair.

## Alternatives Considered

### Length-prefixed or checksummed JSONL frames

Rejected for the MVP because guarded truncation and frame recovery remain more
complex than atomic per-event publication.

### SQLite transaction log

Rejected to preserve the lightweight, Git-reviewable, dependency-free control
store.

### Treat a malformed final JSONL line as disposable

Rejected because the system cannot prove whether the record was uncommitted or
manually corrupted.
