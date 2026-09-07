---
id: TH4.E3.US2
title: "Command envelopes and acknowledgements extraction"
type: standard
priority: high
size: M
agents: [developer]
skills: [bdd-stories]
acceptance-criteria:
  - AC1: "Command envelope construction, payload digesting, acknowledgement validation, duplicate handling, conflict detection, and command folding move behind a command seam while facade symbols and commands remain compatible."
  - AC2: "Command schemas, digest bytes, acknowledgement outcomes, idempotency behavior, output, exit codes, dry-run behavior, and state compatibility are unchanged."
  - AC3: "Identifier reuse with a different digest, malformed acknowledgements, undeclared boundaries, and future command schemas fail closed without mutating authority."
  - AC4: "Focused tests cover command registration, duplicate redelivery, digest conflict, acknowledgement outcomes, dry-run, boundary refusal, import compatibility, and unchanged command CLI reports."
  - AC5: "Rollback restores the prior facade command implementation, and touched terminology uses command, acknowledgement, payload digest, idempotency, conflict, and fail closed."
depends-on: [TH4.E3.US1]
---

As a maintainer, I want command protocol logic isolated so that idempotency and
acknowledgements stay explicit and testable.

## Acceptance criteria

- [ ] AC1: Command envelope and acknowledgement logic is extracted behind the facade.
- [ ] AC2: Schemas, digests, outputs, dry-runs, and state compatibility are unchanged.
- [ ] AC3: Conflicts and malformed command data fail closed.
- [ ] AC4: Focused command/import tests prove behavior preservation.
- [ ] AC5: Rollback and command vocabulary are explicit.

## Compatibility guard

Every implementation session for this story must preserve public
imports/commands, schema/state compatibility, dry-run/fail-closed contracts,
Focused tests named above, and rollback through the `cockpit_control` facade.

## BDD scenarios

### Happy path: command is registered and acknowledged

Given a valid mission command envelope
When it is registered and acknowledged through the extracted seam
Then the command slot, acknowledgement, digest, and output match previous behavior.

### Edge case: duplicate command redelivery

Given the same command ID and payload digest are submitted again
When command folding runs through the extracted seam
Then the stored result is returned idempotently without applying the command twice.

### Error case: command ID is reused with a different digest

Given an existing command ID is submitted with a changed payload digest
When validation runs through the extracted seam
Then a hard conflict is recorded or refused exactly as before and no unsafe mutation occurs.
