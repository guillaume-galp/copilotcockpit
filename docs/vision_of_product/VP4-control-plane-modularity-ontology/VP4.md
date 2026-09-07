# VP4: Control-Plane Modularity and Ontology Adoption

## Vision

VP4 schedules behavior-preserving modularization of the accepted TH3 control
plane. The current public behavior remains the product: `cockpit-control`,
`cockpit-overseer`, `cockpit-wake`, existing imports, control-store schemas,
state transitions, and human-readable output must continue to work while the
oversized `bin/cockpit_control.py` implementation is progressively encapsulated
behind bounded modules and a compatibility facade.

This is a non-functional maintainability and vocabulary initiative. It must not
reopen ADR-011 through ADR-019 mechanisms for aesthetic purity, replace tmux or
the file-backed store, change CLI verbs, alter event/ledger/lock formats, or
perform a big-bang rewrite.

## Desired outcomes

- Small modules map to the canonical bounded contexts in the control-plane
  ontology: control root/schemas/compatibility, locks, journal/projection,
  lifecycle, commands, mission control, controller reconciliation, wake
  scheduling, and CLI/adapters.
- `cockpit_control` remains the stable import facade throughout migration,
  re-exporting existing public functions, constants, and classes until callers
  deliberately move.
- CLI commands, output contracts, exit codes, dry-run behavior, fail-closed
  behavior, state files, and schema compatibility are unchanged at every step.
- Dependency direction is explicit and acyclic: CLI/rendering/adapters depend on
  domain modules, and domain modules do not import CLI, tmux, queue, scheduler,
  or rendering concerns.
- Each extraction is small enough for one developer session and includes focused
  behavior-preservation tests for the moved seam; full repository gates run at
  epic and theme boundaries.
- New or changed files use the ontology vocabulary consistently: queue item,
  mission, command, acknowledgement, worker lifecycle, freshness, wake lease,
  evidence reference, authority, derived state, diagnostic observation,
  one-action tick, bounded recovery, and fail closed.

## Success criteria

1. **Module boundaries:** each bounded context has an owning module or package
   seam with typed contracts at the boundary and no circular imports.
2. **Facade compatibility:** `import cockpit_control` and existing public symbols
   used by `cockpit-control`, `cockpit-overseer`, `cockpit-wake`, and tests keep
   working from both the source tree and globally installed `~/.local/bin`
   wrappers until intentionally retired by a later story.
3. **Behavior preservation:** public commands, help/output shape, exit codes,
   schemas, lock/journal/ledger bytes, dry-run no-mutation behavior, and
   fail-closed diagnostics remain compatible for unchanged mechanisms.
4. **Assurance proportionality:** every story names focused tests for the moved
   boundary; R3/fault/concurrency coverage is required only when the story
   crosses existing durability, security, or concurrency invariants.
5. **Installed distribution:** every extracted runtime module is included in
   global install/update/link, doctor drift checks, uninstall, release tarball
   verification, and cold-install smoke coverage before behavior moves.
6. **Incremental rollback:** every extraction can be reverted to the previous
   facade-backed implementation and exact managed module set without data
   migration or stale installed modules.
7. **Ontology alignment:** terminology introduced or touched by VP4 matches the
   cockpit control-plane ontology, with compatibility aliases documented rather
   than silently renamed.

## Non-goals

- Implementing new runtime control-plane behavior.
- Rewriting all of `bin/cockpit_control.py` at once.
- Changing accepted ADR bodies or TH3 artefacts.
- Renaming accepted CLI verbs, event types, schema fields, or state values only
  for stylistic consistency.
- Adding a daemon, database, generated-code step, or new runtime dependency.
