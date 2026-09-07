---
id: TH4.E1.US1
title: "Compatibility facade and public import contract"
type: standard
priority: high
size: M
agents: [developer]
skills: [bdd-stories]
acceptance-criteria:
  - AC1: "`cockpit_control` is characterized as the compatibility facade for existing public constants, classes, functions, and `main()` without moving behavior."
  - AC2: "`cockpit-control`, `cockpit-overseer`, `cockpit-wake`, and current tests keep existing imports, commands, output shape, exit codes, schemas, and state compatibility."
  - AC3: "Dry-run and fail-closed behavior remain byte-inert and explicit for all characterized commands."
  - AC4: "Focused import/CLI contract tests cover `import cockpit_control`, known public symbols, and wrapper entry points before later extractions."
  - AC5: "The foundation documents a portable Python module/package layout that works from the source tree and direct `~/.local/bin` execution using only Python stdlib and Bash/bootstrap distribution, with no package manager, virtualenv, generated runtime code, or runtime installer dependency."
  - AC6: "Every extracted runtime module is defined as a managed artifact before extraction and is required in global install/update, link mode, doctor drift checks, uninstall, release tarball/install surface verification, and cold-install smoke coverage."
  - AC7: "Compatibility checks invoke the globally installed `cockpit-control`, `cockpit-overseer`, and `cockpit-wake` wrappers from outside the source checkout with `PYTHONPATH` unset or proven isolated, so source-tree imports alone cannot satisfy the gate."
  - AC8: "No behavior may move into a new runtime module until that module artifact is managed and verified by the installed distribution surfaces."
  - AC9: "Rollback is documented as reverting to the pre-extraction facade and exact managed module set with no data migration or stale installed modules, and touched terminology follows the ontology vocabulary."
depends-on: []
---

As a maintainer, I want the public `cockpit_control` facade pinned before
modularization so that every later extraction can prove it did not break users.

## Acceptance criteria

- [ ] AC1: The facade public contract is characterized without behavior movement.
- [ ] AC2: Existing imports, commands, outputs, schemas, and state files remain compatible.
- [ ] AC3: Dry-run and fail-closed contracts remain unchanged.
- [ ] AC4: Focused import and wrapper tests pin the compatibility surface.
- [ ] AC5: The portable stdlib-only installed/source Python layout is selected and documented.
- [ ] AC6: Extracted runtime modules are defined as managed artifacts across install, doctor, uninstall, release, and cold-install surfaces.
- [ ] AC7: Installed wrappers are tested from an isolated working directory and isolated `PYTHONPATH`.
- [ ] AC8: Behavior extraction is blocked until managed-artifact coverage exists.
- [ ] AC9: Rollback and ontology terminology requirements are explicit for the exact managed module set.

## Compatibility guard

Every implementation session for this story must preserve public
imports/commands, schema/state compatibility, dry-run/fail-closed contracts,
focused tests named above, installed distribution/import behavior from
`~/.local/bin`, managed runtime module artifacts, and rollback through the
`cockpit_control` facade.

## BDD scenarios

### Happy path: current public imports keep working

Given existing wrappers and tests import public symbols from `cockpit_control`
When the compatibility contract test imports those symbols
Then every characterized symbol resolves without import-time side effects.

### Happy path: installed wrappers import from the managed layout

Given the project has been installed through the global bootstrap path
And the test process is outside the source checkout with no source-tree `PYTHONPATH`
When `cockpit-control`, `cockpit-overseer`, and `cockpit-wake` are invoked through their installed `~/.local/bin` wrappers
Then each wrapper resolves `cockpit_control` and characterized public symbols from the managed installed layout
And the observed command shape, output shape, and exit code match the current contract.

### Happy path: managed runtime modules are present on every distribution surface

Given the foundation defines the exact managed runtime Python module set
When global install/update, global link mode, doctor drift checks, uninstall, release tarball verification, and cold-install smoke checks are characterized
Then each surface includes the same managed module set before any behavior extraction is allowed.

### Edge case: dry-run command uses the facade

Given a command that already supports `--dry-run`
When it is invoked through the characterized facade path
Then output and exit code match the current contract and control state bytes do not change.

### Edge case: source-tree imports alone are insufficient

Given a proposed compatibility test only imports modules from the source checkout
When it does not execute the installed wrappers from an isolated working directory and isolated `PYTHONPATH`
Then the story gate remains unmet because installed global cockpit tools are not proven.

### Error case: invalid control root fails through the facade

Given a missing or unsupported `COCKPIT_CONTROL_ROOT`
When a mutating command is invoked through the facade
Then it fails closed with the existing diagnostic shape and no mutation.

### Error case: unmanaged module blocks behavior extraction

Given a later story proposes moving behavior into a new runtime module
When that module is not included in the managed install/update, doctor, uninstall, release, and cold-install surfaces
Then the extraction must fail review before behavior moves.

### Rollback: exact managed module set is restored

Given an extraction is reverted
When rollback is executed for the facade and runtime modules
Then the previous managed module set is restored or removed exactly
And no stale installed module can shadow `cockpit_control` or unrelated user files.
