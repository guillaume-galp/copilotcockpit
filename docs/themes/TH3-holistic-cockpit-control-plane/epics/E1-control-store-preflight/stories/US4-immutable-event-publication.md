---
id: TH3.E1.US4
title: "Immutable authoritative event publication"
type: standard
priority: high
size: M
agents: [developer]
skills: [bdd-stories]
acceptance-criteria:
  - AC1: "Each event is fully written, flushed, and validated under a private pending path before one atomic rename commits it into events/."
  - AC2: "Committed filenames, revisions, event IDs, and schemas agree and form one contiguous unique sequence."
  - AC3: "Partial pending files, duplicate revisions, gaps, or invalid committed events never become silent authority and block unsafe mutation."
depends-on: [TH3.E1.US2]
---

As an overseer, I want immutable event publication so that process interruption
cannot expose an ambiguous torn journal record.

## Acceptance criteria

- [ ] AC1: Event commit is one atomic pending-to-committed rename.
- [ ] AC2: Committed revision identity is deterministic.
- [ ] AC3: Partial or inconsistent event state fails closed.

## BDD scenarios

### Happy path: event commit advances one revision

Given the control lock and the latest contiguous revision
When a valid private event is flushed and renamed into `events/`
Then exactly one immutable committed event becomes authoritative.

### Edge case: process exits during private publication

Given a writer created only part of a pending event
When replay or another writer starts
Then the pending file is reported as debris and is not treated as committed.

### Error case: committed revision gap exists

Given revisions 1 and 3 exist without revision 2
When a mutation or replay validates committed events
Then it fails closed and identifies the missing revision.
