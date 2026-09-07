Compatibility facade and portable module layout

This document records the chosen portable Python module/package layout and the
managed-artifact scaffolding required by the Control Plane Modularity foundation
(TH4.E1.US1).

Goals
- Keep cockpit_control as the public compatibility facade: existing public
  constants, classes, functions and main() remain available at import time.
- Do not move any behavior into a new runtime module until that module is
  declared as a managed artifact (see MANAGED_RUNTIME_MODULES).
- Support execution from the source tree and from simple per-user global
  wrappers (e.g. ~/.local/bin) without requiring venvs, pip, or generated
  runtime code. Only Python stdlib and bash/bootstrap scripts are required.

Layout
- bin/ contains the wrapper entrypoints used in global installs and during
  development. Each wrapper imports from the top-level module name that it
  expects (for example, the wrapper `cockpit-control` imports `cockpit_control`).
- The compatibility facade is provided as a top-level Python module file in
  bin/cockpit_control.py (importable as `cockpit_control` when bin/ is on
  PYTHONPATH or when the wrapper executes with sys.path[0] set to its directory).
- No packaging, wheel, or generated runtime artifacts are required for the
  pre-extraction foundation. Global installs may be performed by copying
  the wrapper scripts to ~/.local/bin and ensuring the installed modules are
  available on the filesystem in a stable location.

Managed artifact scaffolding
- Every runtime module that may later be extracted MUST be declared before the
  extraction. The repository root file MANAGED_RUNTIME_MODULES lists the
  top-level module names that the installed distribution must include. This
  list is used by install/update/link/doctor/uninstall/release verification and
  by cold-install smoke tests to ensure the installed surface provides the
  required artifacts.

- At foundation time the MANAGED_RUNTIME_MODULES file started with the
  compatibility facade `cockpit_control`. As extraction stories land, each new
  runtime module is added there before behavior moves, for example
  `cockpit_control_root_schema` for control root and schema compatibility.

Compatibility checks
- Tests that validate installed wrapper behavior must run from an isolated
  working directory outside the source checkout with PYTHONPATH unset (or
  otherwise proven isolated). This ensures wrappers cannot be satisfied by
  accidental imports from developer source trees.

Rollback
- Rollback to the pre-extraction facade consists of restoring MANAGED_RUNTIME_MODULES
  to its previous contents and reverting any changed wrapper/install scripts.
  No data migration is performed; installed runtime modules remaining on disk
  that are not in MANAGED_RUNTIME_MODULES are considered unmanaged and must be
  removed during uninstall or by explicit doctor repair documentation.
