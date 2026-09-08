# BUG-001: Control-Plane Bootstrap and Durable Dispatch Are Disconnected

**Status:** Resolved on `fix/control-plane-bootstrap-dispatch` (not released)

**Reported:** 2026-09-07  
**Resolved:** 2026-09-08

**Owner:** CopilotCockpit  
**Severity:** Critical  
**Affected components:** `cockpit-control`, `cockpit-overseer`,
`cockpit-protocol`, `cockpit-queue`, `cockpit-wake`, E2E cockpit launchers  
**Related decisions:** ADR-010, ADR-011, ADR-012, ADR-013, ADR-014, ADR-015,
ADR-016, ADR-017

## Summary

All eight acceptance criteria are satisfied by the implementation and isolated
public-CLI regression coverage recorded below. The symptoms, reproduction, and
root-cause sections retain the original diagnostic context; they describe the
pre-fix behavior, not the current supported flow.

The installed VP3 control-plane tools fail closed at individual boundaries, but
cannot be bootstrapped into one operational controller. A control store can be
initialized without canonical queue/planning/implementation roots, and no
supported command can declare them afterward. The only available dispatch
commands then bypass the durable mission lifecycle and paste text directly into
tmux panes.

This prevents an overseer from truthfully claiming a durable FIFO dispatch,
worker cancellation, or lifecycle-based recovery.

## Symptoms

1. `cockpit-protocol meta cockpit --json` fails unless
   `COCKPIT_CONTROL_ROOT` is explicitly exported.
2. `cockpit-control init` succeeds with `queue_root`, `planning_root`, and
   `implementation_roots` unset.
3. `cockpit-control preflight` reports `ready`, while also reporting no product
   work authority and no declared planning or implementation roots.
4. `cockpit-overseer tick` cannot select a FIFO item because no canonical queue
   root is declared.
5. `cockpit-protocol mission` marks a pane as working but leaves
   `cockpit-control lifecycle-status` and `mission-status` at zero missions.
6. A worker can display an interactive approval prompt while
   `cockpit-protocol status` reports it as `available`.
7. A protocol `send` cannot durably acknowledge, cancel, or replace that
   pending worker action.

## Reproduction

Use an existing project cockpit that still exports only the legacy queue root:

```bash
export COCKPIT_CONTROL_ROOT="$PWD/docs/queue"
cockpit-control init
cockpit-control preflight
cockpit-overseer tick -s udm-k8s
cockpit-protocol mission \
  --worker worker-dev \
  --session udm-k8s \
  --id "$(cat /proc/sys/kernel/random/uuid)" \
  --message "planning-only mission"
cockpit-control lifecycle-status
cockpit-control mission-status
```

Observed results:

- preflight identifies a structurally valid store but no declared product
  boundaries;
- the tick cannot dispatch from the store;
- the protocol mission is visible only in the worker pane;
- the durable store still reports zero missions and zero worker slots.

For the launcher compatibility path, inspect:

```text
e2e/tmux-cockpit.sh
e2e/tmux-cockpit-local.sh
```

Both export and inject `COCKPIT_QUEUE_ROOT` only. They do not configure
`COCKPIT_CONTROL_ROOT` for the shell or tmux session.

## Root Cause

`cockpit-control init` deliberately initializes nullable canonical roots, but
the public CLI has no atomic follow-up operation that declares them. The
controller correctly refuses dispatch from an undeclared root, but
`cockpit-overseer dispatch` and `cockpit-protocol mission` remain direct tmux
delivery paths rather than controller-owned mission transitions.

The legacy E2E templates predate the separate control-root contract. They reuse
`docs/queue` as a queue location and export only the queue variable, leaving
the upgraded control store unconfigured. Status is derived from pane text and
does not model pending interactive confirmation, command acknowledgements, or
the durable lifecycle record.

## Required Fix

1. Provide one atomic bootstrap/configuration operation that creates or binds
   distinct absolute paths for:
   - `COCKPIT_CONTROL_ROOT`;
   - `COCKPIT_QUEUE_ROOT`;
   - planning root; and
   - one or more implementation roots.
2. Make the operation update control metadata and derived ledger atomically,
   or require all roots as mandatory `init` arguments. It must never require
   hand-editing immutable control-store files.
3. Make the controller-owned dispatch path the only normal mission entry point:
   it must persist the dispatch envelope, claim the worker slot, declare the
   mission/queue/trace boundaries, deliver the brief, and await an
   acknowledgement.
4. Make cancellation, replacement, worker heartbeat, and interactive approval
   prompts durable mission lifecycle events.
5. Make `status` distinguish `available`, `working`, `awaiting-approval`,
   `held`, `blocked`, and `unreachable`; pane observation must not override
   durable lifecycle evidence.
6. Update managed E2E launchers/templates to export and inject both root
   variables into tmux. Use separate directories such as
   `docs/cockpit-control` and `docs/cockpit-queue`.
7. Make wake schedules persist control-root and target identity as structured
   fields and inject them at execution time. Retain a backward-compatible
   `stop` alias or update all managed skills and documentation atomically.
8. Report `operationally-blocked` rather than `ready` when a store lacks
   declared work boundaries or required lifecycle capability.

## Acceptance Criteria

- [x] A fresh E2E cockpit bootstrap creates distinct queue and control roots
  and declares planning/implementation boundaries without manual file edits.
- [x] `cockpit-control preflight` exits non-zero and reports
  `operationally-blocked` when any required dispatch boundary is absent.
- [x] One controller dispatch creates exactly one durable mission, command,
  worker-slot claim, trace, and lifecycle record before text reaches a pane.
- [x] Redelivery of the same command is idempotent; a different command cannot
  claim an occupied worker slot without an explicit replacement event.
- [x] A pending worker confirmation appears as `awaiting-approval` and can be
  held/canceled/replaced through the protocol with an acknowledgement.
- [x] Controller `tick` dispatches the earliest eligible queue item and no
  longer requires the legacy direct-tmux dispatch path.
- [x] A scheduled wake runs with its stored control root and target identity,
  and `stop`/`cancel` behavior is documented and compatible.
- [x] Existing legacy cockpit state is migrated or diagnosed according to
  ADR-017 without destructive rewriting.

## Regression Coverage

- CLI tests for fresh bootstrap, missing-root refusal, root binding, and
  distinct root paths.
- Controller integration tests covering enqueue, FIFO dispatch, acknowledgement,
  heartbeat, cancellation, replacement, and stale acknowledgement recovery.
- E2E template tests proving both root variables enter the launched tmux
  environment.
- Wake tests proving root/target preservation and compatible cancellation.
- Status tests proving a pending confirmation cannot be rendered as
  `available`.

## Scope Boundary

This report does not change UDM application code, UDM profile semantics, or
Autopilot's product backlog schema. The companion Autopilot issue owns the
backlog-to-queue adapter and cross-workspace mission-packet requirements.

## Implemented Dispatch and Recovery Contract

Controller dispatch envelopes now declare an immutable acceptance deadline,
five minutes after dispatch creation. Redelivery reuses that deadline, command
ID and digest; it never extends the window. At or after the deadline, a tick
records `blocked / dispatch-acceptance-expired` once for unchanged evidence and
stops sending input. The reservation remains held. This is an observation of
silence, not a worker failure, cancellation, replacement, or new mission.
An operator must inspect the command and worker and decide explicit recovery
or queue disposition. `recover-dispatch` now releases an unaccepted reservation
only through an explicit, correlated inspection decision. Queue disposition
alone does not release a reservation. Never fabricate acceptance to enable
replacement or edit immutable events.

Each delivered brief includes the boundaries, digest, deadline and exact
shell-quoted receipt command:

```bash
COCKPIT_CONTROL_ROOT=/absolute/control cockpit-control accept-dispatch \
  --command-id <uuid> --mission <uuid> --worker worker-dev \
  --queue-item <id> --trace <uuid> --payload-digest sha256:<digest> \
  --fresh-for 300
```

The receipt validates the registered dispatch, canonical boundaries, current
queue brief/state, worker slot and correlation before publishing a single
`worker-lifecycle-accepted` event containing both `worker_lifecycle` (sequence 1)
and `command_acknowledgement` (accepted). The journal rename commits both or
neither; projections are replayable. Acceptance and final transport validation
share the portable control lock. A receipt cannot accept another worker's
slot, a changed payload, or a terminal/replaced mission.

The CLI returns JSON. Only `outcome=accepted` with `start_work=true` permits a
new execution. A repeat returns `duplicate`, `start_work=false` and the original
receipt event ID without publishing or refreshing lifecycle state. Dry-run
returns `would-accept`, `start_work=false` without writing. If the worker loses
the first response or restarts after accepting, this protocol deliberately
does not infer whether work began: duplicates require observation/explicit
recovery, never a second execution. Workers next emit `running` sequence 2 and
renew freshness under the existing lifecycle protocol.

Old reservations without a deadline are diagnosed as
`dispatch-acceptance-unsupported`; no timeout or successful acceptance is
fabricated. Existing immutable history remains readable, but pending legacy
handoffs require explicit operator recovery rather than event rewriting.

The controller's initial dispatch publication also includes an honest
`pending-dispatch` lifecycle at sequence 0, with no worker heartbeat or accepted
acknowledgement. It is visible in mission/lifecycle projections before transport.

After inspecting the worker and establishing that execution did not start:

```bash
cockpit-protocol recover-dispatch \
  --dispatch-command <original-command-uuid> \
  --command-id <inspection-command-uuid> --worker worker-dev \
  --inspect-safe --by operator --reason "Inspected worker; no execution" \
  --evidence file:/private/inspection-record
```

This decision fences late acceptance, is idempotent for the same ID and inputs,
and returns `worker_stopped: false`. `--dry-run` previews without writing.
It supports expired and legacy deadline-free reservations, not accepted work.
Accepted work requires `cancel-mission` or `replace-mission` and the addressed
worker's cooperative `accepted` then `applied` acknowledgement with a result
reference. A replacement reserves a new mission ID; the next controller tick
publishes its dispatch, and the replacement worker must obtain a new receipt.

## Acceptance Evidence

All evidence below is in repository tests; transport, schedulers, HOME, and
configuration/cache directories are isolated. No real user pane, installed
global runtime, cron entry, or `at` schedule was operated.

| Criterion | Implemented behavior | Regression evidence |
|---|---|---|
| 1. Fresh bootstrap | `bootstrap.sh e2e <explicit-project>` initializes distinct `docs/cockpit-control` and `docs/cockpit-queue`, declares `docs/plan` and the explicit implementation target, and generates launchers exporting/injecting both roots. Existing project-owned launchers remain preserved. | `tests/unit/cmd-e2e.bats`: fresh scaffold inspects metadata/ledger roots, both launchers and ready preflight; dry-run writes nothing. Root/bootstrap foundation retained from `9dec12f`. |
| 2. Blocked preflight | Absent boundaries or unsupported lifecycle/command capabilities produce nonzero `operationally-blocked`, with supported root-binding or adoption guidance. | `cmd-control-preflight.bats`; `test_bug001_runtime.py` status/preflight and pending legacy boundary cases; `test_wake_runtime.py` preflight/boundary/capability refusals. |
| 3. Intent before pane | One committed dispatch owns one command, mission, worker slot, trace and sequence-0 pending lifecycle before any load/paste/input. Accepted lifecycle is never fabricated. | `test_bug001_runtime.py::test_dispatch_has_one_honest_pending_lifecycle_before_any_transport`; public integration transport independently snapshots the journal and ledger at actual tmux load/paste/input boundaries. |
| 4. Idempotency and slots | Same command/digest/deadline redelivery reuses intent; one receipt authorizes execution; conflicting/occupied claims refuse. New replacements require `cooperative: true` and wait for applied worker ACK; committed legacy replacements remain replayable. Explicit pending recovery fences late receipts. | Thirty `test_dispatch_acceptance.py` cases retain concurrency, interruption, queue-claim and identity coverage; runtime recovery/answer races and registration-path admission; public CLI duplicate receipt, occupied-slot, missing/false/null cooperative admission, and committed legacy replacement replay assertions. |
| 5. Approval and lifecycle | Explicit `ask`/`access-prompt`, `hold`, human `reply`, heartbeat, cancel and replace use existing durable command/dialog/lifecycle primitives and correlated ACKs. Envelope/record identity and registered dispatch boundaries must agree. Status distinguishes available, working, awaiting-approval, held, blocked and unreachable; missing committed authority blocks instead of implying idle. | `test_bug001_runtime.py` dialog/recovery correlation and legacy replay cases; public integration covers cancel/replace envelope mismatches, foreign accepted/applied ACKs, actual journal suffix loss, and legitimate missing/stale projections. |
| 6. FIFO controller | Queue selection and one-action ticks remain the only normal mission entry. Mission/direct dispatch bypasses refuse; bootstrap dispatch is role priming only. | `cmd-overseer-tick.bats`, acceptance queue-claim races, `test_bug001_public_cli.py::test_complete_public_lifecycle_and_scheduled_resume`, and updated `resilience.bats` cooperative replacement flow. |
| 7. Scheduled identity and stop | Schedule state stores `control_root`, structured session/window target, mission, queue, owner and intent. Jobs reload and inject stored identity, validate it before lease/tick, and ignore conflicting ambient hints. `stop` aliases `cancel`; recurring wakes remain cancellable after firing. The shared wake-state lock fences final admission through actual subprocess creation, then releases before waiting. | `test_wake_runtime.py` generated jobs, identity guards, aliases, dry-run, lease recovery, and deterministic barriers before admission, inside inbox/process creation, during the running child, and at completion; public integration restores context through a generated job. |
| 8. Legacy safety | Missing roots/capabilities, deadline-free reservations and legacy wake identity receive actionable ADR-017 diagnosis. `migrate` is read-only; operators inspect, stop and reschedule explicitly. No immutable event or live metadata rewriting. | `test_bug001_public_cli.py::test_legacy_pending_handoff_is_diagnosed_without_rewriting_history`; runtime legacy recovery/history assertions; wake corrupt/future/missing metadata and migration snapshots. |

`tests/integration/test_bug001_public_cli.py` uses public binaries for every
transition, not internal control calls. It enqueues FIFO items, dispatches,
receipts, heartbeats, reports/holds/answers prompts, acknowledges cancellation
and replacement, redispatches/accepts the replacement, replays projections,
checks authoritative status, restores a scheduled wake and stops it. JSON
artifact reads independently confirm the resulting durable evidence.

## Review Defect Corrections

The four follow-up findings were corrected without replacing the existing
implementation or changing its delivery scope:

1. **Foreign command ACKs:** publication now checks the envelope against its
   correlated worker, mission, queue, trace and parent trace, plus the original
   dispatch boundaries where present. Reservation inspection also matches the
   original dispatch digest. The same guard covers dialog, cancellation,
   replacement, and recovery replay. Historically admitted mismatched pairs
   remain immutable audit evidence but register no actionable command and fold
   no lifecycle/dialog/recovery effects. Applied cancellation/replacement also
   checks the actual owning worker before side effects. Valid child-trace
   dialogs and legacy non-cooperative replacements remain replayable.
2. **Stop/launch gap:** final wake reload, inbox publication and `Popen`
   creation share the stop lock. A stop that wins admission prevents launch;
   a contending stop cannot report success before the already-admitted child is
   created. File descriptors are closed in the child and the parent releases
   the lock before waiting, so an in-flight controller can read wake state and
   stop remains possible. Completion cannot overwrite cancellation.
3. **Missing authority in status:** the existing ahead-ledger diagnostic now
   makes protocol status authoritative `blocked` rather than folding a truncated
   journal into available/idle. Missing or stale rebuildable projections still
   permit read-only status and explicit replay. Public tests delete the actual
   accepted journal suffix while retaining the real ledger, rather than merely
   forging its revision.
4. **New legacy-shaped replacement bypass:** the shared new-event publication
   boundary now requires `cooperative: true` on every replacement. Missing,
   false, and null values refuse before event or projection writes, including
   fresh commands submitted through public `publish-event`, managed registration,
   generic correlated registration, and declarations. Replay validation remains
   compatible with already committed legacy records: an additive old-writer
   fixture rebuilds the replaced mission and reserved successor with zero ACKs
   without rewriting any committed event. The corresponding new cooperative
   record remains registered with zero ACKs and leaves the accepted mission and
   slot unchanged until the owning worker acknowledges accepted then applied.

New public regressions first reproduced the unfenced envelope/status behavior.
The negative cases exercise both cancellation and replacement across worker
target/kind, mission, queue, trace, parent trace and boundary mismatches, public
accepted/applied ACK attempts, historical raw ACK events, and subsequent valid
owning-worker ACKs. All historical corruption/loss fixtures are isolated test
stores; no live journal was edited.
The final admission regression also reproduced successful publication and
immediate replacement for a fresh, correctly correlated command missing
`cooperative` before the fix; all three registration paths reproduced the same
missing-field bypass. The committed legacy replay regression passed before and
after the fix.

## Final Gates and Review

Final `./run-tests.sh all`: **PASS, exit 0**, after all runtime, documentation,
and wake-race changes, including the final new-event admission fix:

| Gate | Result |
|---|---|
| Unit | 224/224 Bats checks; Python discovery includes 135 cases, including 19 BUG-001 runtime and 35 wake-runtime cases. |
| Template integrity | PASS, including ownership manifests and generated surfaces. |
| Skills | All 8 canonical skills pass; 7 additional managed-guidance regressions pass. |
| Integration | 9/9 Bats checks, including the 6 public-CLI BUG-001 scenarios. |
| Codex compatibility | PASS, canonical symlink exposures and both managed template trees preserved. |
| Whitespace | `git diff --check` passes. |
| Review | The earlier wake completion lost-update fix is retained. The four follow-up defects are corrected with public envelope/ACK, new replacement admission and authority-loss regressions, adjacent registration/dialog/recovery and legacy replay coverage, and deterministic admission/process-creation/in-flight stop barriers. |

Final affected targeted runs passed before the full gate: **59/59 Python tests**
across BUG-001 runtime, mission-control seam, dispatch acceptance, and public CLI
integration; **48/48 Bats checks** across managed missions, overseer recovery, and
resilience. Both targeted commands and `./run-tests.sh all` exited 0.
Earlier failing legacy assertions were updated to require
the real cooperative protocol, not weakened to permit automatic worker replacement.
The wake lock is independent of the control root and is held through subprocess
creation, but never while waiting for the controller to finish. Existing managed
runtime module lists already include every changed module; no new runtime module
or dependency was introduced. One early wake-only run hit the existing five-second
state-lock timeout in the concurrent-schedule case; its isolated rerun and the
final full gate passed. A new dialog test fixture initially paired a question
reply with an access prompt; correcting the fixture to use a question restored
the intended valid-legacy assertion without relaxing the implementation guard.

## Remaining Operational Limits

- Native, uninstrumented permission/question prompts cannot be authoritatively
  inferred from pane text. Workers/operators must explicitly report them via
  `ask` or `access-prompt`; the tools never auto-answer or auto-approve.
- Worker ACKs are cooperative local protocol evidence, not process termination
  or authentication. A silent accepted worker remains blocked for human action.
  Inspection recovery cannot guarantee exactly-once external side effects.
- `stop` fences future wake execution; it does not kill a controller action
  already in flight. Stop-condition text is retained intent, not a general
  expression evaluator; existing controller terminal/suspension rules apply.
- Existing project-owned launchers/overlays and legacy scheduler scripts are not
  silently upgraded. Operators must review configuration, stop old jobs and
  reschedule with full identity. Already-installed legacy shell scripts cannot
  be intercepted by a newer CLI.
- Real tmux/scheduler deployment was deliberately not exercised. This result is
  local implementation plus isolated end-to-end proof, not an installed rollout.
  No installed rollout, push, PR, release, backlog change, or locked artifact
  edit was made.

## Complete Changed-File Inventory

Relative to `de5973e`, this completion changes or adds the following files.
The earlier root/bootstrap and durable-dispatch foundation remains in the three
prior commits and is not duplicated in this inventory.

```text
README.md
bin/cockpit-overseer
bin/cockpit-protocol.go
bin/cockpit-wake
bin/cockpit_control.py
bin/cockpit_control_commands.py
bin/cockpit_control_controller.py
bin/cockpit_control_journal.py
bin/cockpit_control_lifecycle.py
bin/cockpit_control_mission_control.py
bin/cockpit_control_wake.py
docs/plan/BUG-001-control-plane-bootstrap-and-durable-dispatch.md
skills/copilotcockpit-dev/SKILL.md
skills/e2e-cockpit/SKILL.md
skills/e2e-operator/SKILL.md
skills/setup-e2e-cockpit/SKILL.md
skills/setup-e2e-runbook/SKILL.md
skills/worker-dev/SKILL.md
skills/worker-fix/SKILL.md
skills/worker-test/SKILL.md
templates/e2e/.agents/skills/e2e-cockpit/SKILL.md.tmpl
templates/e2e/.agents/skills/e2e-operator/SKILL.md.tmpl
templates/e2e/.agents/skills/worker-dev/SKILL.md.tmpl
templates/e2e/.agents/skills/worker-fix/SKILL.md.tmpl
templates/e2e/.agents/skills/worker-test/SKILL.md.tmpl
templates/e2e/.github/copilot-instructions.md.tmpl
templates/e2e/.github/skills/e2e-cockpit/SKILL.md.tmpl
templates/e2e/.github/skills/e2e-operator/SKILL.md.tmpl
templates/e2e/.github/skills/setup-e2e-cockpit/SKILL.md.tmpl
templates/e2e/.github/skills/setup-e2e-runbook/SKILL.md.tmpl
templates/e2e/.github/skills/worker-dev/SKILL.md.tmpl
templates/e2e/.github/skills/worker-fix/SKILL.md.tmpl
templates/e2e/.github/skills/worker-test/SKILL.md.tmpl
templates/e2e/AGENTS.md.tmpl
tests/integration/bug001-runtime.bats
tests/integration/resilience.bats
tests/integration/test_bug001_public_cli.py
tests/skills/lint-skills.sh
tests/skills/test_bug001_docs.py
tests/transport/tmux
tests/unit/cmd-control-lifecycle.bats
tests/unit/cmd-control-mission.bats
tests/unit/cmd-control-preflight.bats
tests/unit/cmd-evidence-boundary.bats
tests/unit/cmd-overseer-recovery.bats
tests/unit/cmd-overseer-tick.bats
tests/unit/cmd-protocol.bats
tests/unit/cmd-wake-migrate.bats
tests/unit/cmd-wake.bats
tests/unit/helper.bash
tests/unit/py-tests.bats
tests/unit/test_bug001_runtime.py
tests/unit/test_control_wake_seam.py
tests/unit/test_dispatch_acceptance.py
tests/unit/test_wake_runtime.py
```
