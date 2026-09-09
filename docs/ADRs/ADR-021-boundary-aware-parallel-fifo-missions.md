# ADR-021: Boundary-Aware Parallel FIFO Missions

## Status

Proposed

## Context

The queue currently rejects a second active item in `start-next`, and the
controller treats multiple active items as ambiguous. This serializes unrelated
product work even though the journal already reserves one mission slot per
worker. One action per tick (ADR-014) does not require one mission per cockpit.

Existing command boundaries identify the control, queue, planning, and
implementation roots of the cockpit. They do not establish that two missions
are independent. Branch names alone are insufficient: workers can share an
index, working tree, repository-wide Git operations, generated files, planning
artefacts, deployment environments, test databases, ports, or other resources.
Completion, cancellation, and uncertain delivery also matter: a queue state
change is not proof that its worker has stopped.

Discovery anchors:

- `bin/cockpit-queue`: `single_active_item`, `cmd_start_next`, and
  `cmd_clear_current` encode queue-wide serial operation.
- `bin/cockpit_control_controller.py`: `select_controller_action` and
  `_dispatch_locked` require exactly one active item.
- `bin/cockpit_control.py`: `QueueItemObservation` carries no mission-specific
  scheduling scope; command envelopes carry cockpit-wide roots.
- Existing worker-slot replay, reservation, atomic acceptance, and cooperative
  cancellation provide the ownership foundation and must remain authoritative.

## Decision

### Two-stage boundary declaration

Capture intended boundaries when an item is enqueued whenever they are known.
Incomplete discovery is allowed at intake; an unknown boundary never implies
independence. Before parallel admission, the overseer reviews and records the
complete footprint and the rationale for independence. Queue tooling validates
the structured declaration; it does not infer safety from natural-language
mission text.

Review must cover every repository/worktree, branch, writable file or directory,
planning/documentation output, and shared runtime or external resource. Include
generated outputs, test fixtures and environments, deployment targets, Git
operations, and dependencies on the outcomes of earlier queue items. A mission
whose scope cannot yet be bounded remains serial.

At dispatch, re-read authoritative queue state and committed reservations under
the control lock. Bind the reviewed footprint into the immutable dispatch and
its payload digest. At acceptance and redelivery, reject changed declarations
instead of silently widening authority. Existing reservations continue to hold
their original footprint until lifecycle/recovery authority releases them.

Boundary changes require another review. Workers must stop and report an
out-of-scope requirement; they may not expand the declaration while working.
This is scheduling and protocol enforcement, not an operating-system sandbox.

### Conservative independence in the first implementation

Parallelism is opt-in through explicit, reviewed footprints. Legacy or unknown
scopes retain serial semantics. Repository claims are exclusive initially:
sharing a repository, including linked worktrees or branches of it, blocks
parallel execution. Merely declaring different files or branch names does not
override a repository conflict.

Normalize filesystem paths and compare directory ancestry, not string prefixes.
Canonical repository identity must account for linked worktrees; where separate
clones share an upstream identity, that identity must also be declared
consistently. Writable planning/output paths and named shared resources are
exclusive too. Undeclared dependencies or uncertain resource identity must be
treated conservatively by the overseer.

Different repositories may run concurrently only if their remaining claims are
also disjoint and distinct worker slots are available. Multiple instances of a
worker role must have distinct worker IDs and matching explicitly provisioned
tmux windows; the controller does not create worker sessions.

### FIFO and authority

Preserve creation-time/item-ID FIFO admission order. An earlier conflicting or
unreviewed waiting item is not silently bypassed to improve throughput.
Already admitted, independent items may execute simultaneously; FIFO does not
imply FIFO completion. Each tick still commits at most one action.

Keep queue-wide pause and explicit safety conflicts authoritative. A held
mission retains its resources but need not monopolize unrelated worker slots.
Recovery, terminal events, cancelled missions, failed delivery, and replacement
reservations must not accidentally free resources or create duplicate work.

Use addressed clearance when multiple items are active. Preserve the legacy
unaddressed clearance command only when its target is unambiguous. Queue state
remains owned by `cockpit-queue`; control events remain owned by the control
plane. Singular legacy active-item projection fields are not a scheduler lock:
the complete replayed mission-slot set is authoritative for occupied resources.

### Compatibility and delivery

Leave historical events and accepted ADR bodies unchanged. This proposed ADR
documents an opt-in implementation, not retrospective human acceptance or a
supersession of ADR-014's one-action rule. Legacy commands and serial missions
must remain usable. New runtime modules, if needed, must be included in the
managed install/release surface as required by ADR-020.

### Initial implementation contract

Queue items optionally carry a version-1 `footprint` object with `status`
(`draft` or `reviewed`), `rationale`, `repositories`, `write_paths`, `resources`,
and `workers`. Each repository declares its worktree `path`, intended `branch`,
and canonical `upstream`; intake resolves and stores `git_common_dir`. Branches
are intent, not a reason to relax the repository-exclusive claim. Where an
`origin` remote exists, its normalized identity must match the declaration.

`enqueue --footprint <json-file>` records intake scope;
`review-footprint <item-id> --footprint <json-file>` updates queued scope.
`workers` maps a role to a provisioned identity such as
`{"worker-dev": "worker-dev-2"}`. `clear-current --item <item-id>` addresses
completion without assuming a single active item.

The immutable command snapshot is `boundaries.mission_footprint`. Legacy
commands omit it and keep their existing digest. Older command readers reject
the unfamiliar boundary field rather than silently replaying parallel claims;
all participating tools must be updated together. Queue writes and the final
dispatch/acceptance read share a queue lock, acquired after the control lock
when both are needed.

Reviewed footprints only narrow the cockpit's declared authority: repositories
and their Git common directories must remain within implementation roots;
writable paths must remain within implementation or planning roots. Claims
overlapping or enclosing the queue/control stores are refused.

Terminal queue history is validated structurally without requiring its old
worktree to exist. Any still-occupied mission instead retains live validation
of its immutable command footprint. Owed recovery actions take precedence over
new independent dispatch, so a steady intake cannot starve recovery.

The transport handoff is separate from scheduling. Only a newly committed
dispatch is sent to tmux; repeated ticks do not automatically resubmit retained
commands. Unknown delivery keeps its claim and requires inspected recovery,
not a second paste or Enter. This rule also applies to parallel missions.

## Consequences

### Positive

- Independent repositories can progress simultaneously without weakening
  one-worker/one-mission ownership or atomic acceptance.
- Early declarations make conflicts visible during intake and planning.
- Dispatch-time revalidation closes stale-review and competing-tick races.
- Explicit footprints explain why work can or cannot run concurrently.

### Negative

- The overseer must maintain meaningful scope declarations and provision
  separate worker instances.
- Strict FIFO can leave otherwise usable workers idle behind a blocked item.
- Repository-exclusive claims intentionally reject some safe same-repository
  parallel work.

### Risks

- Declarations cannot automatically reveal every semantic dependency or
  external side effect; review quality remains important.
- Filesystem aliases, separate clones, and shared generated outputs can conceal
  overlaps unless identities and all relevant resources are declared.
- Older runtimes must not operate a cockpit that relies on the new opt-in
  scheduling semantics without understanding its boundary declarations.

## Alternatives Considered

### Specify boundaries only at enqueue

Rejected as the sole gate. Intake knowledge is incomplete, and planning or
repository changes can invalidate the initial declaration.

### Discover boundaries only immediately before dispatch

Rejected as the sole source. It hides likely conflicts during queue shaping,
repeats analysis, and leaves no durable intent to compare with the reviewed scope.

### Remove the single-active checks without resource reservations

Rejected. Separate worker IDs are not evidence of independent work.

### Permit same-repository branches or disjoint files immediately

Deferred. A future extension can add isolated-worktree verification, read/write
claims, Git-operation ownership, and integration gates. Different branch names
or apparent file separation alone are not enough.

### Scan past blocked FIFO entries

Deferred. Dependency-aware backfilling needs an explicit ordering policy and
starvation guarantees; it must not be introduced as an incidental optimization.
