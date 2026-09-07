# ADR-020: Incremental Control-Plane Module Extraction and Compatibility Facade

## Status

Accepted

## Context

TH3 delivered the VP3 control-plane mechanisms in and around
`bin/cockpit_control.py`. That file now concentrates many responsibilities:
control-root resolution, schema validation, portable locks, immutable event
publication, ledger replay, worker lifecycle, command envelopes,
mission-control records, queue observation, controller reconciliation,
preflight/repair, CLI parsing, and human-readable rendering.

The implemented behavior is accepted through TH3 and must not be reopened for
aesthetic purity. However, the file size makes future changes harder to review,
raises the risk of accidental circular dependencies, and obscures the bounded
contexts documented in the control-plane architecture.

Post-TH3 evolution needs to progressively encapsulate this implementation
without a big-bang rewrite, without changing public commands, and without
breaking existing imports such as `from cockpit_control import main`,
`controller_tick`, `publish_control_event`, `resolve_control_root`, and the
constants/classes used by `cockpit-overseer`, `cockpit-wake`, and tests.

## Decision

Extract `bin/cockpit_control.py` incrementally into small bounded-context
modules behind a stable compatibility facade.

The public module name `cockpit_control` remains the import compatibility
surface for the current distribution model. The `cockpit-control` CLI continues
to call `from cockpit_control import main`. Existing public commands, output
contracts, event schemas, file layouts, and accepted ADR-011..019 mechanisms are
preserved unless a later ADR explicitly supersedes them.

### Installed distribution contract

Before any behavior moves out of `bin/cockpit_control.py`, the foundation story
must select and document a portable Python module or package layout that works
both from a source checkout and from direct execution of the installed
user-scoped tools in `~/.local/bin`. The layout must remain compatible with the
current Bash/bootstrap distribution model: Python standard library only, no
package manager install step, no virtual environment requirement, no generated
runtime code, and no service or runtime installer dependency.

Every extracted runtime Python module is a managed distribution artifact. The
exact managed module set must be included in all surfaces that currently manage
`bin/cockpit_control.py`:

- `bootstrap.sh global` install/update, including copy and link modes;
- `bootstrap.sh doctor` drift checks for installed cockpit tools;
- `uninstall.sh` removal of project-owned installed artifacts;
- release tarball assembly and install surface verification;
- cold-install smoke coverage from an extracted release artifact.

Compatibility checks must execute the globally installed wrappers
(`cockpit-control`, `cockpit-overseer`, and `cockpit-wake`) from an isolated
working directory with `PYTHONPATH` unset or otherwise proven not to point at
the source checkout. Source-tree imports are useful characterization, but they
are not sufficient to prove the installed contract.

No story may move behavior into a new runtime module until that module's
artifact is named in the managed module set and verified by the install,
doctor, uninstall, release, and cold-install surfaces above. Rollback for an
extraction must remove or restore the exact managed module set safely, without
leaving stale installed modules that can shadow the facade or breaking
unrelated user files.

Target module seams:

| Target seam | Responsibility |
|---|---|
| `control_root` / `schemas` / `compatibility` | Root resolution, schema constants, validation helpers, forward-version refusal, compatibility diagnostics. |
| `locks` | Portable guarded lock acquisition, exact release, stale repair, quarantine/debris types. |
| `journal` | Immutable event naming, committed sequence reading, event validation, event publication. |
| `projection` | Deterministic ledger fold, derived ledger replacement, rebuildable events view. |
| `lifecycle` | Worker lifecycle vocabulary, transition validation, freshness observation. |
| `commands` | Command envelopes, payload digests, acknowledgements, idempotency/conflicts. |
| `mission_control` | Questions, replies, access prompts, cancellations, replacements, recovery commands, mission slots. |
| `controller` | Evidence snapshot, precedence, action selection, one-action tick, reporting inputs. |
| `wake` / `scheduling_adapters` | Wake lease and scheduler-facing integration points without owning controller decisions. |
| `queue_adapter` / `tmux_adapter` / `cli` / `rendering` | Boundary adapters, argument parsing, subprocess/tmux calls, human-readable output. |

Dependency direction:

```text
cli/rendering/adapters
          |
          v
controller / mission_control
          |
          v
commands / lifecycle / projection
          |
          v
journal / locks / schemas / control_root
```

Lower-level modules must not import CLI, rendering, tmux, queue, or scheduler
adapters. Domain modules expose typed contracts and pure functions where
practical. Adapter modules translate external command output and filesystem or
subprocess details into those contracts.

Typed contracts should use explicit dataclasses, closed vocabularies, and
schema-validation functions at module boundaries. Existing dict-shaped payloads
may remain on the wire for compatibility, but conversion should be localized at
the boundary that owns the schema.

The facade module re-exports the existing public constants, classes, and
functions during migration. New implementation modules are allowed to exist only
when tests prove that the facade still satisfies existing import and CLI
contracts.

## Extraction sequence

Use narrow, behavior-preserving stories. Each story moves one seam or one
cohesive subset and proves equivalence before proceeding.

1. Select and document the installed Python module/package layout and managed
   artifact set, then create the package/facade skeleton with no behavior
   movement.
2. Inventory and pin public imports used by wrappers and tests.
3. Extract control-root resolution, schema constants, and validation helpers.
4. Extract lock ownership, guarded acquisition/release, and stale repair.
5. Extract immutable event history/publication.
6. Extract ledger projection and replay.
7. Extract worker lifecycle and freshness.
8. Extract command envelope and acknowledgement logic.
9. Extract mission dialog, cancellation, replacement, and recovery folds.
10. Extract controller evidence, decision, reconciliation, and tick reporting.
11. Extract wake/scheduler adapters only where they depend on control contracts.
12. Move CLI parsing and human-readable rendering last, keeping `main()` as the
    stable facade entry point throughout.

Any step may be split further if review size grows beyond a single
responsibility.

## Testing and rollback rules

Every extraction story must include:

- import compatibility tests for `import cockpit_control` and all public symbols
  used by `cockpit-control`, `cockpit-overseer`, `cockpit-wake`, and current
  unit tests;
- installed-wrapper compatibility tests that invoke the globally installed
  cockpit tools from outside the source checkout with no source-tree
  `PYTHONPATH`;
- CLI golden-output or contract tests for affected subcommands;
- schema and replay equivalence tests for moved domain logic;
- managed-artifact checks proving extracted runtime modules are installed,
  updated, drift-checked, included in release artifacts, smoke-tested after
  cold install, and removed/restored by rollback;
- no change to committed event formats, ledger bytes, lock behavior, or exit
  code semantics unless covered by a separate ADR;
- a rollback path that restores the prior facade implementation and the exact
  managed module set without data migration.

Lock, journal, projection, controller, and wake changes must continue to run the
ADR-018/ADR-019 interruption and concurrency suites and the VP3 resilience
scenario where applicable.

## Compatibility facade rules

The facade must:

- preserve `bin/cockpit_control.py` or an equivalent `cockpit_control` import
  target in the installed `bin/` layout;
- keep `cockpit-control` as the user-facing executable;
- keep installed wrappers able to import the facade and any extracted runtime
  modules without source-checkout paths on `PYTHONPATH`;
- re-export existing public symbols until their callers are deliberately moved;
- avoid import-time side effects beyond defining constants/classes/functions;
- not require a new runtime service, package manager, database, or generated
  code step for normal installed use.

A symbol may be removed from the facade only after repository-wide callers and
tests have migrated and a release note or compatibility note documents the
change.

## Non-goals

- No big-bang rewrite of `bin/cockpit_control.py`.
- No change to ADR-011..019 mechanisms for naming, locking, event publication,
  replay, precedence, or wake semantics.
- No replacement of tmux, the file-backed control store, or the current local
  distribution model.
- No aesthetic renaming of accepted CLI verbs or event vocabulary.
- No migration of product backlog or queue storage into the control store.
- No code extraction as part of the architecture-only TH4 ontology session.

## Consequences

### Positive

- Future stories can change one bounded context at a time.
- Reviewers can map code modules directly to architecture and ADR ownership.
- Existing users and tools keep the same command and import surface.
- Risky mechanisms remain protected by their current contract and fault tests.

### Negative

- The facade temporarily re-exports many symbols and may look redundant.
- Some modules will initially expose dict-compatible contracts until schemas are
  localized further.
- During migration, developers must maintain both old imports and new module
  seams.

### Risks

- Circular imports could emerge if domain modules import CLI/adapters; dependency
  direction must be checked in review.
- Incomplete re-exports could break tests or downstream wrappers; each story
  must pin public imports.
- Moving code without characterization tests could alter subtle crash/replay
  behavior; high-risk seams require fault-injection coverage before merge.

## Alternatives Considered

### Big-bang package rewrite

Rejected because it would bundle many accepted mechanisms into one risky change
and make rollback difficult.

### Leave `bin/cockpit_control.py` as the permanent implementation

Rejected because the current size obscures bounded contexts and increases the
cost of future TH4+ changes.

### Split by CLI command only

Rejected because CLI verbs cut across deeper concerns such as locks, journal,
projection, lifecycle, and controller decisions. Domain ownership must drive the
module seams, with CLI parsing kept thin.

### Introduce a hosted service or database-backed control plane

Rejected as disproportionate to the local-first MVP and contrary to ADR-012,
ADR-017, and ADR-019.
