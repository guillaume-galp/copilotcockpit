---
id: TH4.E2.US3
title: "Immutable event journal extraction"
type: standard
priority: high
size: M
agents: [developer]
skills: [bdd-stories]
acceptance-criteria:
  - AC1: "Committed event naming, event validation, sequence reading, pending debris handling, and publication move behind an immutable journal seam while facade symbols and commands remain compatible."
  - AC2: "Event filenames, JSON bytes, revision allocation, events directory layout, compatibility view behavior, public output, and schema/state compatibility are unchanged."
  - AC3: "Dry-run publication stays non-mutating and malformed, duplicate, gap, or future-schema authoritative data fails closed; ADR-019 durability invariants crossed by the extraction keep existing R3/fault coverage."
  - AC4: "Focused tests cover publish-event, committed sequence reading, pending debris, duplicate/gap refusal, interruption boundaries, import compatibility, and unchanged CLI reports."
  - AC5: "Rollback restores prior facade publication without data migration, and touched terminology uses immutable journal, committed event, revision, authority, and derived view."
depends-on: [TH4.E2.US2]
---

As a maintainer, I want event journal behavior isolated so that publication and
history reading remain durable and comprehensible.

## Acceptance criteria

- [ ] AC1: Journal publication and reading are extracted behind the facade.
- [ ] AC2: Event names, bytes, layouts, output, and schemas are unchanged.
- [ ] AC3: Dry-run and fail-closed journal behavior remain intact.
- [ ] AC4: Focused journal/interruption/import tests prove equivalence.
- [ ] AC5: Rollback and journal vocabulary are explicit.

## Compatibility guard

Every implementation session for this story must preserve public
imports/commands, schema/state compatibility, dry-run/fail-closed contracts,
Focused tests named above, and rollback through the `cockpit_control` facade.

## BDD scenarios

### Happy path: one event is published through the journal seam

Given a valid control root and acquired lock
When an existing publish command records an event through the extracted seam
Then the committed event filename, revision, JSON payload, and output match the prior contract.

### Edge case: pending debris exists

Given non-authoritative files remain under `pending/`
When committed history is read through the extracted seam
Then pending debris is diagnosed as before and committed authority is unchanged.

### Error case: committed revisions contain a gap

Given the `events/` directory has a revision gap or duplicate
When a mutation is attempted
Then mutation fails closed with the existing repair guidance and no new event is committed.
