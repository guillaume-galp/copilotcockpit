---
id: TH3.E1.US3
title: "Recoverable guarded stale-lock repair"
type: standard
priority: high
size: M
agents: [developer]
skills: [bdd-stories]
acceptance-criteria:
  - AC1: "Repair holds the same transition guard as acquisition and proceeds only after positively proving same-host owner death or receiving explicit guarded authorization."
  - AC2: "Repair atomically renames the exact stale lock to a unique quarantine without publishing or deleting a shared repair marker."
  - AC3: "Interruption before repair rename leaves the lock unchanged, while interruption after rename leaves recoverable non-authoritative evidence and permits new acquisition."
depends-on: [TH3.E1.US2]
---

As an operator, I want stale-lock repair to be atomic and resumable so that
recovery cannot archive a live replacement or permanently claim the store.

## Acceptance criteria

- [ ] AC1: Acquisition and repair cannot interleave ownership transitions.
- [ ] AC2: The quarantine rename is the complete repair claim.
- [ ] AC3: Every repair interruption boundary has a recoverable state.

## BDD scenarios

### Happy path: dead same-host owner is quarantined

Given a stale lock whose same-host owner is positively proven dead
When guarded repair validates and renames the exact lock
Then the stale lock is preserved in a unique quarantine and acquisition may resume.

### Edge case: repair crashes after quarantine rename

Given repair atomically moved the stale lock out of the authoritative path
When the process exits before cleanup
Then the quarantine remains as evidence and a new writer can acquire.

### Error case: owner is live or ambiguous

Given the owner is alive, remote, malformed, or cannot be proven dead
When automatic repair runs
Then the authoritative lock remains unchanged and repair fails closed.
