---
id: TH4.E3.US3
title: "Mission command, question, and recovery transition extraction"
type: standard
priority: high
size: M
agents: [developer]
skills: [bdd-stories]
acceptance-criteria:
  - AC1: "Mission dialogs, questions, replies, access-prompt responses, cancellations, replacements, recovery commands, and mission-slot folding move behind a mission-control seam while facade symbols and commands remain compatible."
  - AC2: "Mission-control schemas, command correlation, prompt/reference handling, output, exit codes, dry-run behavior, and state compatibility are unchanged."
  - AC3: "Missing active missions, mismatched question/reply IDs, invalid replacement IDs, malformed recovery records, and future schemas fail closed without mutating authority."
  - AC4: "Focused tests cover raise/answer question, access-prompt response, cancel, replace, recovery command, mission-slot conflict, dry-run, import compatibility, and unchanged mission CLI reports."
  - AC5: "Rollback restores the prior facade mission-control implementation, and touched terminology uses mission, question, access prompt, reply, cancellation, replacement, recovery command, and authority."
depends-on: [TH4.E3.US2]
---

As a maintainer, I want mission-control transitions isolated so that dialog and
recovery behavior stays correlated to active missions and commands.

## Acceptance criteria

- [ ] AC1: Mission-control transitions are extracted behind the facade.
- [ ] AC2: Schemas, correlations, output, dry-runs, and state compatibility are unchanged.
- [ ] AC3: Invalid mission-control data fails closed without mutation.
- [ ] AC4: Focused mission-control/import tests prove behavior preservation.
- [ ] AC5: Rollback and mission vocabulary are explicit.

## Compatibility guard

Every implementation session for this story must preserve public
imports/commands, schema/state compatibility, dry-run/fail-closed contracts,
Focused tests named above, and rollback through the `cockpit_control` facade.

## BDD scenarios

### Happy path: question receives a correlated reply

Given an active mission has a pending question command
When the reply is recorded through the extracted seam
Then the command correlation, mission slot, output, and stored state match prior behavior.

### Edge case: replacement creates a new mission ID

Given an active mission is cooperatively replaced
When replacement folds through the extracted seam
Then the prior mission is marked replaced and the new mission ID is distinct as before.

### Error case: reply targets the wrong active mission

Given a reply references a command or mission that is not pending
When mission-control validation runs
Then it fails closed with existing diagnostics and no authority is changed.
