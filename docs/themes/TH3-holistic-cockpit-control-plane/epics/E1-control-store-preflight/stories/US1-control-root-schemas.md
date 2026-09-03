---
id: TH3.E1.US1
title: "Control root and versioned schemas"
type: standard
priority: high
size: M
agents: [developer]
skills: [bdd-stories]
acceptance-criteria:
  - AC1: "All control-plane commands resolve one explicit absolute COCKPIT_CONTROL_ROOT and never infer it from the working directory."
  - AC2: "Root metadata, ledger, event, command, and escalation records have validated schema versions and required identifiers."
  - AC3: "Missing, relative, malformed, or unsupported future-version configuration fails without mutating control state."
depends-on: []
---

As an overseer, I want an explicit versioned control root so that every model and
tool reads and writes the same durable mission state.

## Acceptance criteria

- [ ] AC1: Commands require one explicit absolute control root.
- [ ] AC2: Canonical records validate required schema and correlation fields.
- [ ] AC3: Invalid or unsupported configuration fails without mutation.

## BDD scenarios

### Happy path: valid root initializes control metadata

Given an absolute writable `COCKPIT_CONTROL_ROOT`
When the control store is initialized
Then versioned root metadata and required directories are created atomically.

### Edge case: root comes from the tmux environment

Given the shell variable is absent and the active cockpit exports the root
When a control command resolves configuration
Then it uses the exact absolute tmux-session value.

### Error case: root is missing or uses a future schema

Given no explicit root exists or its schema version is unsupported
When a mutating command runs
Then it exits non-zero and leaves existing state unchanged.
