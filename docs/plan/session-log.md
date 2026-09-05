# Session Log — copilotcockpit

- 2026-09-05T13:32:10+01:00 — TH3.E3.US1 DONE: Review 1 REQUEST_CHANGES fixed in rework 1/2 and Review 2 APPROVED. `cockpit-overseer tick` is a new bounded controller pass that validates the control and queue roots, replays the committed journal, reads the active queue item and folded worker and mission state, applies the ADR-014 ladder rule by rule, and selects at most one state-changing action that it persists before exiting; the one-action invariant is structural at three layers, since a frozen `ControllerAction` can express only one action, `ControllerTick._commit` refuses a second call, and `apply_controller_dispatch` reserves the single mission slot inside the deterministic fold so even raw `publish-event` records `second-active-slot` or `reused-mission-id` instead of creating a second mission. Precedence is enforced by construction rather than convention because `select_controller_action` receives only a `ControllerEvidence` record carrying rules one to six and has no parameter through which live status or pane text could reach a decision, so a pane forging an available prompt or a whole dispatch line is reported as `advances-state no` and never dispatches a second mission; mission, command, and trace identifiers are `uuid5` derivations of the control identity, queue item, worker, and dispatch attempt, so concurrent ticks mint the same command and redelivery is a retained duplicate rather than a conflict. A tick with no valid action commits one `controller-observation-<outcome>` event keyed by a digest of the exact reconciled evidence and collapses to no action on the next identical tick at both the decision and the fold layer, so a blocker is recorded once instead of re-investigated. The rework closed a real concurrency defect in which the tick pre-read the derived ledger outside the control lock and turned the ordinary architecture 8.3 window between event commit and projection replacement into a hard blocked verdict with a permanent bogus conflict event: the classification vocabulary is now partitioned into genuinely divergent reasons that still refuse with the `replay-ledger` hint and a merely-behind reason that falls through to a decision folded from committed events, and any unsettled reading is re-read exactly once under the existing portable control lock, which commits nothing and is released before any action is chosen. It also covered the previously untested `worker-busy`, `multiple-active-queue-items`, `queue-root-undeclared`, and `no-queue-root-declared` branches whose absence had let a whole worker-state guard be deleted with the suite still green, and corrected refusals about the queue root to name `COCKPIT_QUEUE_ROOT` while leaving all twenty pre-existing control-root call sites byte-identical. Across both reviews the reviewer independently reproduced the pre-fix failure against a rebuilt module as a measured control, ran barrier-synchronised bursts at eight, sixteen, and thirty-two processes with every tick exiting zero and exactly one dispatch, proved the locked re-read never holds the lock while deciding or committing and stays lock-free on the settled path, confirmed byte-deterministic cold-restart replay after total derived-state loss and byte-inert `--dry-run`, and killed twenty-nine of thirty-two mutations across the two rounds with every survivor proven non-semantic or structurally unreachable. Focused 29/29 plus overseer 3, control 36, lifecycle 14, command 12, mission 19, preflight 19, queue 9, doctor 3, install 2, and uninstall 4, and the full gate 195 passed with 0 failed across all five categories with doctor and the three dry-run surfaces green. No existing schema version was bumped and the new controller family is additive. Stale-worker recovery and the conflict-outcome matrix (US2), the escalation ladder (US3), and all E4 wake and lease and E5 migration work remain out of scope; the tick is deliberately shaped so a wake lease can wrap it later without changing how a decision is reached. — copilotcockpit
- 2026-09-05T11:38:21+01:00 — TH3.E3.US1 REVIEW 1 REQUEST_CHANGES / REWORK 1/2: the reviewer independently reproduced every acceptance criterion and BDD scenario on its own scratch roots, confirmed the structural exclusion of ADR-014 rules 7 and 8 from the decision function, the three-layer one-action invariant, byte-deterministic cold-restart replay, and fail-closed behaviour against oversized integers, deep recursion, symlinked stores, and newline-forged pane and window text, and killed fifteen of sixteen of its own mutations, but blocked on three defects: the tick pre-reads the derived `ledger.json` outside the control lock, so the ordinary self-healing window in which a concurrent writer has committed its event but not yet replaced the projection is classified as divergent and turned into a hard blocked verdict with exit 1 and a permanent bogus conflict event whose state key differs every time, which contradicts AC1 and AC3, the story's own new exit-code contract, architecture sections 11 and 17, and the TH3.E2 invariant that ordering comes from the committed revision rather than a pre-read; the `worker-busy` decision branch is entirely uncovered and a mutation deleting it survives the full suite while the exploit commits a second dispatch that fails to claim the slot, and three further reason branches including two blocking refusals have no coverage; and a bad `COCKPIT_QUEUE_ROOT` is refused with a diagnostic naming `COCKPIT_CONTROL_ROOT`, sending the operator to the wrong variable. Focused 22/22, unit 183/183, and the full gate exit 0 across all five categories with doctor and the three dry-run surfaces green, so this is review rework rather than a build or test failure and no troubleshooter is involved; the story remains in-progress. — copilotcockpit
- 2026-09-04T22:30:21+01:00 — TH3.E3.US1 START: one-action controller tick and evidence precedence; dependencies TH3.E1.US7 and TH3.E2.US2 are both done and TH3.E1 and TH3.E2 are both done at 673761730d924421ac69de00978b268e36c3aef7 with a clean tree on `feature/TH3-holistic-cockpit-control-plane`. TH3.E3 moves from todo to in-progress with this first story. Scope is limited to a bounded `cockpit-overseer tick` that validates the control and queue roots, replays new events, reads the active queue item and structured worker state, applies the ADR-014 precedence order over conflicting evidence, selects at most one state-changing action, persists it, and exits, plus a no-action tick that durably records its observation or escalation state and terminates without repeated investigation. Stale-worker recovery and the full conflict reconciliation table (US2), the queue-linked bounded escalation ladder (US3), and all E4 wake scheduling, lease acquisition, and evidence-boundary work and E5 compatibility and end-to-end proof remain out of scope. gitflow-operator `status` and `commit` are supported and in use for evidence and commits; its `branch-from-develop`, `create-merge-request`, and `squash-merge-to-develop` commands require a `develop` branch that does not exist in this main-based repository and remain explicitly incompatible and unused rather than silently substituted. — copilotcockpit
- 2026-09-04T18:20:05+01:00 — TH3.E2 EPIC CEREMONY DONE (3 stories): US1 at d493be6e35ffa4b14c35690c8c2aae437f1f220d, US2 at 5ba0b71d37d907c3e465934584450ffe92b6cb41, and US3 at 2e2bede650c5546970e4e09b53a05ebf990f993b are all reviewer-APPROVED, each after exactly one bounded rework that closed a security or test-integrity defect rather than a functional one. The `epic-integration` session ran the full gate green (166 ok, 0 failures: 161/161 unit, template integrity, 8/8 skills, 5/5 integration, codex) plus `doctor`, `global --dry-run`, `codex-repo --dry-run`, `codex-global --dry-run`, and a side-effect-free `e2e --yes --dry-run`, and exercised the three stories together on scratch control roots through a complete dispatch-to-completion mission with a managed question, the cancellation and replacement obsolescence path, twelve adversarial cross-layer interleavings, total derived-state loss with byte-deterministic replay, and fifteen-process concurrency. It closed three genuine cross-story defects that existed only between the layers — another worker's lifecycle event could claim a replacement-reserved mission and produce two claimed slots naming one mission with no conflict recorded and an unreleasable reservation, a reserved slot's queue item could be silently re-pointed against the committed replacement record, and a dialog could outlive its mission while `mission-status` still advertised it as answerable — with a `_mission_slot_claim_fault` guard placed in the deterministic fold that no surface including raw `publish-event` can bypass, and with render-time dialog observations that rewrite no committed record and leave replay bytes unchanged, each covered by a new regression proven to fail against the pre-fix build. The epic quality check re-verified all nine acceptance criteria, independently reproduced every defect on an isolated pre-fix module, killed seven of eight mutations of the fixes, ran twelve concurrency rounds and 320 randomized cross-layer fuzz steps against eight invariants, verified the migration note that a pre-fix store is reported as advisory derived drift and healed non-destructively by `replay-ledger`, adjudicated all five reported follow-ups as genuinely E3/E4/E5, and APPROVED. The epic completion gate is met. TH3.E2 is marked done; TH3 remains in-progress and unlocked, and TH3.E3 has not been started. gitflow-operator `status` and `commit` were used for all evidence and commits; its `branch-from-develop`, `create-merge-request`, and `squash-merge-to-develop` commands require a `develop` branch that does not exist in this main-based repository and are recorded as explicitly incompatible and unused. — copilotcockpit
- 2026-09-04T17:05:45+01:00 — TH3.E2.US3 DONE: Review 1 REQUEST_CHANGES fixed in rework 1/2 and Review 2 APPROVED. Mission questions, replies, and access-prompt responses are now first-class commands whose correlated `mission-dialog` record rides inside the `command-registered` event of its own envelope under a both-halves contract, with response kinds derived from the prompt, admission refusing prompts and answers that do not name a materialized correlated active mission, generic `register-command` refusing the managed types, and question and answer bodies never persisted anywhere in the control root; cooperative cancellation carries an always-explicit acknowledgement deadline whose outcome is a byte-inert observation over committed evidence and one explicit as-of, distinguishing `acknowledged`, `awaiting-acknowledgement`, and `timed-out` with a recoverable reason that never claims failure and never runs a timer or daemon; and replacement terminates the prior mission with the existing `replaced` state and `superseded_by_mission_id`, reserves exactly one new mission ID per worker in a new `mission_slots` projection, and durably records `second-active-slot`, `unmatched-mission-slot`, and `reused-mission-id` conflicts instead of ever creating a second active slot. The rework closed three surviving-mutation coverage gaps around mission-ID reuse, the duplicate-acknowledgement exclusion, and the envelope-without-record half of the contract, and corrected conflict diagnostics to be reason-accurate. Across both reviews the invariant survived twenty real-process concurrent replacement and lifecycle races with exactly one claimed slot every time, cold restarts answered from committed events after all derived state was deleted, and every declared mutation plus nine reviewer-authored ones were killed. Focused 17/17 plus US2 12/12, US1 14/14, and E1 36/36, 19/19, 10/10, and the full gate 164 passed with 0 failed across all five categories. US1 lifecycle, US2 command, and control schema versions all remain at 1 and no existing suite was modified. The legacy Go `cockpit-protocol` temp-file question path is deliberately retained for additive ADR-017 compatibility and belongs to E5 migration. TH3.E2 small-epic ceremony follows. — copilotcockpit
- 2026-09-04T16:10:35+01:00 — TH3.E2.US3 REVIEW 1 REQUEST_CHANGES / REWORK 1/2: the reviewer independently exploited every AC1 correlation refusal, proved the cancellation timeout is a byte-inert observation that never claims failure, and attacked the single-active-slot invariant with eleven real-process concurrent replacement and lifecycle races that always resolved to exactly one claimed slot, finding no production defect, but blocked on three surviving mutations that leave declared guarantees unenforced: the mission-ID reuse guard can be removed while every suite stays green even though the exploit produces real mission bleed across two workers, the `duplicate`-acknowledgement exclusion can be removed so a redelivered cancellation would be reported as acknowledged when it actually timed out, and only the record-without-envelope half of the half-contract is tested so an envelope declaring a managed command type with no correlated record could commit. Focused 16/16, US2 12/12, US1 14/14, E1 65/65, and the full gate 163 passed with 0 failed, so this is review rework rather than a build or test failure and no troubleshooter is involved; the story remains in-progress. — copilotcockpit
- 2026-09-04T15:00:15+01:00 — TH3.E2.US3 START: managed questions, cancellation, and replacement; dependency TH3.E2.US2 is done at 5ba0b71d37d907c3e465934584450ffe92b6cb41 with a clean tree. Scope is limited to carrying mission questions, replies, and access-prompt responses as protocol commands correlated to the active mission instead of unscoped temporary files or pane input, cooperative cancellation with a durable observable acknowledgement or timeout outcome, and replacement that terminates the prior mission as `replaced`, mints a new mission ID, and refuses and records a conflict when a second active slot would be created for the same worker. All E3 controller and reconciliation work, E4 wakes, leases, escalation, and boundary enforcement, and E5 migration and end-to-end proof remain out of scope. This is the final TH3.E2 story; the small-epic ceremony follows. — copilotcockpit
- 2026-09-04T13:58:20+01:00 — TH3.E2.US2 DONE: Review 1 REQUEST_CHANGES fixed in rework 1/2 and Review 2 APPROVED. State-changing worker operations now persist a versioned closed-field command envelope carrying command ID, mission and queue-item IDs, target kind and ID, trace and parent-trace IDs, a canonical `sha256` payload digest, declared ADR-016 boundaries, and timestamps, published as an immutable committed event with the payload body never stored; accepted, applied, rejected, and duplicate acknowledgements are committed events folded into `ledger.commands` under a closed `registered -> accepted -> applied` and `rejected` order where `duplicate` never advances, and remain answerable from `events/` alone after every derived artefact is deleted; redelivering the same ID and digest returns the stored result without re-applying while a different digest is durably recorded as a conflict and refused. The rework replaced an uncaught `ValueError` traceback on oversized JSON integers with fail-closed diagnostics and extended the same guard to five further parse sites reachable from the new read path, and converted ten `set -e`-vacuous bats negations to errexit-honouring helpers. The reviewer re-exploited every attack independently, proved the old assertions vacuous and the new ones load-bearing by re-applying the `leak-reason` mutation, killed nine of ten mutations including two of its own with the sole survivor traced to an unread field, and adjudicated the CLI-only boundary docstring as correct because read-side enforcement would wedge a relocated store and belongs to E4. Focused 12/12 plus lifecycle 14/14 and E1 36/36, 19/19, 10/10, and the full gate 147 passed with 0 failed across all five categories. Managed questions, cancellation, and replacement remain out of scope. Sixteen pre-existing vacuous negations in sibling suites and an E1 `publish-event` non-finite-number acceptance are recorded as non-blocking follow-ups. — copilotcockpit
- 2026-09-04T13:05:10+01:00 — TH3.E2.US2 REVIEW 1 REQUEST_CHANGES / REWORK 1/2: the reviewer independently proved the idempotency core correct under real-process concurrency, a crash injected between event commit and projection update, a cold restart with all derived state destroyed, and key-order, whitespace, and unicode-escaping canonicalization probes, and killed eleven of twelve production mutations, but blocked on two defects: a JSON integer literal longer than the CPython 4300-digit limit makes `json.loads` raise a bare `ValueError` in `_parsed_command_payload`, escaping the fail-closed contract with a traceback and leaked absolute paths on both new subcommands, and ten `!`-negated bats assertions are vacuous under `set -e` (SC2314), leaving the free-text record-forgery guarantee unenforced as demonstrated by a surviving `leak-reason` mutation that emits a fully-formed spoofed command line while the suite still passes. Focused 12/12, lifecycle 14/14, E1 65/65, and the full gate 142/142 unit, 5/5 integration, template, 8/8 skills, and codex all pass, so this is review rework rather than a build or test failure and no troubleshooter is involved; the story remains in-progress. — copilotcockpit
- 2026-09-04T12:16:40+01:00 — TH3.E2.US2 START: idempotent command envelopes and acknowledgements; dependency TH3.E2.US1 is done at d493be6e35ffa4b14c35690c8c2aae437f1f220d with a clean tree. Scope is limited to durable command envelopes carrying command ID, mission ID, target, trace and parent-trace IDs, schema version, payload digest, and declared boundaries; persisted accepted, applied, rejected, and duplicate acknowledgements that remain queryable after a controller restart; and deterministic idempotent redelivery where the same command ID with the same digest returns the stored result without re-applying and the same ID with a different digest is refused and recorded as a conflict. Managed questions, access-prompt responses, cancellation and replacement with a single active mission slot (US3), and all E3 reconciliation, E4 wake and evidence-boundary, and E5 migration work remain out of scope. gitflow-operator `status` and `commit` remain supported and in use; its develop-based branch and merge-request commands remain explicitly incompatible with this main-based repository and are unused. — copilotcockpit
- 2026-09-04T12:05:30+01:00 — TH3.E2.US1 DONE: Review 1 REQUEST_CHANGES fixed in rework 1/2 and Review 2 APPROVED. Workers now emit versioned `worker-lifecycle-<state>` events for all seven states as ordinary immutable committed events carrying mission, queue-item, worker, trace, and parent-trace identifiers under a closed validated field set; `fold_worker_missions` materializes `ledger.worker_missions` only for correlated, non-terminal, strictly-greater-sequence, architecture section 9 transitions, so late, duplicate, uncorrelated, and post-terminal events stay durable audit evidence without regressing state; and lock-free read-only `lifecycle-status` derives freshness from committed events, reporting an expired `fresh_until` as `stale`/`heartbeat-expired`/`awaiting-bounded-recovery` where `stale` is not a lifecycle state and can never read as failure. The rework closed a record-forgery hole by refusing non-printable or whitespace `worker_id` and `queue_item_id` before any lock or candidate exists, replaced uncaught `OverflowError` and oversized-integer tracebacks with fail-closed diagnostics, and reverted the unvalidated `capabilities.worker_lifecycle` declaration to keep E5 migration surface out of scope. The reviewer re-exploited every original attack, proved prose and evidence fields cannot forge a record boundary on any stdout path, and killed all 13 mutations including the two that previously survived. Focused 14/14 plus E1 36/36, 19/19, and 10/10, and the full gate 130/130 unit, 5/5 integration, template, 8/8 skills, and codex all pass in about 2m49s. Command envelopes, acknowledgements, managed questions, cancellation, and replacement remain out of scope. A pre-existing `--actor`/`--type` newline injection into the `list-events` renderer, reproduced at baseline, is recorded as the top non-blocking follow-up. — copilotcockpit
- 2026-09-04T11:18:40+01:00 — TH3.E2.US1 REVIEW 1 REQUEST_CHANGES / REWORK 1/2: the reviewer independently re-executed all three acceptance criteria and BDD scenarios on scratch stores and killed 18 of 20 production mutations, but found three blocking defects: worker-controlled `worker_id` and `queue_item_id` accept newlines and control characters and are rendered into the line-oriented `lifecycle-status` payload, letting an untrusted worker forge a whole spoofed terminal status record; a large `--fresh-for` raises an uncaught `OverflowError` that escapes the fail-closed diagnostic contract with a traceback and leaked paths; and `capabilities.worker_lifecycle` is newly written into `control.json` but validated by nothing and directly contradicted by the preflight worker-capability report. Focused 12/12, E1 65/65, and the full gate 128/128 unit, 5/5 integration, template, 8/8 skills, and codex all pass, so this is review rework rather than a build or test failure and no troubleshooter is involved; the story remains in-progress. — copilotcockpit
- 2026-09-04T10:42:10+01:00 — TH3.E2.US1 START: structured worker lifecycle and freshness; dependency TH3.E1.US7 is done and TH3.E1 is done at 726dc754030639b4267be0fe6fc2d63b8171bcda with a clean tree on `feature/TH3-holistic-cockpit-control-plane`. Scope is limited to versioned worker lifecycle events (accepted, running, blocked, completed, failed, cancelled, replaced) carrying mission and trace identifiers, monotonic sequence handling so late or lower-sequence events are retained for audit but never regress a terminal or newer materialized state, and heartbeat freshness where `fresh_until` expiry yields a distinct recoverable stale observation that never claims mission failure. Idempotent command envelopes and acknowledgements (US2), managed questions/cancellation/replacement (US3), and all E3 reconciliation, E4 wake and evidence-boundary, and E5 migration work remain out of scope. gitflow-operator `status` and `commit` are supported and used for evidence; its `branch-from-develop`, `create-merge-request`, and `squash-merge-to-develop` commands require a `develop` branch that does not exist in this main-based repository, so they are explicitly incompatible and unused rather than silently substituted. — copilotcockpit
- 2026-09-03T23:12:45+01:00 — TH3.E1 EPIC CEREMONY DONE (large epic, 7 stories): all of US1-US7 are done and reviewer-APPROVED. The `epic-integration` session ran the full gate green (116/116 unit, 5/5 integration, template, 8/8 skills, codex, about 2m50s), plus `bootstrap.sh doctor` and `global --dry-run`, and exercised the epic end to end on a scratch store covering healthy publication and replay, crashed-writer recovery through preflight-named repairs, killed-owner lock recovery, derived ledger and view rebuild, and composed fail-closed refusals; it closed one genuine cross-story gap with a deterministic crashed-writer chain regression in `tests/unit/cmd-control-preflight.bats` and reported the `repair-lock` root-metadata independence deviation without changing protocol. The epic quality check verified all 21 acceptance criteria against final HEAD, confirmed the section 8.4 matrix gate reads the architecture document as its source of truth, ran adversarial symlink, event-forgery, and schema-poisoning probes, adjudicated the reported deviation as lossless, recoverable, and consistent with ADR-018 layering, and APPROVED with seven non-blocking follow-ups recorded in the changelog. TH3.E1 is marked done; TH3 remains in-progress and TH3.E2 has not been started. — copilotcockpit
- 2026-09-03T22:40:20+01:00 — TH3.E1.US7 DONE: Review 1 REQUEST_CHANGES fixed in rework 1/2 and Review 2 APPROVED. `preflight` reports all eleven readiness dimensions and was independently proven byte, inode, and ctime inert across many damaged stores, findings separate authoritative corruption from derived, debris, and configuration classes and name the exact repair command, and `repair-store` only ever quarantines into `quarantine/<stamp>-<uuid>-<name>`, stays idempotent, and refuses live owners, future-versioned state, and unproven committed continuity. The rework made `repair-store --dry-run` share the real run read-only refusal gates while staying lock-free, verified by mutation testing and thirteen additional parity probes; one narrow externally triggered quarantine-type parity gap and an architecture section 7 layout note are recorded as non-blocking follow-ups. Focused 36/36, 10/10, 18/18 bats plus the 17-entry matrix gate, and full gate 115/115 unit, 5/5 integration, template, 8/8 skills, and codex all pass in about 2m10s. TH3.E1 large-epic ceremony follows. — copilotcockpit
- 2026-09-03T22:07:30+01:00 — TH3.E1.US7 REVIEW 1 REQUEST_CHANGES / REWORK 1/2: preflight was independently proven read-only across twelve additional damaged stores and every named repair action was verified sufficient, but `repair-store --dry-run` skips the future-versioned committed event and unproven committed-event continuity refusals that the real run enforces, so it previews a repair the tool will refuse. Focused 62/62 and full gate 113/113 unit, 5/5 integration, template, skills, and codex all pass, so this is review rework rather than a build or test failure and no troubleshooter is involved; the story remains in-progress. — copilotcockpit
- 2026-09-03T21:36:10+01:00 — TH3.E1.US7 START: control-plane preflight and guarded repair diagnostics; dependency TH3.E1.US6 is done at 6642144c314fc6d8bc7e03e158a0b453203b5135 with a clean tree. Scope is limited to read-only preflight over root, schema, transition guard, authoritative lock, quarantines, pending events, committed revisions, ledger, queue, worker capability, and declared paths, diagnosis that separates authoritative corruption from safe private or quarantined debris and names the exact explicit repair action, and guarded repairs that back up or quarantine, stay idempotent, and refuse ambiguous or future-versioned state without mutation. This is the final TH3.E1 story; the large-epic ceremony follows. — copilotcockpit
- 2026-09-03T21:33:50+01:00 — TH3.E1.US6 DONE: Review 1 APPROVED with no blocking issues. All nine architecture 8.4 interruption boundaries and all six required interleavings are now executable, registered in `tests/unit/control-interruption-matrix.tsv`, and each proves safety plus post-crash liveness; `tests/unit/check-interruption-matrix.sh` parses section 8.4 directly, fails on drift in both directions, and mechanically rejects sleep-only coordination. The reviewer ran four independent production mutations (hook placement, pending-then-rename event atomicity, and shared-versus-exclusive guard) and each was caught, confirming the proofs are real rather than theatre; the only recorded non-blocking limitation is that the static gate can still be satisfied by comment keyword stuffing, backstopped by the doc-anchored check and the pinned entry count. Focused 36/36 plus 10/10 bats and the gate itself, full gate 97/97 unit, 5/5 integration, template, skills, and codex all pass in about 1m41s. Preflight remains out of scope and no earlier assertion was weakened. — copilotcockpit
- 2026-09-03T21:05:40+01:00 — TH3.E1.US6 START: deterministic crash-consistency and interleaving proof; dependencies TH3.E1.US3 and TH3.E1.US5 are done at 75bdce58ba2ef77eb8310dd06c3aaf6d33c7f2b5 with a clean tree. Scope is limited to completing executable fault-hook coverage for every architecture 8.4 interruption boundary, barrier-coordinated process-level acquire/acquire, acquire/repair, release/acquire, repair/acquire, bounded timeout, and replacement-preservation regressions, and a deterministic gate that rejects sleep-only coordination; each scenario must prove both safety and subsequent liveness. Preflight and guarded repair diagnostics remain out of scope, and no protocol requirement may be weakened to make a test pass. — copilotcockpit
- 2026-09-03T21:03:15+01:00 — TH3.E1.US5 DONE: Review 1 APPROVED with no blocking issues (two non-blocking US7 candidates recorded). `ledger.json` is now a pure byte-reproducible fold of the contiguous committed events, written through a flushed and byte-verified `ledger.json.tmp` and installed by `os.replace` under the already-held control lock without touching event authority, and `replay-ledger` deterministically repairs missing, corrupt, stale, ahead, or divergent projections exactly once while `events.jsonl` becomes a strictly derived view. The reviewer adjudicated the non-object payload rejection, derived `ledger_id`/`updated_at`, empty projection at init, and the strengthened US4 test assertions as in scope and non-breaking, verifying a pre-US5 store still validates and self-heals. Focused 36/36 bats, full gate 87/87 unit, 5/5 integration, template, 8/8 skills, and codex all pass. The full crash matrix and preflight remain out of scope. — copilotcockpit
- 2026-09-03T20:33:40+01:00 — TH3.E1.US5 START: materialized ledger projection and replay; dependency TH3.E1.US4 is done at 29a22e241339fb2db2685962f0a43111175a4c4e with a clean tree. Scope is limited to deriving `ledger.json` solely from the contiguous committed event sequence, flushing and atomically replacing it through `ledger.json.tmp` after each commit without changing event authority, and deterministically rebuilding a missing, stale, corrupt, or interrupted projection exactly once. The full crash/interleaving matrix and preflight diagnostics remain out of scope. — copilotcockpit
- 2026-09-03T20:31:05+01:00 — TH3.E1.US4 DONE: Review 1 APPROVED with no blocking issues (four non-blocking observations recorded). Event publication now runs entirely under the held control lock, writes one private `pending/` candidate, flushes it, validates the flushed bytes against the exact canonical serialization, and commits with a single non-clobbering atomic rename into `events/<12-digit revision>-<event-id>.json`; discovery fails closed and names the offender for gaps, duplicate revisions or event IDs, filename/record disagreement, foreign control IDs, future schemas, and irregular entries, and pending debris is reported but never deleted. The reviewer deliberately adjudicated the new required `events/` and `pending/` directories as spec-conformant and in scope rather than an ADR-017 incompatibility. Focused 31/31 bats, full gate 82/82 unit, 5/5 integration, template, 8/8 skills, and codex all pass. Ledger projection/replay, the full crash matrix, and preflight remain out of scope. — copilotcockpit
- 2026-09-03T20:09:20+01:00 — TH3.E1.US4 START: immutable authoritative event publication; dependency TH3.E1.US2 is done and TH3.E1.US3 is done at 8f88a218e396e5f6bf57b8c75d61db73bf300d4c with a clean tree. Scope is limited to per-event private pending write, flush, exact serialized validation, and a single atomic rename into `events/`, plus deterministic committed revision identity and fail-closed handling of pending debris, duplicate revisions, gaps, and invalid committed events. Ledger projection/replay materialization, the full crash matrix, and preflight diagnostics remain out of scope. — copilotcockpit
- 2026-09-03T20:06:40+01:00 — TH3.E1.US3 DONE: Review 1 APPROVED with no blocking issues (three non-blocking suggestions recorded, no rework needed). Guarded repair now observes, authorizes, and renames entirely under the same fcntl `locks/control.guard` used by acquisition, proves same-host owner death via ESRCH only, accepts explicit exact-owner-UUID authorization for unprovable cases, refuses live owners even when authorized, and claims repair solely by atomic rename of the exact lock to a unique `control.lock.repaired-<lock_id>-<uuid4>` quarantine with no shared marker and no deletion. Focused 24/24 bats, full gate 75/75 unit, 5/5 integration, template, 8/8 skills, codex, and py_compile all pass. Events, projection/replay, the full crash matrix, and preflight remain out of scope. — copilotcockpit
- 2026-09-03T19:47:10+01:00 — TH3.E1.US3 START: recoverable guarded stale-lock repair; dependency TH3.E1.US2 is done at 207f922ae68e190d302722b99a5f20e5f4a5f217 with a clean tree. Scope is limited to guarded repair holding the same fcntl transition guard as acquisition, positive same-host owner-death proof or explicit guarded authorization, atomic exact-lock rename to a unique quarantine with no shared repair marker, and recoverable interruption boundaries; events, projection/replay, the full crash matrix, and preflight remain out of scope. gitflow-operator status works in this checkout and is used for evidence, but its develop-only branch/MR commands are incompatible with this main-based repo, so repo-specific git covers unsupported operations. — copilotcockpit
- 2026-09-03T19:43:23+01:00 — REVISED TH3.E1.US2 DONE: Review 1 REQUEST_CHANGES fixed in rework 1/2; Review 2 APPROVED. Focused 17/17, full 68/68 unit, 5/5 integration, template integrity, 8/8 skills, Codex, Python compile, and diff check all pass. Implemented process-scoped fcntl guard, atomic complete candidate publication, and exact UUID+filesystem identity quarantine release/unwind; later stale repair/events/projection/crash matrix/preflight excluded. gitflow-operator unavailable (no executable) and its develop-only flow incompatible with this main-based repo, so repo-specific git is the explicit supported exception. — copilotcockpit
- 2026-09-03T19:32:35+01:00 — REVISED TH3.E1.US2 REVIEW 1 REQUEST_CHANGES / REWORK 1/2: same-UUID/new-inode replacement can be deleted during acquisition unwind; catchable post-publication guard-release errors can leak the exact caller lock; repeated EINTR can exceed the guard deadline. Focused 14/14 and full 65 unit + 5 integration + template/skills/Codex passed, so this is review rework, not a troubleshooter/build failure; story remains in-progress. — copilotcockpit
- 2026-09-03T19:13:44+01:00 — REVISED TH3.E1.US2 START: portable lock acquisition and exact release; dependency US1 done; fresh post-replan review budget. Unapproved working-tree code in `bin/cockpit_control.py` and `tests/unit/cmd-control.bats` must be reduced/refactored to strict US2 scope; stale repair/event/projection/crash-matrix/preflight excluded except required US2 interfaces. — copilotcockpit
- 2026-09-03T19:06:00+01:00 — TH3 ARCHITECTURE / PLANNING REVISION: paused implementation after US2 concurrency reviews; added ADR-018 for kernel-guarded atomic lock publication and quarantine repair, ADR-019 for immutable per-event publication and projection replay, expanded the deterministic crash/interleaving matrix, and split E1 from 3 into 7 stories. US1 remains done; revised US2-US7 are todo while the rejected implementation diff is preserved for selective reuse. — copilotcockpit
- 2026-09-03T19:03:02+01:00 — TH3.E1.US2 PLANNING-REVISION HOLD / HUMAN-DIRECTED PAUSE: recovered failed -> in-progress as the valid status fallback and paused at the current safe boundary. No implementation may resume until architecture enrichment and story replanning are approved; existing uncommitted US2 work and review evidence remain intact. — copilotcockpit
- 2026-09-03T19:01:57+01:00 — TH3.E1.US2 FAILED / NEW HUMAN ESCALATION: the one authorized exceptional review returned REQUEST_CHANGES. A crash after repair claim publication but before archive rename can still leave an unrecoverable claimed lock; stale repair-marker cleanup has a final identity-check-to-unlink replacement race; deterministic regressions do not cover claim-to-rename interruption, marker replacement during cleanup, or partial marker/journal publication. The exceptional developer rework and review authorization is exhausted, so no further rework was launched. Unapproved US2 work remains uncommitted for recovery. — copilotcockpit
- 2026-09-03T19:01:57+01:00 — TH3.E1.US2 EXCEPTIONAL REVIEW REQUEST_CHANGES: scoped tests passed (21/21 Bats, Python compilation, diff check), but the remaining lock-repair crash and TOCTOU windows violate interruption recovery and exact-identity safety. Escalated without exceeding authorization. — copilotcockpit
- 2026-09-03T18:44:50+01:00 — TH3.E1.US2 CRASH RECOVERY / EXCEPTIONAL REWORK START: backlog-authoritative failed state recovered to in-progress under explicit human authorization for exactly one additional developer rework and subsequent review. Scope is limited to interruption-safe repair-marker recovery, safe acquisition unwind of a writer-created lock, exclusion of live lock creation-to-owner publication from repair even at stale threshold zero, deterministic crash/interleaving regressions for all three windows, and focused/full tests. Preserving uncommitted US2 work on feature/TH3-holistic-cockpit-control-plane at approved US1 HEAD 677fc7e83e713188d8a1003aca77b9a168f115ac. gitflow-operator is unavailable in this checkout and its develop-based flow is incompatible with this main-based repository; repo-specific git/gh remains the recorded exception for unsupported operations. — copilotcockpit
- 2026-09-03T17:42:27+01:00 — TH3.E1.US2 FAILED / HUMAN ESCALATION: mandatory REVIEW 2 found the repair marker can permanently wedge the store after interruption, acquisition timeout can leak its own lock, and guarded repair can still archive a live writer in the mkdir-to-owner-publication window. Both allowed review/rework iterations are exhausted; no troubleshooter was used because this is review feedback rather than a build/test failure. Unapproved US2 changes remain uncommitted for recovery. — copilotcockpit
- 2026-09-03T17:42:27+01:00 — TH3.E1.US2 REVIEW 2 REQUEST_CHANGES: add exact-identity stale repair-marker recovery and safe acquisition unwind, plus a portable handshake excluding repair from the live lock creation-to-owner-publication window; add deterministic crash/interleaving tests. Bounded rework limit reached. — copilotcockpit
- 2026-09-03T17:42:27+01:00 — TH3.E1.US2 REVIEW 1 REQUEST_CHANGES: guarded lock repair has a TOCTOU race that can archive a newly acquired live lock, and projection updates are not fully represented in authoritative events across interruption; add deterministic concurrent mutation/repair/reacquisition and post-event-flush fault regressions. Story remains in-progress for bounded rework 1/2. — copilotcockpit
- 2026-09-03T17:42:27+01:00 — TH3.E1.US2 START: Atomic event journal and portable locking; dependency TH3.E1.US1 is done. — copilotcockpit
- 2026-09-03T17:42:27+01:00 — TH3.E1.US1 DONE: exceptional third rework fixed generated cockpit-wake jobs to stop before every tmux mutation when control-store validation or inbox writing fails; generated-job execution regressions cover missing, malformed, future-versioned, relative-root, and inbox-write failures. Developer focused tests 18/18 and full gate 60 unit + 5 integration + template/skills/Codex PASS; exceptional review APPROVED; bin/__pycache__/ removed. Commit 677fc7e83e713188d8a1003aca77b9a168f115ac. — copilotcockpit
- 2026-09-03T17:42:27+01:00 — TH3.E1.US1 CRASH RECOVERY / EXCEPTIONAL REWORK START: backlog-authoritative failed state recovered to in-progress under explicit human authorization for one exceptional third developer rework and subsequent review. Scope is limited to fail-closed generated cockpit-wake scheduled jobs for missing, malformed, and future-versioned control stores, execution regressions for each invalid state, removal of bin/__pycache__/, focused/full tests, and review. Gitflow operator status is unavailable because bin/gitflow-operator is absent; its develop-based branch/PR flow is also incompatible with this main-based repository, so repo-specific git/gh will be used only where the operator cannot operate. — copilotcockpit
- 2026-09-03T16:34:34+01:00 — TH3.E1.US1 FAILED / HUMAN ESCALATION: mandatory REVIEW 2 found generated cockpit-wake scheduled-job scripts continue tmux mutation after control-store validation fails. The two-review bounded rework limit is exhausted; no troubleshooter was used because this is review feedback rather than a build/test failure. — copilotcockpit
- 2026-09-03T16:34:34+01:00 — TH3.E1.US1 REVIEW 1 REQUEST_CHANGES: control-schema validation must fail closed for canonical roots/active mission IDs; cockpit-protocol and cockpit-wake must resolve and validate the common root before mutation. Story remains in-progress for bounded rework. — copilotcockpit
- 2026-09-03T16:34:34+01:00 — TH3.E1.US1 START: Control root and versioned schemas; TH3 and TH3.E1 moved to in-progress. — copilotcockpit
- 2026-09-03T16:20:19+01:00 — Planned TH3 holistic cockpit control plane: 5 epics and 15 BDD stories covering control storage, worker protocol, reconciliation, recurrent wakes, evidence boundaries, compatibility, and resilience validation; archived TH2 issue templates. — copilotcockpit
- 2026-07-31T11:09:14+01:00 — User accepted TH2 delivery checkpoint; locked FIFO overseer request queue theme. — copilotcockpit
- 2026-07-31T11:09:14+01:00 — Implemented TH2 queue runtime: added cockpit-queue CLI, install/uninstall/doctor wiring, queue unit tests, and queue-scoped overseer/e2e-operator runbook updates. — copilotcockpit
- 2026-07-30T20:47:56+01:00 — Advanced TH2 to architecture/planning: updated architecture overview with FIFO queue component model and added TH2 epic issue templates. — copilotcockpit
- 2026-07-30T19:56:41+01:00 — Kickstarted VP2 FIFO overseer request queue; accepted queue-directory persistence ADR; added TH2 with queue operator and E2E clearance stories. — copilotcockpit

A running log of lifecycle ceremonies and significant decisions.

---

## 2026-06-19 — Mission kickoff: overseer/worker AIC slimming

Started a follow-on mission to reduce overseer-worker communication spend by
making tmux comms traceable after the fact.

**Initial changes:**
- `bin/cockpit-overseer` now emits UUID `TRACE-ID` headers on dispatch, archives
  `loop` / `status` / `dispatch` / `reset` events as append-only JSONL, and
  records `trace_id` + `parent_trace_id` for stitching.
- `bin/cockpit-trace` was added to read the archive and stitch dialogs by trace
  UUID or trace family.
- `README.md`, `.github/copilot-instructions.md`, `skills/e2e-cockpit/SKILL.md`,
  and the worker skills now surface the trace protocol and preserve the UUID in
  completion reports.
- Unit and smoke coverage was extended for dispatch UUIDs, trace lookup, and the
  new managed binary.

**Goal:** use the archive to replay prompts, worker snapshots, and AIC signals,
then trim deadweight from the default overseer protocol.

---

## 2026-06-19 — Docs follow-up: tools table for trace + AIC analysis

Added a structured tools table to the README so `cockpit-trace` and the
companion `aic-tracker` tool are visible alongside the cockpit harness, instead
of being buried in prose. Also aligned the overseer skill and repo instructions
to treat `cockpit-trace` as the comms replay tool and `aic-tracker` as the
budget-analysis tool.

---

## 2026-06-16 — Phase 2: Architecture (Architect Agent)

**Input:** VP1 vision brief for `copilotcockpit` — the canonical one-command
bootstrap for the Copilot CLI E2E testing harness (global skills install +
per-project `e2e/` scaffold).

**Reference study:** read the two working harnesses at `ulysses-portal/e2e/` and
`ulysses-index/e2e/` (governed `run-audit.sh` + 3-tier audit trail, Dockerised
Playwright runner, Gherkin test-book, tmux cockpit, local skill overlays), the seven
global skills in `~/.copilot/skills/`, and the `cockpit-wake` script
(`~/.local/bin/cockpit-wake`, a 344-line stdlib Python file).

**Produced:**
- `docs/vision_of_product/VP1-e2e-bootstrap/VP1.md` — product brief.
- `docs/architecture/overview.md` — repo structure, two-phase bootstrap, component
  diagram, parameterisation model, idempotency/update model, tech summary, risks/spikes.
- `docs/ADRs/ADR-001` … `ADR-006` — all Proposed:
  - ADR-001 Skills source-of-truth → vendor skills in `skills/`, copy on install (`--link` for authors).
  - ADR-002 `e2e/` sub-repo → `git init` inside `e2e/` (matches references); `--no-git` opt-out.
  - ADR-003 Entry point → single `bootstrap.sh` dispatcher + `lib/cmd-*.sh` (no CLI framework).
  - ADR-004 Parameterisation → two-tier: scaffold-time tokens (config→prompt→default) + AI skills for topology.
  - ADR-005 `cockpit-wake` → vendor single-file Python script in `bin/`, copy to `~/.local/bin`.
  - ADR-006 Update strategy → `e2e --update` driven by `MANIFEST.toml` (framework/seed/project ownership).
  - ADR-007 Cold install → versioned GitHub Releases tarball + `install.sh` one-liner; `--from-release latest|vX.Y.Z`.
- `docs/plan/backlog.yaml` — VP1 + placeholder TH1 (`not_started`, `locked: false`).

**Open items for Phase 3 (Planning):**
- Decompose TH1 into epics/stories.
- Two spikes flagged: macOS/BSD bash portability; `MANIFEST.toml` glob classification correctness.

**Status:** Architecture complete; ADRs Proposed (none locked). Ready for `/plan-product` step 2.

---

## 2026-06-16 — Architecture addendum: cold install + release CI (Architect Agent)

Added per request:
- `docs/ADRs/ADR-007-github-release-distribution.md` (Proposed) — cold install via a
  versioned GitHub Releases tarball; `install.sh` one-liner; checksum-verified.
- `docs/architecture/overview.md` §8 "CI/CD & releases" — `.github/workflows/release.yml`
  triggers on `v[0-9]+.[0-9]+.[0-9]+` tags, assembles `copilotcockpit-${TAG}.tar.gz`
  (+ `.sha256`), and runs `gh release create --latest`. Sections renumbered (Technology
  choices → 9, Risks → 10, Out of scope → 11); repo tree gains `install.sh`,
  `CHANGELOG.md`, `.github/workflows/release.yml`.
- `docs/plan/backlog.yaml` — ADR-007 registered; TH1 scope notes gain cold-install +
  release-CI items.

**Boundary clarified:** the release CI is for *copilotcockpit itself* and is in scope;
generating CI for a *scaffolded project's* `e2e/` remains out of scope (VP1 §9, arch §11).

---

## 2026-06-16 — Architecture addendum: dev workflow, test strategy & dev skill (Architect Agent)

Added per request:
- `docs/ADRs/ADR-008-copilotcockpit-dev-skill.md` (Proposed) — a new **8th** repo-managed
  skill `copilotcockpit-dev` (in `skills/` + installed globally) encoding the autonomous
  GitOps feature→release flow: branch → local test gate → PR → CI → squash-merge →
  conventional-commit-driven version bump (`VERSION`) → annotated tag → `release.yml`
  (ADR-007). Defines 5 test categories (script unit/bats, template integrity, skills lint,
  integration smoke, release asset validation), the commit→semver mapping, a bounded
  3-attempt escalation policy, and the `gh` CLI commands per step.
- `docs/architecture/overview.md` §9 "Developer workflow & test strategy" — `copilotcockpit-dev`
  skill, `tests/` tree + `run-tests.sh` dispatcher, 5-category test table, ASCII GitOps
  sequence diagram, `VERSION`+`CHANGELOG.md` ownership. Sections renumbered (Technology
  choices → 10, Risks → 11, Out of scope → 12). §2 repo tree gains `tests/`, `run-tests.sh`,
  `VERSION`, `CHANGELOG.md`, `skills/copilotcockpit-dev/SKILL.md`, `.github/workflows/ci.yml`.
  §4 global-install list extended to 8 skills.
- `docs/ADRs/ADR-001-skills-source-of-truth.md` — added a forward-reference note that
  ADR-008 introduces the 8th (contributor) skill; the source-of-truth/install mechanism is
  unchanged. (ADR-001 is Proposed/unlocked — edit permitted.)
- `docs/plan/backlog.yaml` — ADR-008 registered; TH1 scope notes gain dev-skill, test-infra,
  `ci.yml`, and `VERSION`/`CHANGELOG` items.

**Boundary clarified:** ADR-008 owns idea→tag (development discipline of copilotcockpit
itself); ADR-007 owns tag→release (artefact publishing). Contributor deps (`bats`, `gh`)
do not affect end users — `bootstrap.sh` stays dependency-light (P5).

---

## 2026-06-16 — Phase 3: Planning (Product Owner Agent)

**Input:** VP1 vision + architecture overview (§2–§12) + ADR-001…ADR-008 + the TH1
placeholder backlog. Decomposed TH1 — Bootstrap Tooling into epics and hybrid-BDD
user stories.

**Produced:**
- `docs/themes/TH1-bootstrap-tooling/README.md` — theme overview + epic table + sequencing.
- **6 epics / 21 stories** (all `pending`), every story with an "As a…", concrete
  acceptance criteria (exact paths/flags/exit codes), Gherkin BDD scenarios incl.
  edge/error cases, and `## Notes` with ADR references:
  - **E6 — Spikes** (scheduled first): US1 macOS/BSD portability cheat-sheet; US2 `MANIFEST.toml`
    glob-classification matcher. Both XS, output-defined.
  - **E1 — Core bootstrap shell**: US1 `lib/common.sh`; US2 `bootstrap.sh` dispatcher; US3 `cmd-doctor.sh`.
  - **E2 — Global skills install**: US1 vendor 7 harness skills; US2 vendor `cockpit-wake`;
    US3 `cmd-global.sh` (8 skills + cockpit-wake, copy/`--link`/idempotent); US4 cold install
    (`install.sh` + `--from-release`).
  - **E3 — E2E scaffold**: US1–US3 `templates/e2e/` skeleton (runners / governance+test-book /
    overlays+context); US4 `MANIFEST.toml`; US5 `cmd-e2e.sh` scaffold; US6 `--update`.
  - **E4 — CI/CD & releases**: US1 `VERSION`+`CHANGELOG.md`; US2 `release.yml`; US3 `ci.yml`.
  - **E5 — Dev skill & test infra**: US1 `run-tests.sh`+cat-1 bats; US2 cat-2/3/4 suites;
    US3 `copilotcockpit-dev/SKILL.md`.
- **6 GitHub issue templates** at `.github/ISSUE_TEMPLATE/TH1-E<m>-<slug>.md`
  (frontmatter `labels: ["TH1","E<m>"]`, one checkbox per story + link to full stories).
- `docs/plan/backlog.yaml` — `epics: []` placeholder replaced with full epic/story entries
  (`id`, `title`, `status`, `size`, `path`, `depends_on`).

**Sequencing & dependencies:** E6 spikes first (feed E1 `common.sh` + E3 templates/manifest);
E1 (`common.sh`→dispatcher→doctor) gates E2/E3; E5 test suites follow the code they cover and
wire into E4 `ci.yml`; `release.yml` consumes the E3 template tarball contract; `copilotcockpit-dev`
skill follows `release.yml`. Validated: **YAML parses, all 21 story paths exist, dependency graph
is acyclic, no duplicate IDs.** Sizes: 3×XS, 5×S, 13×M (none > L).

**Notes:** No remote/`gh` auth in this environment, so repo labels could not be verified live;
issue templates use the conventional `["TH1","E<m>"]` labels per the planning spec. TH1 is the
first theme (no prior-theme issue templates to archive). TH1 remains `locked: false`.

**Status:** Phase 3 (Planning) complete for TH1. Backlog ready for orchestration/implementation.

---

## 2026-06-16 — Phase 4: Autopilot orchestration begins (Orchestrator)

Starting TH1 execution. Dependency-resolved order: E6 spikes → E1 → E2/E3 → E4/E5.

- **TH1-E6-US1** START — SPIKE macOS/BSD portability cheat-sheet.
- **TH1-E6-US1** DONE — portability-cheatsheet.md produced (commit 1ec7015). XS/spike: self-review only.
- **TH1-E6-US2** START — SPIKE MANIFEST.toml glob classification matcher.
- **TH1-E6-US2** DONE — manifest-classification.md produced; matcher run against throwaway fixture (outside repo), 11/11 paths classified per ADR-006. Mechanism: bash `[[ == glob ]]` array classifier, seed→framework→project→default-project precedence, macOS bash-3.2-safe (no globstar dependency). XS/spike: self-review only.
- **TH1-E6-US2** DONE — manifest-classification.md produced (commit 9a15b7f). Fixture kept out of VCS.
- **EPIC E6 DONE** — both spikes complete. Small-epic ceremony: test suite deferred (none built yet); changelog entry written. E6 unblocks E1-US1, E3-US1, E3-US4, E3-US6.
- **TH1-E1-US1** START — lib/common.sh shared helpers.
- **TH1-E1-US1** DONE — lib/common.sh (commit b2e0d58). Reviewer: APPROVED.
- **TH1-E1-US2** START — bootstrap.sh dispatcher.
- **TH1-E1-US2** DONE — bootstrap.sh dispatcher (commit 44f4507). Reviewer: APPROVED.
- **TH1-E1-US3** START — lib/cmd-doctor.sh prerequisites + drift detection.
- **TH1-E1-US3** DONE — lib/cmd-doctor.sh (commit 2667aba). Reviewer: APPROVED.
- **EPIC E1 DONE** — dispatcher + common.sh + doctor. Small-epic ceremony: bash -n all 3 files OK; doctor/usage smoke exit 0; changelog written. Unblocks E2-US3, E3-US5.
- **TH1-E2-US1** START — vendor 7 harness skills into skills/.
- **TH1-E2-US2** START — vendor cockpit-wake into bin/.
- **TH1-E2-US1** DONE — 7 skills vendored (commit ba7cce7). Verbatim verified.
- **TH1-E2-US2** DONE — cockpit-wake vendored (commit 97d26d5). Verbatim verified.
- **TH1-E2-US3** START — lib/cmd-global.sh install/update 8 skills + cockpit-wake.
- **TH1-E2-US3** DONE — lib/cmd-global.sh installs/updates 8 skills + cockpit-wake; all 9 ACs verified against scratch HOME (copy/link/dry-run/drift/idempotent/PATH/AC9-fatal).
- **TH1-E2-US3** DONE — lib/cmd-global.sh (commit 31c04a1). Reviewer: APPROVED.
- **TH1-E2-US4** START — install.sh wrapper + global --from-release.
- **TH1-E2-US4** DONE — install.sh + --from-release (commit 6be0446). Reviewer: APPROVED. Tech-debt note: reject `--link` with `--from-release` to avoid dangling symlinks (non-AC hardening, deferred).
- **EPIC E2** all stories done — running large-epic ceremony (integration + quality check).
- **EPIC E2 DONE** — integration PASS + quality review APPROVED. Changelog written. Unblocks E2-US4-dependents (E4-US2) and feeds E5 tests. Deferred tech-debt logged.

## 2026-06-16 — Epic E3 (E2E scaffold) begins
- **TH1-E3-US1** START — templates/e2e/ Playwright infra + governed runners.
- **TH1-E3-US1** DONE — template runners (commit 4de48f0; rework fix 406a298). Reviewer: REQUEST_CHANGES→APPROVED (1 iter; JUnit failure-classification + digest header bugs fixed).
- **TH1-E3-US2** START — templates/e2e governance, test-book, tests & runs skeleton.
- **TH1-E3-US2** DONE — governance/test-book/tests skeleton (commit a4792e1). Reviewer: APPROVED.
- **TH1-E3-US3** START — templates/e2e context, skill overlays, tmux stubs & env files.
- **TH1-E3-US3** DONE — overlays/context/tmux/env (commit deddd6e). Reviewer: APPROVED.
- **TH1-E3-US4** START — templates/e2e/MANIFEST.toml ownership classification.
- **TH1-E3-US4** DONE — MANIFEST.toml (commit 640ac96). Reviewer: APPROVED (flaky-known.md safe-default = correct, non-blocker).
- **TH1-E3-US5** START — lib/cmd-e2e.sh scaffold.
- **TH1-E3-US5** DONE — lib/cmd-e2e.sh scaffold (commit 788e682). Reviewer: APPROVED (substitute-everywhere + atomic staging + sed-injection hardening validated).
- **TH1-E3-US6** START — lib/cmd-e2e.sh --update content-preserving refresh.
- **TH1-E3-US6** DONE — --update content-preserving refresh (commit 493f3be). Reviewer: APPROVED (never-touch + idempotency + scaffold-regression validated).
- **EPIC E3** all 6 stories done — running large-epic ceremony (integration + quality check).
- **EPIC E3 DONE** — integration PASS (1 blocker fixed: run-audit INDEX append, commit 4a09371; re-verified PASS) + quality review APPROVED. Changelog written. Unblocks E3-US5/US6-dependents (E4-US2, E5 tests).

## 2026-06-16 — Epics E4 (CI/CD) + E5 (test infra) — interleaved by dependency
- **TH1-E4-US1** START — VERSION + CHANGELOG.md (semver source of truth).
- **TH1-E4-US1** DONE — VERSION 0.1.0 + CHANGELOG.md (commit b8222e8). XS: self-review.
- **TH1-E4-US2** START — .github/workflows/release.yml (tag-driven tarball + sha256 + gh release).

### TH1-E4-US2 — DONE (reviewer APPROVED)
- `.github/workflows/release.yml` (tag-driven tarball + dual sha256 + gh release --latest + Category-5 validate job) + repo-root `README.md`. Commit a8db271. Local packaging dry-run + Category-5 extract/global-dry-run/doctor all PASS; actionlint clean.

### TH1-E5-US1 — START
- Dispatching developer: run-tests.sh dispatcher + Category-1 bats unit tests (cmd-global/cmd-e2e/cmd-doctor). bats not present on host — developer to install.

### TH1-E5-US1 — DONE
- `run-tests.sh` repo-root dispatcher (`unit|template|skills|integration|all`; absent categories auto-skip without false pass, auto-wire when E5-US2 files land) + `tests/unit/{cmd-global,cmd-e2e,cmd-doctor}.bats` + `tests/unit/helper.bash`. All 14 unit tests green via `./run-tests.sh unit`; bats installed under $HOME (not committed). Fake-HOME isolation enforced in helper (hard guard); real ~/.copilot untouched. `bash -n` clean.

### TH1-E5-US1 — DONE (reviewer APPROVED)
- `run-tests.sh` dispatcher + `tests/unit/{cmd-global,cmd-e2e,cmd-doctor}.bats` (14 tests, all pass, exit 0). Fake-HOME isolation with hard guard. Commit d2aeddf.

### TH1-E5-US2 — START
- Dispatching developer: Category 2 (template integrity), Category 3 (skills lint), Category 4 (integration smoke); auto-wire into run-tests.sh.

### TH1-E5-US2 — DONE
- Categories 2-4 added and auto-wired into `run-tests.sh`: `tests/template/check-template.sh` (token resolution after test-substitution of the 4 sanctioned tokens, package.json.tmpl valid JSON, MANIFEST.toml explicit coverage of every templates/e2e/ path, `bash -n` of run-audit.sh/run-playwright.sh), `tests/skills/lint-skills.sh` (YAML frontmatter + non-empty name/description via PyYAML), `tests/integration/smoke.bats` (global/e2e/doctor dry-run smoke under fake HOME).
- Coverage check surfaced 2 genuine manifest gaps → classified in MANIFEST.toml: `MANIFEST.toml`→[framework], `governance/flaky-known.md`→[seed].
- All categories exit 0; `./run-tests.sh template|skills|integration|all` green (all incl. 14 Category-1 unit tests). Fake-HOME hard-guard isolation; real ~/.copilot/cockpit-wake untouched. `bash -n` clean.

### TH1-E5-US2 — DONE (reviewer APPROVED)
- Category 2 `tests/template/check-template.sh`, Category 3 `tests/skills/lint-skills.sh`, Category 4 `tests/integration/smoke.bats`. Closed 2 MANIFEST coverage gaps (MANIFEST.toml→framework, governance/flaky-known.md→seed, classifications verified correct). `run-tests.sh all` exits 0 (14 unit + cat2/3/4). Commit 047197d.
- DEFERRED (non-blocking): sync architecture §7 embedded MANIFEST block with the 2 new classifications; add min-count assert to lint-skills.

### TH1-E4-US3 — START
- Dispatching developer: `.github/workflows/ci.yml` PR checks running test categories 1-4.

### TH1-E4-US3 — DONE (reviewer APPROVED)
- `.github/workflows/ci.yml` PR merge-gate running categories 1-4 via `run-tests.sh all`; no Category 5; `[skip ci]` honoured. Local job-body simulation exit 0. Commit d160e5e.

### Epic E4 (CI/CD & releases) — CEREMONY (small epic, 3 stories)
- Epic smoke: release.yml + ci.yml valid YAML; VERSION=0.1.0; CHANGELOG.md has v0.1.0 section. All 3 stories APPROVED.
- Epic E4 status -> done. Changelog entry written.

### TH1-E5-US3 — START
- Dispatching developer: `skills/copilotcockpit-dev/SKILL.md` (GitOps delivery runbook, 8th managed skill).

### TH1-E5-US3 — DONE (reviewer APPROVED)
- `skills/copilotcockpit-dev/SKILL.md` (8th managed skill, ADR-008 GitOps runbook). Promoted copilotcockpit-dev from CC_PENDING_ROLES → CC_HARNESS_ROLES in cmd-global.sh (now required+installed); updated smoke.bats to assert pending-warning absent. Commit 216bc09.
- DEFERRED (non-blocking): generic-ify the dead pending-skip message in cmd-global.sh:333.

### Epic E5 (Dev skill & test infra) — CEREMONY (small epic, 3 stories)
- Epic gate `./run-tests.sh all` → EXIT 0 (14 unit + template + 8 skills + 3 integration). All 3 stories APPROVED.
- Epic E5 status -> done. Changelog entry written.

## THEME TH1 — CEREMONY (Bootstrap Tooling)
- All 6 epics done (E6, E1, E2, E3, E4, E5); 21/21 stories done; 0 failed/pending/in_progress.
- Full suite `./run-tests.sh all` → EXIT 0 (14 unit + template + 8 skills + 3 integration).
- `bash -n` clean across all shell sources; release.yml + ci.yml valid YAML; install surface present (8 skills).
- Product-owner revalidation vs VP1 → **GO** (all 6 SCs MET, all 8 NFRs addressed, no functional gaps; only minor deferred tech-debt).
- Release notes written: docs/plan/RELEASE-TH1.md (v0.1.0).
- Archived .github/ISSUE_TEMPLATE/TH1-*.md → .github/ISSUE_TEMPLATE/archive/.
- Theme TH1 status -> done (locked: false — awaiting user checkpoint decision; NOT auto-locked).
- PAUSED for user checkpoint: accept / reject / amend.

## TH1 — USER CHECKPOINT: ACCEPTED (2026-06-16)
TH1 accepted by user. Locked. v0.1.0 ready to tag.
- Set `locked: true` on theme TH1 (freezes VP1, theme dir, story files, ADRs).
- Promoted all 8 ADRs (ADR-001..008) from `proposed` -> `accepted`.
- Next manual step (per copilotcockpit-dev runbook): tag `v0.1.0` on `main` to trigger release.yml.
