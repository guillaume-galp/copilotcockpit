# ADR-018: Portable Lock Acquisition and Recoverable Repair

## Status

Accepted

## Context

TH3.E1.US2 implementation reviews exposed unsafe transition windows in a plain
`mkdir control.lock` design:

- the lock path could exist before owner metadata was published;
- a repair process could validate one lock and then rename or unlink its
  replacement;
- interruption while publishing a repair marker could permanently wedge the
  store;
- timeout and error paths could leak writer-owned lock artefacts.

Pathname identity checks alone cannot provide an atomic compare-and-remove
operation on portable Linux/macOS filesystems.

## Decision

Serialize lock ownership transitions with a short-lived advisory exclusive
`fcntl.flock` on `locks/control.guard`. The kernel releases this guard when the
process exits.

Prepare a complete owner directory under a unique private candidate path, flush
its `owner.json`, and atomically rename the directory to `control.lock` while
holding the guard. A published lock therefore always has complete owner
metadata.

Release and stale repair also hold the guard. They validate owner UUID,
same-host process evidence, and filesystem identity, then atomically rename the
entire lock directory to a unique quarantine path before cleanup. Acquisition
cannot replace the lock between validation and rename because it requires the
same guard.

Do not use a shared repair marker inside the authoritative lock. The quarantine
rename is the repair claim:

- interruption before rename leaves the original lock unchanged;
- interruption after rename leaves non-authoritative evidence and frees the
  authoritative path;
- cleanup never performs pathname-only deletion of a shared live marker.

Private candidates and quarantines are non-authoritative. Their creator may
remove them; doctor may report or explicitly repair stale debris. Acquisition
timeout must remove only the caller's private candidate and must release any
published lock only when its exact owner UUID matches.

## Consequences

### Positive

- Removes the owner-publication and repair cleanup TOCTOU windows.
- Repair claims are complete-or-absent at the authoritative path.
- Process death cannot permanently hold the transition guard.
- The design uses Python standard-library facilities available on Linux/macOS.

### Negative

- `fcntl.flock` narrows the supported control root to local filesystems with
  working advisory locks.
- Lock transition code must retain file descriptors correctly.
- Windows is not supported by this implementation.

### Risks

- Advisory locking is ineffective if a mutating tool bypasses the protocol.
  All control-plane writers must use the shared lock implementation.
- Some network filesystems have weak or inconsistent `flock` semantics and
  remain unsupported for canonical control state.

## Alternatives Considered

### `mkdir` plus an acquisition-intent marker

Rejected because the intent marker introduces its own publication, replacement,
cleanup, and crash-recovery races.

### Repair marker inside `control.lock`

Rejected because interruption can strand a claim and pathname cleanup can
delete a replacement marker.

### Path identity check immediately before `rename` or `unlink`

Rejected because another process can replace the pathname after the check.

### Permanent controller daemon

Rejected because a short kernel-managed guard provides the required transition
serialization without another service.
