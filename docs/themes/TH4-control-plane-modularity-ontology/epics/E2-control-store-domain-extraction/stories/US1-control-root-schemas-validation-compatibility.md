---
id: TH4.E2.US1
title: "Control-root schemas, validation, and compatibility extraction"
type: standard
priority: high
size: M
agents: [developer]
skills: [bdd-stories]
acceptance-criteria:
  - AC1: "Control-root resolution, schema constants, validation helpers, forward-version refusal, and compatibility diagnostics move behind a control-root/schema seam while `cockpit_control` re-exports existing symbols."
  - AC2: "Public commands, output, exit codes, schema/state compatibility, and existing root-resolution precedence remain unchanged."
  - AC3: "Missing, relative, malformed, or unsupported future-version configuration still fails closed and dry-run remains non-mutating."
  - AC4: "Focused tests cover valid roots, tmux-root fallback, invalid roots, future schemas, import compatibility, and unchanged preflight diagnostics."
  - AC5: "Rollback restores the facade-backed implementation without data migration, and touched vocabulary uses control root, authority, compatibility, and fail closed consistently."
depends-on: [TH4.E1.US2]
---

As a maintainer, I want root and schema behavior extracted first so that all
later modules share one explicit compatibility boundary.

## Acceptance criteria

- [ ] AC1: Root and schema logic is extracted behind a facade-preserved seam.
- [ ] AC2: Commands, output, exit codes, schemas, and state compatibility are unchanged.
- [ ] AC3: Invalid configuration and dry-run contracts remain fail-closed and non-mutating.
- [ ] AC4: Focused root/schema/preflight/import tests prove behavior preservation.
- [ ] AC5: Rollback and ontology vocabulary are explicit.

## Compatibility guard

Every implementation session for this story must preserve public
imports/commands, schema/state compatibility, dry-run/fail-closed contracts,
Focused tests named above, and rollback through the `cockpit_control` facade.

## BDD scenarios

### Happy path: valid control root validates through the new seam

Given an absolute writable `COCKPIT_CONTROL_ROOT`
When existing initialization and preflight commands call the extracted seam
Then their output, files, and facade imports match the pre-extraction contract.

### Edge case: tmux environment supplies the control root

Given the shell variable is absent and tmux exports a valid control root
When root resolution runs through the extracted seam
Then the same absolute root is selected as before and no schema bytes change.

### Error case: future schema is encountered

Given control metadata declares an unsupported future schema
When a mutating command validates the root
Then mutation is refused with the existing diagnostic wording and exit semantics.
