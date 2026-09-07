---
id: TH3.E1.US2
title: "Portable lock acquisition and exact release"
type: standard
priority: high
size: M
agents: [developer]
skills: [bdd-stories]
acceptance-criteria:
  - AC1: "Acquisition, release, and later repair transitions are serialized by a bounded process-scoped guard that is released automatically on process death."
  - AC2: "A complete private owner directory is flushed and atomically published as control.lock, so an authoritative owner-less lock is impossible."
  - AC3: "Release, timeout, and error unwind remove only the exact caller-owned candidate or lock and preserve any replacement owner."
depends-on: [TH3.E1.US1]
---

As an overseer, I want portable lock acquisition and exact release so that a
writer either owns one completely identified lock or leaves no authoritative
lock behind.

## Acceptance criteria

- [ ] AC1: A kernel-released transition guard serializes ownership changes.
- [ ] AC2: Lock publication is complete and atomic.
- [ ] AC3: Unwind and release preserve replacement ownership.

## BDD scenarios

### Happy path: prepared owner becomes authoritative

Given a complete private owner candidate and no authoritative lock
When acquisition publishes the candidate under the transition guard
Then `control.lock` atomically appears with complete matching owner metadata.

### Edge case: two writers contend

Given one process holds the control lock
When a second writer reaches its bounded wait
Then it removes only its private candidate and leaves the owner's lock intact.

### Error case: release sees a replacement owner

Given the caller's original lock path was replaced by another owner
When the caller attempts release
Then release fails closed and does not rename or delete the replacement.
