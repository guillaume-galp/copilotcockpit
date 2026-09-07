---
id: TH4.E3.US4
title: "Controller evidence, decision, and reconciliation extraction"
type: standard
priority: high
size: M
agents: [developer]
skills: [bdd-stories]
acceptance-criteria:
  - AC1: "Evidence snapshots, precedence evaluation, one-action action selection, recovery ladder selection, controller observations, dispatch builders, and tick reporting inputs move behind a controller seam while facade symbols and commands remain compatible."
  - AC2: "Controller decisions, event payloads, output lines, exit codes, dry-run behavior, schema/state compatibility, and queue/protocol adapter contracts are unchanged."
  - AC3: "Conflicting evidence, undeclared boundaries, corrupt authority, missing roots, and ambiguous recovery still fail closed or escalate according to ADR-014 without speculative redesign."
  - AC4: "Focused tests cover precedence table cases, one-action ticks, no-action ticks, stale recovery rungs, queue-terminal conflicts, dry-run, import compatibility, and unchanged overseer reports."
  - AC5: "Rollback restores the prior facade controller implementation, and touched terminology uses evidence reference, decision, reconciliation, one-action tick, bounded recovery, diagnostic observation, and fail closed."
depends-on: [TH4.E3.US3]
---

As a maintainer, I want controller reconciliation isolated so that decisions are
reviewable without moving low-level persistence or presentation rules into it.

## Acceptance criteria

- [ ] AC1: Controller evidence and action selection are extracted behind the facade.
- [ ] AC2: Decisions, output, dry-runs, schemas, and adapter contracts are unchanged.
- [ ] AC3: Conflicts and ambiguity remain fail-closed or escalated by existing rules.
- [ ] AC4: Focused controller/import tests prove behavior preservation.
- [ ] AC5: Rollback and controller vocabulary are explicit.

## Compatibility guard

Every implementation session for this story must preserve public
imports/commands, schema/state compatibility, dry-run/fail-closed contracts,
Focused tests named above, and rollback through the `cockpit_control` facade.

## BDD scenarios

### Happy path: tick dispatches exactly one valid action

Given queue, journal, ledger, and worker evidence permit dispatch
When the controller tick runs through the extracted seam
Then exactly one state-changing event is committed and the report matches prior output.

### Edge case: pane text disagrees with durable lifecycle

Given pane text suggests idle but durable lifecycle says running
When reconciliation runs through the extracted seam
Then lifecycle authority wins and no second mission is dispatched.

### Error case: authoritative journal is corrupt

Given committed authority cannot be replayed safely
When a controller tick runs
Then mutation is blocked with existing repair guidance and no controller action is selected.
