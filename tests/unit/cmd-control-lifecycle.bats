#!/usr/bin/env bats
# tests/unit/cmd-control-lifecycle.bats — TH3.E2.US1 structured worker lifecycle
# and freshness.
#
# The contract under test has three halves:
#
#   * every lifecycle state a worker may emit is one versioned, machine-readable
#     committed event correlated to its mission, worker, queue item, and trace,
#     and anything malformed, unversioned, future-versioned, or unknown is
#     refused before it can be committed and again when it is read back;
#   * publication and materialization are separate answers: a late, duplicate,
#     superseded, or uncorrelated lifecycle event is durably retained as audit
#     evidence but can never regress a terminal or newer materialized state;
#   * heartbeat expiry is an observation with a recoverable reason, never a
#     mission failure, and the observation is derived from committed events
#     rather than from the derived ledger.
#
# Every freshness assertion uses an explicit `--as-of` moment against an explicit
# `--heartbeat-at`, so nothing in this suite waits on wall-clock timing.

load helper

setup() {
	cc_setup_fake_home
	export CONTROL_BIN="$BATS_TEST_DIRNAME/../../bin/cockpit-control"
	export MODULE_DIR="$BATS_TEST_DIRNAME/../../bin"
	unset COCKPIT_CONTROL_ROOT COCKPIT_QUEUE_ROOT TMUX TMUX_SESSION COCKPIT_SESSION_ID COCKPIT_ID
}

# cc_uuid — print one canonical lowercase UUID for a mission or trace identifier.
cc_uuid() {
	python3 -c 'import uuid; print(uuid.uuid4())'
}

# cc_lifecycle_store <root> — initialize one empty control store under <root>.
cc_lifecycle_store() {
	export COCKPIT_CONTROL_ROOT="$1"
	"$CONTROL_BIN" init >/dev/null
}

# cc_emit <record-lifecycle args...> — emit one lifecycle event and require the
# command itself to succeed. Whether the event advanced the materialized state
# is a separate question every caller asserts on "$output".
cc_emit() {
	run "$CONTROL_BIN" record-lifecycle "$@"
	[ "$status" -eq 0 ]
}

# cc_refuse_lifecycle <stdout-file> <stderr-file> <record-lifecycle args...> —
# require one record-lifecycle invocation to be refused as a fail-closed
# diagnostic: a non-zero exit, an empty stdout payload, a `cockpit-control:`
# stderr diagnostic, and never a Python traceback leaking internal paths.
cc_refuse_lifecycle() {
	local out="$1" err="$2"
	shift 2
	local code=0
	"$CONTROL_BIN" record-lifecycle "$@" >"$out" 2>"$err" || code=$?
	[ "$code" -ne 0 ]
	[ ! -s "$out" ]
	head -n 1 "$err" | grep -q "^cockpit-control: "
	! grep -q "Traceback (most recent call last)" "$err"
	! grep -q "cockpit_control.py" "$err"
}

@test "every lifecycle state commits a versioned event correlated to its mission worker and trace" {
	local root="$BATS_TEST_TMPDIR/lifecycle-vocabulary"
	cc_lifecycle_store "$root"
	local completed_mission failed_mission cancelled_mission replaced_mission
	local replacement trace
	completed_mission="$(cc_uuid)"
	failed_mission="$(cc_uuid)"
	cancelled_mission="$(cc_uuid)"
	replaced_mission="$(cc_uuid)"
	replacement="$(cc_uuid)"
	trace="$(cc_uuid)"

	cc_emit --state accepted --worker worker-dev --mission "$completed_mission" \
		--queue-item QI-1 --trace "$trace" --sequence 1 --fresh-for 300
	cc_emit --state running --worker worker-dev --mission "$completed_mission" \
		--queue-item QI-1 --trace "$trace" --sequence 2 --fresh-for 300
	cc_emit --state blocked --worker worker-dev --mission "$completed_mission" \
		--queue-item QI-1 --trace "$trace" --sequence 3 --fresh-for 300 \
		--reason "awaiting an architecture answer" --blocker-category question
	cc_emit --state running --worker worker-dev --mission "$completed_mission" \
		--queue-item QI-1 --trace "$trace" --sequence 4 --fresh-for 300
	cc_emit --state completed --worker worker-dev --mission "$completed_mission" \
		--queue-item QI-1 --trace "$trace" --sequence 5 \
		--evidence "report:worker-dev/$completed_mission/5" --evidence test:RUN-1

	cc_emit --state accepted --worker worker-test --mission "$failed_mission" \
		--queue-item QI-2 --trace "$trace" --sequence 1 --fresh-for 300
	cc_emit --state running --worker worker-test --mission "$failed_mission" \
		--queue-item QI-2 --trace "$trace" --sequence 2 --fresh-for 300
	cc_emit --state failed --worker worker-test --mission "$failed_mission" \
		--queue-item QI-2 --trace "$trace" --sequence 3 --reason "the governed test run failed"

	cc_emit --state accepted --worker worker-fix --mission "$cancelled_mission" \
		--queue-item QI-3 --trace "$trace" --sequence 1 --fresh-for 300
	cc_emit --state running --worker worker-fix --mission "$cancelled_mission" \
		--queue-item QI-3 --trace "$trace" --sequence 2 --fresh-for 300
	cc_emit --state cancelled --worker worker-fix --mission "$cancelled_mission" \
		--queue-item QI-3 --trace "$trace" --sequence 3 --reason "the queue item was withdrawn"

	cc_emit --state accepted --worker worker-dev --mission "$replaced_mission" \
		--queue-item QI-4 --trace "$trace" --sequence 1 --fresh-for 300
	cc_emit --state replaced --worker worker-dev --mission "$replaced_mission" \
		--queue-item QI-4 --trace "$trace" --sequence 2 \
		--reason "the mission boundary was re-scoped" --superseded-by "$replacement"

	run "$CONTROL_BIN" list-events
	[ "$status" -eq 0 ]
	echo "$output" | grep -q "type worker-lifecycle-accepted actor worker-dev$"
	echo "$output" | grep -q "type worker-lifecycle-running actor worker-dev$"
	echo "$output" | grep -q "type worker-lifecycle-blocked actor worker-dev$"
	echo "$output" | grep -q "type worker-lifecycle-completed actor worker-dev$"
	echo "$output" | grep -q "type worker-lifecycle-failed actor worker-test$"
	echo "$output" | grep -q "type worker-lifecycle-cancelled actor worker-fix$"
	echo "$output" | grep -q "type worker-lifecycle-replaced actor worker-dev$"

	run python3 -c '
import json
import sys
from pathlib import Path
from uuid import UUID

sys.path.insert(0, sys.argv[2])
import cockpit_control

root = Path(sys.argv[1])
control_id = json.loads((root / cockpit_control.CONTROL_METADATA_NAME).read_text())["control_id"]
history = cockpit_control.read_committed_events(root, control_id)

lifecycle = []
for event in history.events:
    record = cockpit_control.event_worker_lifecycle(event.record, event.path.name)
    if record is not None:
        lifecycle.append((event, record))

assert len(lifecycle) == 13, len(lifecycle)
observed = sorted({record["state"] for _event, record in lifecycle})
assert observed == sorted(set(cockpit_control.WORKER_LIFECYCLE_STATES) - {"pending-dispatch"}), observed

for event, record in lifecycle:
    assert event.record["schema_version"] == cockpit_control.CONTROL_SCHEMA_VERSION
    assert record["schema_version"] == cockpit_control.WORKER_LIFECYCLE_SCHEMA_VERSION
    assert record["record_type"] == cockpit_control.WORKER_LIFECYCLE_RECORD_TYPE
    assert event.record["event_type"] == (
        cockpit_control.WORKER_LIFECYCLE_EVENT_PREFIX + record["state"]
    ), event.record["event_type"]
    # Every lifecycle event is correlated, not merely typed.
    UUID(record["mission_id"])
    UUID(record["trace_id"])
    assert record["worker_id"], record
    assert record["queue_item_id"], record
    assert isinstance(record["sequence"], int) and record["sequence"] >= 1
    assert event.record["actor"] == record["worker_id"]
    if record["state"] in cockpit_control.WORKER_LIFECYCLE_ACTIVE_STATES:
        assert record["heartbeat_at"] is not None and record["fresh_until"] is not None
    else:
        assert record["heartbeat_at"] is None and record["fresh_until"] is None

by_state = {record["state"]: record for _event, record in lifecycle}
assert by_state["blocked"]["blocker"] == {"category": "question", "detail": None}
assert by_state["completed"]["evidence_refs"] == [
    "report:worker-dev/%s/5" % sys.argv[3],
    "test:RUN-1",
]
assert by_state["replaced"]["superseded_by_mission_id"] == sys.argv[4]
assert by_state["replaced"]["superseded_by_mission_id"] != by_state["replaced"]["mission_id"]

ledger = json.loads((root / cockpit_control.LEDGER_NAME).read_text())
slots = ledger[cockpit_control.LEDGER_WORKER_MISSIONS_FIELD]
assert len(slots) == 4, sorted(slots)
terminal = sorted(slot["lifecycle"]["state"] for slot in slots.values())
assert terminal == ["cancelled", "completed", "failed", "replaced"], terminal
print("every lifecycle state is versioned and correlated")
' "$root" "$MODULE_DIR" "$completed_mission" "$replacement"
	[ "$status" -eq 0 ]
	echo "$output" | grep -Fq "every lifecycle state is versioned and correlated"

	run "$CONTROL_BIN" validate
	[ "$status" -eq 0 ]
}

@test "an accepted and started worker completes the active mission with its evidence references" {
	local root="$BATS_TEST_TMPDIR/lifecycle-happy-path"
	cc_lifecycle_store "$root"
	local mission trace
	mission="$(cc_uuid)"
	trace="$(cc_uuid)"

	run "$CONTROL_BIN" publish-event --type mission-dispatched --actor overseer \
		--payload "{\"active_queue_item_id\": \"QI-7\", \"active_mission_id\": \"$mission\"}"
	[ "$status" -eq 0 ]

	cc_emit --state accepted --worker worker-dev --mission "$mission" --queue-item QI-7 \
		--trace "$trace" --sequence 1 --fresh-for 300
	echo "$output" | grep -Fq "advanced the materialized mission state"
	cc_emit --state running --worker worker-dev --mission "$mission" --queue-item QI-7 \
		--trace "$trace" --sequence 2 --fresh-for 300
	echo "$output" | grep -Fq "advanced the materialized mission state"

	cc_emit --state completed --worker worker-dev --mission "$mission" --queue-item QI-7 \
		--trace "$trace" --sequence 3 \
		--evidence "queue:QI-7" --evidence "report:worker-dev/$mission/3" --evidence test:RUN-42
	echo "$output" | grep -Fq "worker-lifecycle completed sequence 3 for mission $mission"
	echo "$output" | grep -Fq "advanced the materialized mission state"

	run python3 -c '
import json
import sys
from pathlib import Path

sys.path.insert(0, sys.argv[2])
import cockpit_control

root = Path(sys.argv[1])
mission = sys.argv[3]
control_id = json.loads((root / cockpit_control.CONTROL_METADATA_NAME).read_text())["control_id"]
ledger = json.loads((root / cockpit_control.LEDGER_NAME).read_text())

assert ledger["active_mission_id"] == mission
assert ledger["active_queue_item_id"] == "QI-7"
slot = ledger[cockpit_control.LEDGER_WORKER_MISSIONS_FIELD][mission]
assert slot["lifecycle"]["state"] == cockpit_control.LIFECYCLE_COMPLETED
assert slot["lifecycle"]["sequence"] == 3
assert slot["lifecycle"]["worker_id"] == "worker-dev"
assert slot["lifecycle"]["trace_id"] == sys.argv[4]
assert slot["lifecycle"]["evidence_refs"] == [
    "queue:QI-7",
    "report:worker-dev/%s/3" % mission,
    "test:RUN-42",
]

# The materialized slot points back at exactly one immutable committed event.
history = cockpit_control.read_committed_events(root, control_id)
source = [event for event in history.events if event.revision == slot["revision"]]
assert len(source) == 1
assert source[0].event_id == slot["event_id"]
assert source[0].record["timestamp"] == slot["recorded_at"]
assert source[0].record["payload"][cockpit_control.WORKER_LIFECYCLE_PAYLOAD_FIELD] == (
    slot["lifecycle"]
)
print("the completion materialized with its evidence references")
' "$root" "$MODULE_DIR" "$mission" "$trace"
	[ "$status" -eq 0 ]
	echo "$output" | grep -Fq "the completion materialized with its evidence references"

	run "$CONTROL_BIN" lifecycle-status
	[ "$status" -eq 0 ]
	echo "$output" | grep -Fq "$mission worker worker-dev queue-item QI-7 trace $trace state completed sequence 3 observation terminal"
}

@test "a late running event after a higher-sequence completion is retained for audit only" {
	local root="$BATS_TEST_TMPDIR/lifecycle-late-event"
	cc_lifecycle_store "$root"
	local mission trace
	mission="$(cc_uuid)"
	trace="$(cc_uuid)"

	cc_emit --state accepted --worker worker-dev --mission "$mission" --queue-item QI-8 \
		--trace "$trace" --sequence 1 --fresh-for 300
	cc_emit --state running --worker worker-dev --mission "$mission" --queue-item QI-8 \
		--trace "$trace" --sequence 2 --fresh-for 300
	cc_emit --state completed --worker worker-dev --mission "$mission" --queue-item QI-8 \
		--trace "$trace" --sequence 6 --evidence "report:worker-dev/$mission/6"

	local before_slots before_revision
	before_slots="$(python3 -c '
import json, sys
ledger = json.load(open(sys.argv[1]))
print(json.dumps(ledger["worker_missions"], sort_keys=True))
' "$root/ledger.json")"
	before_revision="$(python3 -c '
import json, sys
print(json.load(open(sys.argv[1]))["revision"])
' "$root/ledger.json")"

	run "$CONTROL_BIN" record-lifecycle --state running --worker worker-dev --mission "$mission" \
		--queue-item QI-8 --trace "$trace" --sequence 3 --fresh-for 300
	[ "$status" -eq 0 ]
	echo "$output" | grep -Fq "is retained for audit only (retained-terminal-state; materialized state is completed at sequence 6)"
	echo "$output" | grep -Fq "is durable audit evidence that did not change the materialized mission state (retained-terminal-state)"

	run python3 -c '
import json
import sys
from pathlib import Path

sys.path.insert(0, sys.argv[2])
import cockpit_control

root = Path(sys.argv[1])
before_slots = json.loads(sys.argv[3])
before_revision = int(sys.argv[4])
control_id = json.loads((root / cockpit_control.CONTROL_METADATA_NAME).read_text())["control_id"]
history = cockpit_control.read_committed_events(root, control_id)

# The late event is durably committed: it is retained, not discarded.
assert history.latest_revision == before_revision + 1, history.latest_revision
late = history.events[-1]
record = cockpit_control.event_worker_lifecycle(late.record, late.path.name)
assert record["state"] == cockpit_control.LIFECYCLE_RUNNING
assert record["sequence"] == 3
assert late.path.is_file()

# ... and it changed nothing about the materialized mission state.
ledger = json.loads((root / cockpit_control.LEDGER_NAME).read_text())
assert ledger["revision"] == before_revision + 1
after_slots = ledger[cockpit_control.LEDGER_WORKER_MISSIONS_FIELD]
assert json.dumps(after_slots, sort_keys=True) == json.dumps(before_slots, sort_keys=True)
assert after_slots[sys.argv[5]]["lifecycle"]["state"] == cockpit_control.LIFECYCLE_COMPLETED
assert after_slots[sys.argv[5]]["lifecycle"]["sequence"] == 6

# Replay is deterministic: rebuilding from committed events reaches the same
# materialized state, so retention never leaks into the projection later.
metadata = cockpit_control.validate_root_metadata(
    json.loads((root / cockpit_control.CONTROL_METADATA_NAME).read_text()), root
)
rebuilt = cockpit_control.build_ledger_projection(metadata, history.events)
assert rebuilt[cockpit_control.LEDGER_WORKER_MISSIONS_FIELD] == after_slots
print("the late event is retained and materialized state is unchanged")
' "$root" "$MODULE_DIR" "$before_slots" "$before_revision" "$mission"
	[ "$status" -eq 0 ]
	echo "$output" | grep -Fq "the late event is retained and materialized state is unchanged"

	run "$CONTROL_BIN" lifecycle-status
	[ "$status" -eq 0 ]
	echo "$output" | grep -Fq "state completed sequence 6 observation terminal"
}

@test "duplicate and lower lifecycle sequences cannot re-apply or regress a newer state" {
	local root="$BATS_TEST_TMPDIR/lifecycle-sequences"
	cc_lifecycle_store "$root"
	local mission trace
	mission="$(cc_uuid)"
	trace="$(cc_uuid)"

	cc_emit --state accepted --worker worker-dev --mission "$mission" --queue-item QI-9 \
		--trace "$trace" --sequence 1 --fresh-for 300
	cc_emit --state running --worker worker-dev --mission "$mission" --queue-item QI-9 \
		--trace "$trace" --sequence 5 --fresh-for 300

	run "$CONTROL_BIN" record-lifecycle --state running --worker worker-dev --mission "$mission" \
		--queue-item QI-9 --trace "$trace" --sequence 5 --fresh-for 300
	[ "$status" -eq 0 ]
	echo "$output" | grep -Fq "is retained for audit only (retained-stale-sequence; materialized state is running at sequence 5)"

	run "$CONTROL_BIN" record-lifecycle --state blocked --worker worker-dev --mission "$mission" \
		--queue-item QI-9 --trace "$trace" --sequence 3 --fresh-for 300 \
		--reason "a late blocker report" --blocker-category question
	[ "$status" -eq 0 ]
	echo "$output" | grep -Fq "is retained for audit only (retained-stale-sequence; materialized state is running at sequence 5)"

	# A strictly newer sequence still advances the same slot, so the guard is
	# monotonicity rather than a permanent freeze.
	cc_emit --state blocked --worker worker-dev --mission "$mission" --queue-item QI-9 \
		--trace "$trace" --sequence 6 --fresh-for 300 \
		--reason "a current blocker report" --blocker-category question
	echo "$output" | grep -Fq "advanced the materialized mission state"

	run python3 -c '
import json
import sys
from pathlib import Path

sys.path.insert(0, sys.argv[2])
import cockpit_control

root = Path(sys.argv[1])
control_id = json.loads((root / cockpit_control.CONTROL_METADATA_NAME).read_text())["control_id"]
history = cockpit_control.read_committed_events(root, control_id)
assert history.latest_revision == 5, history.latest_revision

slots, outcomes = cockpit_control.fold_worker_missions(history.events)
folded = [outcomes[event.event_id] for event in history.events]
assert folded == [
    (True, cockpit_control.LIFECYCLE_APPLIED),
    (True, cockpit_control.LIFECYCLE_APPLIED),
    (False, cockpit_control.LIFECYCLE_RETAINED_STALE_SEQUENCE),
    (False, cockpit_control.LIFECYCLE_RETAINED_STALE_SEQUENCE),
    (True, cockpit_control.LIFECYCLE_APPLIED),
], folded

slot = slots[sys.argv[3]]
assert slot["lifecycle"]["state"] == cockpit_control.LIFECYCLE_BLOCKED
assert slot["lifecycle"]["sequence"] == 6
assert slot["lifecycle"]["reason"] == "a current blocker report"
print("equal and lower sequences never moved the materialized state")
' "$root" "$MODULE_DIR" "$mission"
	[ "$status" -eq 0 ]
	echo "$output" | grep -Fq "equal and lower sequences never moved the materialized state"
}

@test "an expired heartbeat is reported stale with a recoverable reason and never as a failure" {
	local root="$BATS_TEST_TMPDIR/lifecycle-freshness"
	cc_lifecycle_store "$root"
	local mission trace
	mission="$(cc_uuid)"
	trace="$(cc_uuid)"

	cc_emit --state accepted --worker worker-dev --mission "$mission" --queue-item QI-10 \
		--trace "$trace" --sequence 1 \
		--heartbeat-at 2026-01-01T00:00:00.000000Z --fresh-for 120
	cc_emit --state running --worker worker-dev --mission "$mission" --queue-item QI-10 \
		--trace "$trace" --sequence 2 \
		--heartbeat-at 2026-01-01T00:00:30.000000Z --fresh-until 2026-01-01T00:02:30.000000Z

	local before
	before="$(cc_control_snapshot "$root")"

	run "$CONTROL_BIN" lifecycle-status --as-of 2026-01-01T00:01:00.000000Z
	[ "$status" -eq 0 ]
	echo "$output" | grep -Fq "0 stale observation(s)"
	echo "$output" | grep -Fq "state running sequence 2 observation fresh reason - recovery - fresh-until 2026-01-01T00:02:30.000000Z"

	# The deadline itself has not yet passed, so the boundary is still fresh.
	run "$CONTROL_BIN" lifecycle-status --as-of 2026-01-01T00:02:30.000000Z
	[ "$status" -eq 0 ]
	echo "$output" | grep -Fq "0 stale observation(s)"
	echo "$output" | grep -Fq "observation fresh"

	run "$CONTROL_BIN" lifecycle-status --as-of 2026-01-01T00:02:30.000001Z
	[ "$status" -eq 0 ]
	echo "$output" | grep -Fq "1 stale observation(s)"
	echo "$output" | grep -Fq "state running sequence 2 observation stale reason heartbeat-expired recovery awaiting-bounded-recovery fresh-until 2026-01-01T00:02:30.000000Z"
	echo "$output" | grep -Fq "is a stale running observation (heartbeat-expired); this is recoverable and is not a mission failure, and it awaits a bounded recovery action"
	# Expiry is explicitly not a failure claim, and it is not a terminal state.
	! echo "$output" | grep -q "failed"
	! echo "$output" | grep -q "observation terminal"

	# Reconciling freshness changed nothing: the mission is still running.
	[ "$before" = "$(cc_control_snapshot "$root")" ]

	run python3 -c '
import json
import sys
from pathlib import Path

sys.path.insert(0, sys.argv[2])
import cockpit_control

root = Path(sys.argv[1])
mission = sys.argv[3]
ledger = json.loads((root / cockpit_control.LEDGER_NAME).read_text())
slot = ledger[cockpit_control.LEDGER_WORKER_MISSIONS_FIELD][mission]
assert slot["lifecycle"]["state"] == cockpit_control.LIFECYCLE_RUNNING

observations = cockpit_control.observe_worker_lifecycle(
    root, as_of="2026-01-01T00:10:00.000000Z"
)
assert len(observations) == 1
observation = observations[0]
assert observation.stale
assert observation.state == cockpit_control.LIFECYCLE_RUNNING
assert observation.observation == cockpit_control.LIFECYCLE_OBSERVATION_STALE
assert observation.reason == cockpit_control.LIFECYCLE_STALE_REASON
assert observation.recovery == cockpit_control.LIFECYCLE_STALE_RECOVERY
assert observation.recoverable is True
assert observation.terminal is False
assert observation.observation not in cockpit_control.WORKER_LIFECYCLE_STATES
assert observation.mission_id == mission
assert observation.trace_id == sys.argv[4]

# A refreshed heartbeat clears the same observation without any state change.
fresh = cockpit_control.observe_worker_lifecycle(
    root, as_of="2026-01-01T00:00:45.000000Z"
)
assert fresh[0].observation == cockpit_control.LIFECYCLE_OBSERVATION_FRESH
assert fresh[0].reason is None and fresh[0].recovery is None
print("expiry is a recoverable stale observation, not a failure")
' "$root" "$MODULE_DIR" "$mission" "$trace"
	[ "$status" -eq 0 ]
	echo "$output" | grep -Fq "expiry is a recoverable stale observation, not a failure"
}

@test "lifecycle status derives freshness from committed events not the derived ledger" {
	local root="$BATS_TEST_TMPDIR/lifecycle-status-derivation"
	cc_lifecycle_store "$root"
	local mission trace
	mission="$(cc_uuid)"
	trace="$(cc_uuid)"

	cc_emit --state accepted --worker worker-dev --mission "$mission" --queue-item QI-11 \
		--trace "$trace" --sequence 1 \
		--heartbeat-at 2026-01-01T00:00:00.000000Z --fresh-for 60

	printf 'not a ledger\n' > "$root/ledger.json"
	local before
	before="$(cc_control_snapshot "$root")"

	run "$CONTROL_BIN" lifecycle-status --as-of 2026-01-01T00:05:00.000000Z
	[ "$status" -eq 0 ]
	echo "$output" | grep -Fq "1 worker mission(s) in $root as of 2026-01-01T00:05:00.000000Z; 1 stale observation(s)"
	echo "$output" | grep -Fq "state accepted sequence 1 observation stale reason heartbeat-expired"
	[ "$before" = "$(cc_control_snapshot "$root")" ]

	run "$CONTROL_BIN" lifecycle-status --worker worker-test --as-of 2026-01-01T00:05:00.000000Z
	[ "$status" -eq 0 ]
	echo "$output" | grep -Fq "0 worker mission(s) in $root as of 2026-01-01T00:05:00.000000Z; 0 stale observation(s)"
	[ "$before" = "$(cc_control_snapshot "$root")" ]

	run "$CONTROL_BIN" lifecycle-status --as-of not-a-timestamp
	[ "$status" -ne 0 ]
	echo "$output" | grep -Fq "cockpit-control: lifecycle status requires UTC as_of"
	[ "$before" = "$(cc_control_snapshot "$root")" ]
}

@test "an uncorrelated or out-of-order lifecycle event never materializes mission state" {
	local root="$BATS_TEST_TMPDIR/lifecycle-correlation"
	cc_lifecycle_store "$root"
	local first second trace
	first="$(cc_uuid)"
	second="$(cc_uuid)"
	trace="$(cc_uuid)"

	# `running` is not reachable from pending-dispatch, so nothing materializes.
	run "$CONTROL_BIN" record-lifecycle --state running --worker worker-dev --mission "$first" \
		--queue-item QI-12 --trace "$trace" --sequence 1 --fresh-for 300
	[ "$status" -eq 0 ]
	echo "$output" | grep -Fq "is retained for audit only (retained-invalid-transition; no materialized mission state exists)"

	cc_emit --state accepted --worker worker-dev --mission "$second" --queue-item QI-13 \
		--trace "$trace" --sequence 1 --fresh-for 300

	# A different worker claiming the same mission is unmatched evidence.
	run "$CONTROL_BIN" record-lifecycle --state running --worker worker-test --mission "$second" \
		--queue-item QI-13 --trace "$trace" --sequence 2 --fresh-for 300
	[ "$status" -eq 0 ]
	echo "$output" | grep -Fq "is retained for audit only (retained-unmatched-correlation; materialized state is accepted at sequence 1)"

	# So is the same worker reporting the mission against a different queue item.
	run "$CONTROL_BIN" record-lifecycle --state running --worker worker-dev --mission "$second" \
		--queue-item QI-99 --trace "$trace" --sequence 2 --fresh-for 300
	[ "$status" -eq 0 ]
	echo "$output" | grep -Fq "is retained for audit only (retained-unmatched-correlation; materialized state is accepted at sequence 1)"

	run python3 -c '
import json
import sys
from pathlib import Path

sys.path.insert(0, sys.argv[2])
import cockpit_control

root = Path(sys.argv[1])
control_id = json.loads((root / cockpit_control.CONTROL_METADATA_NAME).read_text())["control_id"]
history = cockpit_control.read_committed_events(root, control_id)
assert history.latest_revision == 4, history.latest_revision

ledger = json.loads((root / cockpit_control.LEDGER_NAME).read_text())
slots = ledger[cockpit_control.LEDGER_WORKER_MISSIONS_FIELD]
assert sorted(slots) == [sys.argv[4]], sorted(slots)
assert slots[sys.argv[4]]["lifecycle"]["state"] == cockpit_control.LIFECYCLE_ACCEPTED
assert slots[sys.argv[4]]["lifecycle"]["sequence"] == 1
assert slots[sys.argv[4]]["lifecycle"]["worker_id"] == "worker-dev"
assert sys.argv[3] not in slots
print("unmatched and unreachable lifecycle events materialized nothing")
' "$root" "$MODULE_DIR" "$first" "$second"
	[ "$status" -eq 0 ]
	echo "$output" | grep -Fq "unmatched and unreachable lifecycle events materialized nothing"
}

@test "the materialized transition table is exactly the architecture section 9 table" {
	run python3 -c '
import sys

sys.path.insert(0, sys.argv[1])
import cockpit_control

# Architecture section 9:
#   pending-dispatch -> accepted -> running
#   running -> blocked -> running
#   running -> completed | failed | cancelled
#   blocked -> failed | cancelled
#   accepted | running | blocked -> replaced
# plus the same-active-state heartbeat refresh, which renews freshness without
# changing the declared state.
expected = {
    "pending-dispatch": {"accepted"},
    "accepted": {"accepted", "running", "replaced"},
    "running": {"running", "blocked", "completed", "failed", "cancelled", "replaced"},
    "blocked": {"blocked", "running", "failed", "cancelled", "replaced"},
}
observed = {
    state: set(successors)
    for state, successors in cockpit_control.WORKER_LIFECYCLE_TRANSITIONS.items()
}
assert observed == expected, observed

# Terminal states are absolute: none of them has a successor at all.
for state in cockpit_control.WORKER_LIFECYCLE_TERMINAL_STATES:
    assert state not in cockpit_control.WORKER_LIFECYCLE_TRANSITIONS, state

assert sorted(cockpit_control.WORKER_LIFECYCLE_STATES) == sorted(
    set(cockpit_control.WORKER_LIFECYCLE_ACTIVE_STATES)
    | set(cockpit_control.WORKER_LIFECYCLE_TERMINAL_STATES)
    | {"pending-dispatch"}
)
print("the transition table matches architecture section 9")
' "$MODULE_DIR"
	[ "$status" -eq 0 ]
	echo "$output" | grep -Fq "the transition table matches architecture section 9"

	local root="$BATS_TEST_TMPDIR/lifecycle-transitions"
	cc_lifecycle_store "$root"
	local mission trace
	mission="$(cc_uuid)"
	trace="$(cc_uuid)"

	cc_emit --state accepted --worker worker-dev --mission "$mission" --queue-item QI-18 \
		--trace "$trace" --sequence 1 --fresh-for 300

	# `accepted -> failed` is not an edge in the table, so the report is retained
	# as evidence instead of quietly ending a mission that never started.
	run "$CONTROL_BIN" record-lifecycle --state failed --worker worker-dev --mission "$mission" \
		--queue-item QI-18 --trace "$trace" --sequence 2 --reason "never started"
	[ "$status" -eq 0 ]
	echo "$output" | grep -Fq "is retained for audit only (retained-invalid-transition; materialized state is accepted at sequence 1)"

	run "$CONTROL_BIN" lifecycle-status --as-of 2026-01-01T00:00:00.000000Z
	[ "$status" -eq 0 ]
	echo "$output" | grep -Fq "state accepted sequence 1"

	# Re-emitting the same active state at a newer sequence is the heartbeat
	# refresh path: it renews freshness without changing the declared state.
	cc_emit --state accepted --worker worker-dev --mission "$mission" --queue-item QI-18 \
		--trace "$trace" --sequence 3 \
		--heartbeat-at 2026-01-01T00:00:00.000000Z --fresh-for 600
	echo "$output" | grep -Fq "advanced the materialized mission state"

	run "$CONTROL_BIN" lifecycle-status --as-of 2026-01-01T00:05:00.000000Z
	[ "$status" -eq 0 ]
	echo "$output" | grep -Fq "state accepted sequence 3 observation fresh"
	echo "$output" | grep -Fq "fresh-until 2026-01-01T00:10:00.000000Z"
}

@test "malformed unversioned and future-versioned lifecycle records fail closed when read back" {
	local root="$BATS_TEST_TMPDIR/lifecycle-fail-closed"
	cc_lifecycle_store "$root"

	run python3 -c '
import copy
import json
import subprocess
import sys
from pathlib import Path
from uuid import uuid4

sys.path.insert(0, sys.argv[2])
import cockpit_control

root = Path(sys.argv[1])
control_bin = sys.argv[3]
events = root / cockpit_control.EVENTS_DIR_NAME
control_id = json.loads((root / cockpit_control.CONTROL_METADATA_NAME).read_text())["control_id"]
mission = str(uuid4())
trace = str(uuid4())

valid = cockpit_control.build_worker_lifecycle(
    state="running",
    worker_id="worker-dev",
    mission_id=mission,
    queue_item_id="QI-1",
    trace_id=trace,
    sequence=2,
    heartbeat_at="2026-01-01T00:00:00.000000Z",
    fresh_until="2026-01-01T00:01:00.000000Z",
)

def lifecycle(**changes):
    record = copy.deepcopy(valid)
    for field, value in changes.items():
        if value is cockpit_control:
            record.pop(field, None)
        else:
            record[field] = value
    return record

REMOVE = cockpit_control

cases = (
    ("unversioned", "worker-lifecycle-running", lifecycle(schema_version=REMOVE),
     "requires integer schema_version"),
    ("future-versioned", "worker-lifecycle-running", lifecycle(schema_version=2),
     "unsupported future schema_version 2"),
    ("wrong record type", "worker-lifecycle-running", lifecycle(record_type="worker-report"),
     "requires record_type"),
    ("unknown state", "worker-lifecycle-running", lifecycle(state="paused"),
     "declares unknown state"),
    ("unknown field", "worker-lifecycle-running", lifecycle(command_id=str(uuid4())),
     "declares unknown field(s) command_id"),
    ("missing correlation", "worker-lifecycle-running", lifecycle(trace_id=REMOVE),
     "requires trace_id"),
    ("non-uuid mission", "worker-lifecycle-running", lifecycle(mission_id="mission-1"),
     "requires UUID mission_id"),
    ("zero sequence", "worker-lifecycle-running", lifecycle(sequence=0),
     "requires positive integer sequence"),
    ("boolean sequence", "worker-lifecycle-running", lifecycle(sequence=True),
     "requires positive integer sequence"),
    ("mismatched event type", "worker-lifecycle-blocked", lifecycle(),
     "expected event_type"),
    ("expired freshness order", "worker-lifecycle-running",
     lifecycle(fresh_until="2026-01-01T00:00:00.000000Z"),
     "requires fresh_until after heartbeat_at"),
    ("terminal freshness", "worker-lifecycle-completed",
     lifecycle(state="completed", evidence_refs=["test:RUN-1"]),
     "must not declare heartbeat_at for terminal state"),
    ("completion without evidence", "worker-lifecycle-completed",
     lifecycle(state="completed", heartbeat_at=None, fresh_until=None),
     "requires at least one evidence reference"),
    ("untyped evidence", "worker-lifecycle-completed",
     lifecycle(state="completed", heartbeat_at=None, fresh_until=None,
               evidence_refs=["a plain sentence"]),
     "must not contain whitespace"),
    ("blocker on a running state", "worker-lifecycle-running",
     lifecycle(blocker={"category": "question", "detail": "invented"}),
     "must not declare a blocker for state"),
    ("blocked without blocker", "worker-lifecycle-blocked",
     lifecycle(state="blocked", reason="waiting"),
     "requires an object blocker"),
    ("terminal without reason", "worker-lifecycle-failed",
     lifecycle(state="failed", heartbeat_at=None, fresh_until=None),
     "requires a reason for state"),
    ("self replacement", "worker-lifecycle-replaced",
     lifecycle(state="replaced", reason="re-scoped", heartbeat_at=None, fresh_until=None,
               superseded_by_mission_id=mission),
     "requires superseded_by_mission_id to name a different mission"),
)

def commit(event_type, record):
    event_id = str(uuid4())
    event = {
        "schema_version": cockpit_control.CONTROL_SCHEMA_VERSION,
        "record_type": "event",
        "event_id": event_id,
        "control_id": control_id,
        "timestamp": "2026-01-01T00:00:30.000000Z",
        "revision": 1,
        "event_type": event_type,
        "actor": "worker-dev",
        "payload": {cockpit_control.WORKER_LIFECYCLE_PAYLOAD_FIELD: record},
    }
    path = events / cockpit_control._event_filename(1, event_id)
    path.write_text(json.dumps(event, indent=2, sort_keys=True) + "\n")
    return path

ledger_before = (root / cockpit_control.LEDGER_NAME).read_bytes()
for name, event_type, record, expected in cases:
    path = commit(event_type, record)
    try:
        result = subprocess.run(
            [control_bin, "validate"],
            capture_output=True,
            text=True,
            env=dict(**{"COCKPIT_CONTROL_ROOT": str(root)}, PATH=sys.argv[4], HOME=sys.argv[5]),
        )
        assert result.returncode != 0, "%s was accepted" % name
        assert expected in result.stderr, "%s: %r" % (name, result.stderr)
        assert result.stderr.startswith("cockpit-control: "), result.stderr
        assert result.stdout == "", result.stdout
        assert (root / cockpit_control.LEDGER_NAME).read_bytes() == ledger_before
    finally:
        path.unlink()

# A lifecycle event type with no lifecycle payload at all is equally refused.
event_id = str(uuid4())
event = {
    "schema_version": cockpit_control.CONTROL_SCHEMA_VERSION,
    "record_type": "event",
    "event_id": event_id,
    "control_id": control_id,
    "timestamp": "2026-01-01T00:00:30.000000Z",
    "revision": 1,
    "event_type": "worker-lifecycle-running",
    "actor": "worker-dev",
    "payload": {},
}
path = events / cockpit_control._event_filename(1, event_id)
path.write_text(json.dumps(event, indent=2, sort_keys=True) + "\n")
try:
    result = subprocess.run(
        [control_bin, "validate"],
        capture_output=True,
        text=True,
        env=dict(**{"COCKPIT_CONTROL_ROOT": str(root)}, PATH=sys.argv[4], HOME=sys.argv[5]),
    )
    assert result.returncode != 0
    assert "without a payload.worker_lifecycle record" in result.stderr, result.stderr
finally:
    path.unlink()

# A lifecycle record hidden under an unrelated event type is refused as well.
event["event_type"] = "mission-dispatched"
event["payload"] = {cockpit_control.WORKER_LIFECYCLE_PAYLOAD_FIELD: valid}
path.write_text(json.dumps(event, indent=2, sort_keys=True) + "\n")
try:
    result = subprocess.run(
        [control_bin, "validate"],
        capture_output=True,
        text=True,
        env=dict(**{"COCKPIT_CONTROL_ROOT": str(root)}, PATH=sys.argv[4], HOME=sys.argv[5]),
    )
    assert result.returncode != 0
    assert "expected event_type" in result.stderr, result.stderr
finally:
    path.unlink()

assert (root / cockpit_control.LEDGER_NAME).read_bytes() == ledger_before
print("%d malformed lifecycle records failed closed" % (len(cases) + 2))
' "$root" "$MODULE_DIR" "$CONTROL_BIN" "$PATH" "$HOME"
	[ "$status" -eq 0 ]
	echo "$output" | grep -Fq "20 malformed lifecycle records failed closed"

	run "$CONTROL_BIN" validate
	[ "$status" -eq 0 ]
}

@test "malformed lifecycle arguments are refused before any event is committed" {
	local root="$BATS_TEST_TMPDIR/lifecycle-arguments"
	cc_lifecycle_store "$root"
	local mission trace
	mission="$(cc_uuid)"
	trace="$(cc_uuid)"
	local before
	before="$(cc_control_contents "$root")"

	run "$CONTROL_BIN" record-lifecycle --state running --worker worker-dev --mission "$mission" \
		--queue-item QI-14 --trace "$trace" --sequence 1
	[ "$status" -ne 0 ]
	echo "$output" | grep -Fq "requires an explicit freshness deadline; pass --fresh-until or --fresh-for"

	run "$CONTROL_BIN" record-lifecycle --state completed --worker worker-dev --mission "$mission" \
		--queue-item QI-14 --trace "$trace" --sequence 1 --fresh-for 60 --evidence test:RUN-1
	[ "$status" -ne 0 ]
	echo "$output" | grep -Fq -e "--fresh-for cannot be given for terminal state 'completed'"

	run "$CONTROL_BIN" record-lifecycle --state completed --worker worker-dev --mission "$mission" \
		--queue-item QI-14 --trace "$trace" --sequence 1
	[ "$status" -ne 0 ]
	echo "$output" | grep -Fq "requires at least one evidence reference"

	run "$CONTROL_BIN" record-lifecycle --state failed --worker worker-dev --mission "$mission" \
		--queue-item QI-14 --trace "$trace" --sequence 1
	[ "$status" -ne 0 ]
	echo "$output" | grep -Fq "requires a reason for state 'failed'"

	run "$CONTROL_BIN" record-lifecycle --state blocked --worker worker-dev --mission "$mission" \
		--queue-item QI-14 --trace "$trace" --sequence 1 --fresh-for 60 --reason waiting
	[ "$status" -ne 0 ]
	echo "$output" | grep -Fq "requires a categorized blocker; pass --blocker-category"

	run "$CONTROL_BIN" record-lifecycle --state replaced --worker worker-dev --mission "$mission" \
		--queue-item QI-14 --trace "$trace" --sequence 1 --reason "re-scoped"
	[ "$status" -ne 0 ]
	echo "$output" | grep -Fq "requires non-empty superseded_by_mission_id"

	run "$CONTROL_BIN" record-lifecycle --state accepted --worker worker-dev --mission not-a-uuid \
		--queue-item QI-14 --trace "$trace" --sequence 1 --fresh-for 60
	[ "$status" -ne 0 ]
	echo "$output" | grep -Fq "requires UUID mission_id"

	run "$CONTROL_BIN" record-lifecycle --state accepted --worker worker-dev --mission "$mission" \
		--queue-item QI-14 --trace "$trace" --sequence 0 --fresh-for 60
	[ "$status" -ne 0 ]
	echo "$output" | grep -Fq "requires positive integer sequence"

	run "$CONTROL_BIN" record-lifecycle --state accepted --worker worker-dev --mission "$mission" \
		--queue-item QI-14 --trace "$trace" --sequence 1.5 --fresh-for 60
	[ "$status" -ne 0 ]
	echo "$output" | grep -Fq "requires positive integer sequence"

	run "$CONTROL_BIN" record-lifecycle --state accepted --worker worker-dev --mission "$mission" \
		--queue-item QI-14 --trace "$trace" --sequence 1 --fresh-for 60 --evidence untyped-evidence
	[ "$status" -ne 0 ]
	echo "$output" | grep -Fq "must be a typed '<type>:<value>' evidence reference"

	run "$CONTROL_BIN" record-lifecycle --state accepted --worker worker-dev --mission "$mission" \
		--queue-item QI-14 --trace "$trace" --sequence 1 --fresh-for 60 \
		--fresh-until 2026-01-01T00:01:00.000000Z
	[ "$status" -ne 0 ]
	echo "$output" | grep -Fq -e "--fresh-until and --fresh-for cannot be combined"

	[ "$before" = "$(cc_control_contents "$root")" ]
	run "$CONTROL_BIN" list-events
	[ "$status" -eq 0 ]
	echo "$output" | grep -Fq "0 committed events in $root (latest revision 0)"
}

@test "the record-lifecycle dry run reports its decision and changes no state" {
	local root="$BATS_TEST_TMPDIR/lifecycle-dry-run"
	cc_lifecycle_store "$root"
	local mission trace
	mission="$(cc_uuid)"
	trace="$(cc_uuid)"

	cc_emit --state accepted --worker worker-dev --mission "$mission" --queue-item QI-15 \
		--trace "$trace" --sequence 1 --fresh-for 300
	cc_emit --state running --worker worker-dev --mission "$mission" --queue-item QI-15 \
		--trace "$trace" --sequence 2 --fresh-for 300
	cc_emit --state completed --worker worker-dev --mission "$mission" --queue-item QI-15 \
		--trace "$trace" --sequence 3 --evidence "report:worker-dev/$mission/3"

	local before
	before="$(cc_control_contents "$root")"

	run "$CONTROL_BIN" record-lifecycle --state running --worker worker-dev --mission "$mission" \
		--queue-item QI-15 --trace "$trace" --sequence 9 --fresh-for 300 --dry-run
	[ "$status" -eq 0 ]
	echo "$output" | grep -Fq "at revision 4; no state changed"
	echo "$output" | grep -Fq "would be retained for audit only (retained-terminal-state; materialized state is completed at sequence 3)"
	[ "$before" = "$(cc_control_contents "$root")" ]

	local other other_trace
	other="$(cc_uuid)"
	other_trace="$(cc_uuid)"
	run "$CONTROL_BIN" record-lifecycle --state accepted --worker worker-test --mission "$other" \
		--queue-item QI-16 --trace "$other_trace" --sequence 1 --fresh-for 300 --dry-run
	[ "$status" -eq 0 ]
	echo "$output" | grep -Fq "would advance the materialized mission state"
	[ "$before" = "$(cc_control_contents "$root")" ]

	run "$CONTROL_BIN" list-events
	[ "$status" -eq 0 ]
	echo "$output" | grep -Fq "3 committed events in $root (latest revision 3)"
}

@test "a hand-edited ledger worker mission slot is refused as derived corruption and replayed" {
	local root="$BATS_TEST_TMPDIR/lifecycle-ledger-repair"
	cc_lifecycle_store "$root"
	local mission trace
	mission="$(cc_uuid)"
	trace="$(cc_uuid)"

	cc_emit --state accepted --worker worker-dev --mission "$mission" --queue-item QI-17 \
		--trace "$trace" --sequence 1 --fresh-for 300
	cc_emit --state running --worker worker-dev --mission "$mission" --queue-item QI-17 \
		--trace "$trace" --sequence 2 --fresh-for 300

	local projected
	projected="$(cat "$root/ledger.json")"

	# A ledger claiming a mission state no committed event published is derived
	# corruption: it never becomes authority and never survives a replay.
	python3 -c '
import json
import sys

path = sys.argv[1]
ledger = json.load(open(path))
slot = ledger["worker_missions"][sys.argv[2]]
slot["lifecycle"]["state"] = "failed"
slot["lifecycle"]["reason"] = "invented by hand"
slot["lifecycle"]["heartbeat_at"] = None
slot["lifecycle"]["fresh_until"] = None
with open(path, "w") as handle:
    handle.write(json.dumps(ledger, indent=2, sort_keys=True) + "\n")
' "$root/ledger.json" "$mission"

	run "$CONTROL_BIN" lifecycle-status --as-of 2026-01-01T00:00:00.000000Z
	[ "$status" -eq 0 ]
	echo "$output" | grep -Fq "state running sequence 2"
	! echo "$output" | grep -q "failed"

	run "$CONTROL_BIN" replay-ledger
	[ "$status" -eq 0 ]
	echo "$output" | grep -Fq "rebuilt ledger.json at revision 2"
	[ "$projected" = "$(cat "$root/ledger.json")" ]

	run "$CONTROL_BIN" validate
	[ "$status" -eq 0 ]

	# A ledger that drops the materialized worker mission slots altogether is
	# not a ledger that merely has none: the field is required, so its absence
	# is refused as derived corruption and healed by a replay rather than being
	# silently defaulted to an empty projection.
	python3 -c '
import json
import sys

path = sys.argv[1]
ledger = json.load(open(path))
del ledger["worker_missions"]
with open(path, "w") as handle:
    handle.write(json.dumps(ledger, indent=2, sort_keys=True) + "\n")
' "$root/ledger.json"

	run "$CONTROL_BIN" validate
	[ "$status" -ne 0 ]
	echo "$output" | grep -Fq "ledger.json requires worker_missions"

	run "$CONTROL_BIN" replay-ledger
	[ "$status" -eq 0 ]
	[ "$projected" = "$(cat "$root/ledger.json")" ]

	run "$CONTROL_BIN" validate
	[ "$status" -eq 0 ]
}


@test "worker-controlled identity strings cannot forge a lifecycle record boundary" {
	local root="$BATS_TEST_TMPDIR/lifecycle-injection"
	cc_lifecycle_store "$root"
	local mission trace forged
	mission="$(cc_uuid)"
	trace="$(cc_uuid)"
	local out="$BATS_TEST_TMPDIR/injection-stdout"
	local err="$BATS_TEST_TMPDIR/injection-stderr"

	cc_emit --state accepted --worker worker-dev --mission "$mission" --queue-item QI-18 \
		--trace "$trace" --heartbeat-at 2026-01-01T00:00:00.000000Z --sequence 1 \
		--fresh-for 300

	local before
	before="$(cc_control_contents "$root")"

	# One fully-formed spoofed status record, exactly as an untrusted worker
	# would append it to the line-oriented payload if identity strings were
	# accepted verbatim.
	forged=$'worker-evil\nSPOOFED worker root queue-item Q trace T state completed sequence 99 observation terminal reason - recovery - fresh-until -'

	cc_refuse_lifecycle "$out" "$err" --state running --worker "$forged" \
		--mission "$mission" --queue-item QI-18 --trace "$trace" --sequence 2 --fresh-for 300
	grep -Fq "requires printable non-whitespace ASCII worker_id" "$err"
	grep -Fq "could forge a record boundary" "$err"
	# The refusal diagnostic cannot forge a line either: the offending value is
	# reported escaped, so no bare SPOOFED record ever reaches a stream.
	! grep -q "^SPOOFED" "$err"

	cc_refuse_lifecycle "$out" "$err" --state running --worker worker-dev \
		--mission "$mission" --queue-item "$forged" --trace "$trace" --sequence 2 --fresh-for 300
	grep -Fq "requires printable non-whitespace ASCII queue_item_id" "$err"
	! grep -q "^SPOOFED" "$err"

	# Whitespace and bare control characters are refused on the same ground, and
	# so is the `--actor` path, which defaults to the emitting worker.
	cc_refuse_lifecycle "$out" "$err" --state running --worker $'worker\tdev' \
		--mission "$mission" --queue-item QI-18 --trace "$trace" --sequence 2 --fresh-for 300
	grep -Fq "requires printable non-whitespace ASCII worker_id" "$err"
	cc_refuse_lifecycle "$out" "$err" --state running --worker $'worker\001dev' \
		--mission "$mission" --queue-item QI-18 --trace "$trace" --sequence 2 --fresh-for 300
	grep -Fq "requires printable non-whitespace ASCII worker_id" "$err"
	cc_refuse_lifecycle "$out" "$err" --state running --worker worker-dev \
		--mission "$mission" --queue-item $'QI\n18' --trace "$trace" --sequence 2 \
		--fresh-for 300
	grep -Fq "requires printable non-whitespace ASCII queue_item_id" "$err"

	# Nothing was committed: every refusal happened before any candidate event
	# existed, so the store is byte-identical to the pre-attempt state.
	[ "$before" = "$(cc_control_contents "$root")" ]
	run "$CONTROL_BIN" list-events
	[ "$status" -eq 0 ]
	echo "$output" | grep -Fq "1 committed events in $root (latest revision 1)"

	# The forged boundary never becomes a second status record: exactly one
	# mission is reported, and nothing claims the spoofed terminal completion.
	"$CONTROL_BIN" lifecycle-status --as-of 2026-01-01T00:01:00.000000Z >"$out" 2>"$err"
	[ "$(grep -c " observation " "$out")" -eq 1 ]
	! grep -q "SPOOFED" "$out"
	! grep -q "state completed" "$out"
	grep -Fq "1 worker mission(s) in $root" "$out"
}


@test "an unrepresentable freshness deadline or sequence is refused with a diagnostic" {
	local root="$BATS_TEST_TMPDIR/lifecycle-overflow"
	cc_lifecycle_store "$root"
	local mission trace
	mission="$(cc_uuid)"
	trace="$(cc_uuid)"
	local out="$BATS_TEST_TMPDIR/overflow-stdout"
	local err="$BATS_TEST_TMPDIR/overflow-stderr"
	local before
	before="$(cc_control_contents "$root")"

	# A freshness window that leaves the representable timestamp range is a
	# refusal with a diagnostic, never an uncaught OverflowError traceback.
	cc_refuse_lifecycle "$out" "$err" --state running --worker worker-dev \
		--mission "$mission" --queue-item QI-19 --trace "$trace" --sequence 1 \
		--fresh-for 1e12
	grep -Fq -e "--fresh-for produces a freshness deadline outside the representable timestamp range" "$err"

	# The same arithmetic reached from the heartbeat side rather than the window.
	cc_refuse_lifecycle "$out" "$err" --state running --worker worker-dev \
		--mission "$mission" --queue-item QI-19 --trace "$trace" --sequence 1 \
		--heartbeat-at 9999-12-31T23:59:59.999999Z --fresh-for 300
	grep -Fq "outside the representable timestamp range" "$err"

	# A sequence too long for the interpreter to convert at all is refused the
	# same way instead of escaping as a ValueError traceback.
	local huge
	huge="$(python3 -c 'print("1" * 5000)')"
	cc_refuse_lifecycle "$out" "$err" --state running --worker worker-dev \
		--mission "$mission" --queue-item QI-19 --trace "$trace" --sequence "$huge" \
		--fresh-for 300
	grep -Fq "requires a sequence within the representable integer range" "$err"

	# An infinite or malformed window is still refused by the shared guard.
	cc_refuse_lifecycle "$out" "$err" --state running --worker worker-dev \
		--mission "$mission" --queue-item QI-19 --trace "$trace" --sequence 1 \
		--fresh-for 1e400
	grep -Fq -e "--fresh-for must be a positive number of seconds" "$err"

	[ "$before" = "$(cc_control_contents "$root")" ]
	run "$CONTROL_BIN" list-events
	[ "$status" -eq 0 ]
	echo "$output" | grep -Fq "0 committed events in $root (latest revision 0)"
	run "$CONTROL_BIN" validate
	[ "$status" -eq 0 ]
}
