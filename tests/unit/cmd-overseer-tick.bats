#!/usr/bin/env bats
# tests/unit/cmd-overseer-tick.bats — TH3.E3.US1 one-action controller tick and
# evidence precedence.
#
# The contract under test has three halves:
#
#   * one `cockpit-overseer tick` validates the control and queue roots, replays
#     the committed journal, checks the derived ledger against that replay, reads
#     product-work and worker state, and selects *at most one* state-changing
#     action, which it persists and then exits;
#   * conflicting evidence is resolved in the ADR-014 order — human decision,
#     queue state, durable events, replayed journal, materialized ledger,
#     correlated worker reports, live status, pane text — and the last two are
#     reported without ever advancing authoritative state;
#   * a tick that finds no valid action persists the observation or escalation
#     state it reconciled to and terminates, and the next tick over the same
#     evidence persists nothing at all instead of investigating it again.
#
# Nothing here waits on wall-clock timing: every moment is an explicit `--as-of`
# and the concurrency proof starts real processes and lets the control lock and
# the deterministic fold order them.

load helper

setup() {
	cc_setup_fake_home
	export PATH="$BATS_TEST_TMPDIR/bin:$PATH"
	mkdir -p "$BATS_TEST_TMPDIR/bin"
	export OVERSEER_BIN="$BATS_TEST_DIRNAME/../../bin/cockpit-overseer"
	export CONTROL_BIN="$BATS_TEST_DIRNAME/../../bin/cockpit-control"
	export QUEUE_BIN="$BATS_TEST_DIRNAME/../../bin/cockpit-queue"
	export MODULE_DIR="$BATS_TEST_DIRNAME/../../bin"
	unset COCKPIT_CONTROL_ROOT COCKPIT_QUEUE_ROOT TMUX TMUX_SESSION COCKPIT_SESSION_ID COCKPIT_ID
	cc_setup_tmux_stub
}

# --- errexit-honouring assertion helpers -------------------------------------
#
# A negative assertion must never be written as a bare `! grep -q ...`: bash
# suspends `set -e` for any command whose status is inverted with `!`
# (ShellCheck SC2314), so such a line can report a forged match and let the test
# continue regardless. Returning non-zero from a helper is a plain command
# failure, which errexit does honour.

# cc_absent_output <pattern> — require <pattern> to be absent from `$output`.
cc_absent_output() {
	if printf '%s\n' "$output" | grep -q "$1"; then
		echo "cc_absent_output: forbidden pattern '$1' found in captured output:" >&2
		printf '%s\n' "$output" | grep -n "$1" >&2
		return 1
	fi
	return 0
}

# cc_absent <pattern> <file> — require <pattern> to be absent from <file>.
cc_absent() {
	if grep -q "$1" "$2"; then
		echo "cc_absent: forbidden pattern '$1' found in $2:" >&2
		grep -n "$1" "$2" >&2
		return 1
	fi
	return 0
}

# cc_absent_events <pattern> — require no committed event to carry <pattern>.
cc_absent_events() {
	local matches
	matches="$(grep -rl "$1" "$COCKPIT_CONTROL_ROOT/events" || true)"
	if [ -n "$matches" ]; then
		echo "cc_absent_events: forbidden pattern '$1' reached a committed event:" >&2
		printf '%s\n' "$matches" >&2
		return 1
	fi
	return 0
}

# cc_no_traceback <file> — a refusal never leaks a traceback or a module path.
cc_no_traceback() {
	cc_absent "Traceback (most recent call last)" "$1"
	cc_absent "cockpit_control\.py" "$1"
}

# --- fixtures ----------------------------------------------------------------

# cc_setup_tmux_stub — a tmux whose pane text each test writes explicitly, so
# ADR-014 rule 8 evidence is a controlled input rather than a live terminal.
cc_setup_tmux_stub() {
	cat >"$BATS_TEST_TMPDIR/bin/tmux" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
state_dir="${BATS_TEST_TMPDIR:-/tmp}/tmux-stub"
mkdir -p "$state_dir"
cmd="${1:-}"
shift || true
target=""
while [ $# -gt 0 ]; do
	case "$1" in
	-t)
		target="$2"
		shift 2
		;;
	*) shift ;;
	esac
done
case "$cmd" in
capture-pane)
	file="$state_dir/$(printf '%s' "$target" | tr ':' '_').txt"
	[ -f "$file" ] && cat "$file"
	;;
display-message)
	printf '%s\n' "${TMUX_SESSION:-cockpit}"
	;;
*) ;;
esac
EOF
	chmod +x "$BATS_TEST_TMPDIR/bin/tmux"
}

# cc_pane <session> <window> <text> — publish the exact pane text rule 8 sees.
cc_pane() {
	mkdir -p "$BATS_TEST_TMPDIR/tmux-stub"
	printf '%s\n' "$3" >"$BATS_TEST_TMPDIR/tmux-stub/$1_$2.txt"
}

# cc_declare_queue_root <control-root> <queue-root> — declare the mission's
# canonical queue boundary in the authoritative root metadata, exactly as
# tests/unit/cmd-control-preflight.bats does.
cc_declare_queue_root() {
	python3 -c '
import json
import sys

path = sys.argv[1] + "/control.json"
with open(path) as handle:
    record = json.load(handle)
record["canonical_roots"]["queue_root"] = sys.argv[2]
record["queue_root"] = sys.argv[2]
with open(path, "w") as handle:
    handle.write(json.dumps(record, indent=2, sort_keys=True) + "\n")
' "$1" "$2"
}

# cc_cockpit <name> — build one scratch cockpit: an initialized control root that
# declares an existing queue root, with both exported. Prints nothing.
cc_cockpit() {
	export COCKPIT_CONTROL_ROOT="$BATS_TEST_TMPDIR/$1-control"
	export COCKPIT_QUEUE_ROOT="$BATS_TEST_TMPDIR/$1-queue"
	mkdir -p "$COCKPIT_QUEUE_ROOT"
	"$CONTROL_BIN" init >/dev/null
	cc_declare_queue_root "$COCKPIT_CONTROL_ROOT" "$COCKPIT_QUEUE_ROOT"
	run "$CONTROL_BIN" replay-ledger
	[ "$status" -eq 0 ]
}

# cc_active_item <state> — enqueue one build-method item, start it, and move it
# to <state> through cockpit-queue itself. Prints the queue item ID.
cc_active_item() {
	local item
	item="$("$QUEUE_BIN" enqueue --text "/the-copilot-build-method deliver a change" --title change)"
	"$QUEUE_BIN" start-next >/dev/null
	"$QUEUE_BIN" transition "$item" "$1" --reason "ready for $1" >/dev/null
	printf '%s' "$item"
}

# cc_tick [args...] — run one controller tick and capture stdout, stderr, status.
cc_tick() {
	run "$OVERSEER_BIN" tick "$@"
}

# cc_events <root> — print how many immutable events the store has committed.
cc_events() {
	find "$1/events" -name '*.json' | grep -c . || true
}

# cc_emit <record-lifecycle args...> — emit one lifecycle event successfully.
cc_emit() {
	run "$CONTROL_BIN" record-lifecycle "$@"
	[ "$status" -eq 0 ]
}

# cc_ledger <root> <python-expression over `ledger`> — print one derived value.
cc_ledger() {
	python3 -c '
import json
import sys

with open(sys.argv[1] + "/ledger.json") as handle:
    ledger = json.load(handle)
print(eval(sys.argv[2]))
' "$1" "$2"
}

# --- AC1: one tick, one action ----------------------------------------------

@test "AC1 BDD1 an idle worker receives the next mission as exactly one dispatch command" {
	cc_cockpit happy
	local item
	item="$(cc_active_item implementing)"
	cc_pane cockpit worker-dev "waiting"

	[ "$(cc_events "$COCKPIT_CONTROL_ROOT")" -eq 0 ]
	cc_tick --window worker-dev --as-of 2026-09-04T10:00:00.000000Z
	[ "$status" -eq 0 ]
	echo "$output" | grep -Fq "tick dispatched action dispatch-mission outcome dispatched reason queue-item-implementable"
	echo "$output" | grep -Fq "events-committed 1"
	echo "$output" | grep -Eq "^dispatch command [0-9a-f-]{36} worker worker-dev mission [0-9a-f-]{36} queue-item $item trace [0-9a-f-]{36} digest sha256:[0-9a-f]{64}$"

	# Exactly one immutable event, exactly one registered command, and exactly
	# one worker slot: the tick took one action and no more.
	[ "$(cc_events "$COCKPIT_CONTROL_ROOT")" -eq 1 ]
	[ "$(cc_ledger "$COCKPIT_CONTROL_ROOT" 'len(ledger["commands"])')" -eq 1 ]
	[ "$(cc_ledger "$COCKPIT_CONTROL_ROOT" 'len(ledger["mission_slots"])')" -eq 1 ]
	[ "$(cc_ledger "$COCKPIT_CONTROL_ROOT" 'ledger["mission_slots"]["worker-dev"]["state"]')" = "reserved" ]
	[ "$(cc_ledger "$COCKPIT_CONTROL_ROOT" 'ledger["mission_slots"]["worker-dev"]["queue_item_id"]')" = "$item" ]
	[ "$(cc_ledger "$COCKPIT_CONTROL_ROOT" 'ledger["active_queue_item_id"]')" = "$item" ]
	[ "$(cc_ledger "$COCKPIT_CONTROL_ROOT" 'ledger["controller"]["outcome"]')" = "dispatched" ]
	[ "$(cc_ledger "$COCKPIT_CONTROL_ROOT" 'len(ledger["mission_slots"]["worker-dev"]["conflicts"])')" -eq 0 ]

	# The dispatch is a managed command whose correlated record rides in the very
	# same committed event, so a dispatch correlated to nothing cannot exist.
	run "$CONTROL_BIN" list-events
	[ "$status" -eq 0 ]
	echo "$output" | grep -Fq "command-registered"
	grep -l '"controller_dispatch"' "$COCKPIT_CONTROL_ROOT"/events/*.json
	grep -l '"command_envelope"' "$COCKPIT_CONTROL_ROOT"/events/*.json
}

@test "AC1 a tick that already acted refuses a second state-changing action" {
	cc_cockpit oneaction
	cc_active_item implementing >/dev/null

	run python3 -c '
import sys

sys.path.insert(0, sys.argv[1])
import cockpit_control

tick = cockpit_control.ControllerTick(sys.argv[2], as_of="2026-09-04T10:00:00.000000Z")
evidence = tick._gathered_evidence()
action = cockpit_control.select_controller_action(evidence)
first = tick._commit(action, evidence)
print("first", first.outcome, first.committed_events)
try:
    tick._commit(action, evidence)
except cockpit_control.ControlStoreError as exc:
    print("refused", exc)
else:
    print("BUG: a second action was allowed")
' "$MODULE_DIR" "$COCKPIT_CONTROL_ROOT"
	[ "$status" -eq 0 ]
	echo "$output" | grep -Fq "first dispatched 1"
	echo "$output" | grep -Fq "refused a controller tick takes at most one state-changing action"
	cc_absent_output "BUG"
	[ "$(cc_events "$COCKPIT_CONTROL_ROOT")" -eq 1 ]
}

@test "AC1 a tick blocks instead of trusting a derived ledger that disagrees with the journal" {
	cc_cockpit divergent
	cc_active_item implementing >/dev/null
	cc_tick --as-of 2026-09-04T10:00:00.000000Z
	[ "$status" -eq 0 ]

	# Rewrite the derived projection so it claims a revision the committed
	# journal never reached. Rule 5 is a projection, never authority.
	python3 -c '
import json
import sys

path = sys.argv[1] + "/ledger.json"
with open(path) as handle:
    ledger = json.load(handle)
ledger["revision"] = 99
with open(path, "w") as handle:
    handle.write(json.dumps(ledger, indent=2, sort_keys=True) + "\n")
' "$COCKPIT_CONTROL_ROOT"

	local before
	before="$(cc_events "$COCKPIT_CONTROL_ROOT")"
	local contents
	contents="$(cc_control_contents "$COCKPIT_CONTROL_ROOT")"
	local out="$BATS_TEST_TMPDIR/ahead.out" err="$BATS_TEST_TMPDIR/ahead.err"
	local code=0
	"$OVERSEER_BIN" tick --as-of 2026-09-04T10:05:00.000000Z >"$out" 2>"$err" || code=$?
	[ "$code" -ne 0 ]
	[ ! -s "$out" ]

	# TH3.E3.US2 repairs every derived disagreement the committed journal can
	# settle, so the repair a refusal names is never one the tick could have
	# performed itself. A projection *ahead* of the journal is the one exception:
	# replaying it would rewind derived state past revisions events/ no longer
	# holds, which would quietly settle the disappearance of committed authority.
	# Recording even a blocking observation would replace the projection through
	# the ordinary publication path and destroy that evidence, so the tick
	# refuses before it assembles evidence and commits nothing at all.
	grep -Fq "ledger.json records revision 99 but events/ ends at revision 1" "$err"
	grep -Fq "committed events appear to have been removed and replay would rewind derived state" "$err"
	grep -Fq "[repair: restore the committed event file(s) that ledger.json names but events/ no longer holds" "$err"
	grep -Fq "only if the removal was intended run \`cockpit-control replay-ledger\` to rewind the derived ledger to revision 1]" "$err"
	cc_no_traceback "$err"

	# Nothing was committed and the projection it refused is byte-identical.
	[ "$(cc_events "$COCKPIT_CONTROL_ROOT")" -eq "$before" ]
	[ "$(cc_control_contents "$COCKPIT_CONTROL_ROOT")" = "$contents" ]

	# The decision function still keeps its own structural guard for derived
	# state no tick may decide from, and that guard can never reach a dispatch.
	run python3 -c '
import dataclasses
import sys

sys.path.insert(0, sys.argv[1])
import cockpit_control

tick = cockpit_control.ControllerTick(sys.argv[2], as_of="2026-09-04T10:06:00.000000Z")
probe = dataclasses.replace(
    tick._gathered_evidence(),
    ledger_repair=cockpit_control.CONTROLLER_LEDGER_UNREPAIRED,
    ledger_reason=cockpit_control.PROJECTION_REASON_DIVERGENT,
)
action = cockpit_control.select_controller_action(probe)
print("action", action.kind, action.outcome, action.reason)
' "$MODULE_DIR" "$COCKPIT_CONTROL_ROOT"
	[ "$status" -eq 1 ]
	echo "$output" | grep -Fq "ledger.json records revision 99"
}

@test "AC1 the window a concurrent writer holds open is not evidence of divergence" {
	cc_cockpit window
	local item parked writer
	item="$(cc_active_item implementing)"
	parked="$BATS_TEST_TMPDIR/writer-parked"
	writer="$BATS_TEST_TMPDIR/writer.log"

	# A second real process stops exactly where architecture section 8.3 leaves
	# a store between step 4 and step 8: its event is committed authority, the
	# published ledger still projects the revision before it, and `control.lock`
	# is still held. It resumes only once another process has queued for that
	# lock, so the tick below is guaranteed to have read the open window rather
	# than whatever the timing happened to produce.
	python3 -c '
import os
import sys
import time

sys.path.insert(0, sys.argv[1])
import cockpit_control

locks = os.path.join(sys.argv[2], "locks")


def park_at_projection_boundary(boundary, projection):
    if boundary != "projection-built":
        return
    with open(sys.argv[3], "w"):
        pass
    deadline = time.monotonic() + 60.0
    while time.monotonic() < deadline:
        for name in os.listdir(locks):
            if name.startswith(".control.lock.candidate-"):
                return
        time.sleep(0.01)
    raise SystemExit("no reader ever queued for the control lock")


cockpit_control._ledger_projection_fault = park_at_projection_boundary
cockpit_control.publish_control_event(
    sys.argv[2], "overseer-decided", actor="worker", payload={"note": "heartbeat"}
)
print("writer committed and projected")
' "$MODULE_DIR" "$COCKPIT_CONTROL_ROOT" "$parked" >"$writer" 2>&1 &

	local waited=0
	while [ ! -f "$parked" ]; do
		sleep 0.05
		waited=$((waited + 1))
		if [ "$waited" -ge 600 ]; then
			echo "the writer never reached the projection boundary" >&2
			cat "$writer" >&2
			return 1
		fi
	done

	# The window is real and open: one revision is committed authority and the
	# published projection does not carry it yet.
	[ "$(cc_events "$COCKPIT_CONTROL_ROOT")" -eq 1 ]
	[ "$(cc_ledger "$COCKPIT_CONTROL_ROOT" 'ledger["revision"]')" -eq 0 ]
	[ -e "$COCKPIT_CONTROL_ROOT/locks/control.lock" ]

	cc_tick --window worker-dev --as-of 2026-09-04T10:00:00.000000Z
	[ "$status" -eq 0 ]
	wait
	grep -Fq "writer committed and projected" "$writer"

	# The tick read the journal and the projection as one settled observation,
	# so it reported rule 5 as current and decided from validated evidence.
	echo "$output" | grep -Fq "precedence 5 ledger-projection derived revision 1 current"
	echo "$output" | grep -Fq "tick dispatched action dispatch-mission outcome dispatched reason queue-item-implementable"
	echo "$output" | grep -Fq "precedence 4 control-journal authoritative revision 1"
	cc_absent_output "ledger-projection-divergent"
	cc_absent_events "ledger-projection-divergent"
	[ "$(cc_ledger "$COCKPIT_CONTROL_ROOT" 'ledger["mission_slots"]["worker-dev"]["queue_item_id"]')" = "$item" ]
	run "$CONTROL_BIN" validate
	[ "$status" -eq 0 ]
}

@test "AC1 a projection an interrupted writer never replaced is repaired, not refused" {
	cc_cockpit interrupted
	cc_active_item implementing >/dev/null

	# A real writer commits its event and is interrupted before it can replace
	# the projection: section 8.3 step 4 completed and step 8 never ran, which
	# sections 11 and 17 repair by replaying the journal and never by refusing.
	run python3 -c '
import sys

sys.path.insert(0, sys.argv[1])
import cockpit_control


def interrupt_at_projection_boundary(boundary, projection):
    if boundary == "projection-built":
        raise SystemExit(9)


cockpit_control._ledger_projection_fault = interrupt_at_projection_boundary
cockpit_control.publish_control_event(
    sys.argv[2], "overseer-decided", actor="worker", payload={"note": "heartbeat"}
)
' "$MODULE_DIR" "$COCKPIT_CONTROL_ROOT"
	[ "$status" -eq 9 ]
	[ "$(cc_events "$COCKPIT_CONTROL_ROOT")" -eq 1 ]
	[ "$(cc_ledger "$COCKPIT_CONTROL_ROOT" 'ledger["revision"]')" -eq 0 ]
	[ ! -e "$COCKPIT_CONTROL_ROOT/locks/control.lock" ]

	# Nothing holds the lock now, so this lag is a settled fact rather than a
	# window; it is derived state the committed journal already answers, and
	# TH3.E3.US2 makes the tick itself perform the replay section 17 names.
	cc_tick --as-of 2026-09-04T10:00:00.000000Z
	[ "$status" -eq 0 ]
	echo "$output" | grep -Fq "ledger repair repaired from stale ledger-revision 1 journal-revision 1"
	echo "$output" | grep -Fq "precedence 5 ledger-projection derived revision 1 current repair repaired"
	echo "$output" | grep -Fq "outcome dispatched reason queue-item-implementable"
	cc_absent_output "ledger-projection-divergent"
	cc_absent_events "ledger-projection-divergent"

	# The repair commits no event of its own: the only event this tick added is
	# the one dispatch it decided on, so repair is never the tick's one action.
	echo "$output" | grep -Fq "events-committed 1"
	[ "$(cc_events "$COCKPIT_CONTROL_ROOT")" -eq 2 ]

	run "$CONTROL_BIN" replay-ledger
	[ "$status" -eq 0 ]
	echo "$output" | grep -Fq "ledger.json projection is current at revision 2"
	run "$CONTROL_BIN" validate
	[ "$status" -eq 0 ]
}

@test "AC1 every derived disagreement the journal can settle is repaired, not refused" {
	cc_cockpit forged
	cc_active_item implementing >/dev/null
	cc_tick --as-of 2026-09-04T10:00:00.000000Z
	[ "$status" -eq 0 ]
	local clean before

	# The same revision the journal reached, carrying derived state the journal
	# never produced: no writer following section 8.3 can publish this. It is
	# still *derived* state the committed events reproduce exactly, so TH3.E3.US2
	# rebuilds it in step 3 of the bounded loop rather than refusing to decide.
	python3 -c '
import json
import sys

path = sys.argv[1] + "/ledger.json"
with open(path) as handle:
    ledger = json.load(handle)
ledger["active_queue_item_id"] = "QI-forged"
with open(path, "w") as handle:
    handle.write(json.dumps(ledger, indent=2, sort_keys=True) + "\n")
' "$COCKPIT_CONTROL_ROOT"
	cc_tick --as-of 2026-09-04T10:05:00.000000Z
	[ "$status" -eq 0 ]
	echo "$output" | grep -Fq "ledger repair repaired from divergent"
	echo "$output" | grep -Eq "^precedence 5 ledger-projection derived revision [0-9]+ current repair repaired$"
	cc_absent_output "^dispatch command"
	cc_absent_output "QI-forged"

	# From here the situation is unchanged, so every later tick decides nothing
	# and commits nothing: what follows measures repair alone.
	before="$(cc_events "$COCKPIT_CONTROL_ROOT")"
	clean="$(cat "$COCKPIT_CONTROL_ROOT/ledger.json")"

	# A projection that cannot be read at all is derived corruption too, and
	# architecture section 17 rebuilds it from the journal by exactly the same
	# replay. The rebuild is byte-identical to the clean projection.
	printf 'not a projection\n' >"$COCKPIT_CONTROL_ROOT/ledger.json"
	cc_tick --as-of 2026-09-04T10:10:00.000000Z
	[ "$status" -eq 0 ]
	echo "$output" | grep -Fq "ledger repair repaired from corrupt"
	echo "$output" | grep -Fq "events-committed 0"
	cc_absent_output "malformed ledger.json"
	printf '%s\n' "$output" >"$BATS_TEST_TMPDIR/forged.log"
	cc_no_traceback "$BATS_TEST_TMPDIR/forged.log"

	# The rebuildable compatibility view is repaired by the same one replay.
	printf '{"torn":\n' >"$COCKPIT_CONTROL_ROOT/events.jsonl"
	cc_tick --as-of 2026-09-04T10:15:00.000000Z
	[ "$status" -eq 0 ]
	echo "$output" | grep -Fq "ledger repair repaired from derived-view"
	echo "$output" | grep -Fq "events-committed 0"
	run "$CONTROL_BIN" validate
	[ "$status" -eq 0 ]

	# Neither repair committed an event: repair is step 3 of the bounded loop,
	# never the one state-changing action a tick may take, and each rebuild is
	# byte-identical to the projection a clean store carries.
	[ "$(cc_events "$COCKPIT_CONTROL_ROOT")" -eq "$before" ]
	[ "$clean" = "$(cat "$COCKPIT_CONTROL_ROOT/ledger.json")" ]

	# Every classification the tick can observe is read exactly once, and by one
	# production function rather than by whichever caller looked. A future
	# classification belonging to neither side is a refusal, not a default.
	run python3 -c '
import sys

sys.path.insert(0, sys.argv[1])
import cockpit_control

repairable = set(cockpit_control.CONTROLLER_PROJECTION_REPAIRABLE_REASONS)
refused = set(cockpit_control.CONTROLLER_PROJECTION_DIVERGENT_REASONS)
observable = {
    value
    for name, value in vars(cockpit_control).items()
    if name.startswith("PROJECTION_REASON_")
}
if repairable & refused:
    raise SystemExit("a classification is both repairable and refused: %r" % (repairable & refused,))
if repairable | refused | {cockpit_control.PROJECTION_REASON_CURRENT} != observable:
    raise SystemExit("classification vocabulary drifted: %r" % (sorted(observable),))
print("projection vocabulary is partitioned")

expected = {cockpit_control.PROJECTION_REASON_CURRENT: cockpit_control.CONTROLLER_LEDGER_CURRENT}
for reason in repairable:
    expected[reason] = cockpit_control.CONTROLLER_LEDGER_REPAIRED
for reason in refused:
    expected[reason] = cockpit_control.CONTROLLER_LEDGER_REFUSED
for reason in sorted(observable):
    plan = cockpit_control.controller_ledger_plan(reason)
    if plan != expected[reason]:
        raise SystemExit("%s was planned as %r" % (reason, plan))
try:
    cockpit_control.controller_ledger_plan("invented-classification")
except cockpit_control.ControlStoreError as exc:
    print("unclassified", exc)
else:
    raise SystemExit("BUG: an unclassified projection reason was silently planned")
print("every classification is read exactly once")
' "$MODULE_DIR"
	[ "$status" -eq 0 ]
	echo "$output" | grep -Fq "projection vocabulary is partitioned"
	echo "$output" | grep -Fq "every classification is read exactly once"
	echo "$output" | grep -Fq "unclassified ledger.json classification"
	cc_absent_output "BUG"
}

@test "AC1 a busy worker's durable slot outranks a newly implementable item" {
	cc_cockpit busy
	local first second mission
	first="$(cc_active_item implementing)"
	cc_tick --as-of 2026-09-04T10:00:00.000000Z
	[ "$status" -eq 0 ]
	mission="$(cc_ledger "$COCKPIT_CONTROL_ROOT" 'ledger["mission_slots"]["worker-dev"]["mission_id"]')"

	# The dispatched item leaves the active set without the worker ever
	# reporting the mission finished, and a new item enters the same state, so
	# product work is implementable by a worker whose one slot is still claimed.
	run "$QUEUE_BIN" reject "$first" --reason "abandoned by the operator"
	[ "$status" -eq 0 ]
	second="$(cc_active_item implementing)"
	[ "$second" != "$first" ]

	cc_tick --as-of 2026-09-04T10:05:00.000000Z
	[ "$status" -eq 0 ]
	echo "$output" | grep -Fq "tick recorded action record-observation outcome observed reason worker-busy"
	echo "$output" | grep -Fq "precedence 3 durable-events authoritative claimed-slots 1 worker worker-dev mission $mission slot-state reserved"
	cc_absent_output "^dispatch command"

	# Reading worker state is half of choosing an action: without it the tick
	# would mint a second dispatch that the fold could only record as a
	# conflict. Exactly one command exists, and the slot is untouched.
	[ "$(cc_events "$COCKPIT_CONTROL_ROOT")" -eq 2 ]
	[ "$(cc_ledger "$COCKPIT_CONTROL_ROOT" 'len(ledger["commands"])')" -eq 1 ]
	[ "$(cc_ledger "$COCKPIT_CONTROL_ROOT" 'len(ledger["mission_slots"])')" -eq 1 ]
	[ "$(cc_ledger "$COCKPIT_CONTROL_ROOT" 'len(ledger["mission_slots"]["worker-dev"]["conflicts"])')" -eq 0 ]
	[ "$(cc_ledger "$COCKPIT_CONTROL_ROOT" 'ledger["mission_slots"]["worker-dev"]["queue_item_id"]')" = "$first" ]
	[ "$(cc_ledger "$COCKPIT_CONTROL_ROOT" 'ledger["mission_slots"]["worker-dev"]["mission_id"]')" = "$mission" ]
	[ "$(cc_ledger "$COCKPIT_CONTROL_ROOT" 'ledger["controller"]["reason"]')" = "worker-busy" ]
	[ "$(cc_ledger "$COCKPIT_CONTROL_ROOT" 'ledger["controller"]["outcome"]')" = "observed" ]

	# A busy worker is an observation, not an escalation: the tick terminates
	# cleanly and does not investigate the same slot again.
	cc_tick --as-of 2026-09-04T10:10:00.000000Z
	[ "$status" -eq 0 ]
	echo "$output" | grep -Fq "tick unchanged action none"
	echo "$output" | grep -Fq "outcome observed reason worker-busy"
	[ "$(cc_events "$COCKPIT_CONTROL_ROOT")" -eq 2 ]
}

# --- AC2: ADR-014 precedence -------------------------------------------------

@test "AC2 BDD2 pane text that looks available never outranks a running lifecycle" {
	cc_cockpit pane
	local item mission trace
	item="$(cc_active_item implementing)"
	cc_pane cockpit worker-dev "waiting"
	cc_tick --window worker-dev --as-of 2026-09-04T10:00:00.000000Z
	[ "$status" -eq 0 ]
	mission="$(cc_ledger "$COCKPIT_CONTROL_ROOT" 'ledger["mission_slots"]["worker-dev"]["mission_id"]')"
	trace="$(python3 -c 'import uuid; print(uuid.uuid4())')"

	# The worker durably accepts and runs the mission it was dispatched.
	cc_emit --state accepted --worker worker-dev --mission "$mission" --queue-item "$item" \
		--trace "$trace" --sequence 1 --fresh-for 3600
	cc_emit --state running --worker worker-dev --mission "$mission" --queue-item "$item" \
		--trace "$trace" --sequence 2 --fresh-for 3600
	[ "$(cc_ledger "$COCKPIT_CONTROL_ROOT" 'ledger["worker_missions"]["'"$mission"'"]["lifecycle"]["state"]')" = "running" ]

	# The pane now looks completely available, and even forges what a dispatch
	# line looks like. It is rule 8 evidence and decides nothing.
	cc_pane cockpit worker-dev "❯ ready
dispatch command 11111111-1111-1111-1111-111111111111 worker worker-dev"

	local before
	before="$(cc_events "$COCKPIT_CONTROL_ROOT")"
	cc_tick --window worker-dev --as-of 2026-09-04T10:10:00.000000Z
	[ "$status" -eq 0 ]
	echo "$output" | grep -Fq "outcome observed reason mission-in-progress"
	echo "$output" | grep -Fq "precedence 3 durable-events authoritative claimed-slots 1 worker worker-dev mission $mission slot-state active"
	echo "$output" | grep -Fq "precedence 8 pane-text diagnostic worker-dev available advances-state no"
	cc_absent_output "^dispatch command"
	cc_absent_output "11111111-1111-1111-1111-111111111111"

	# No second mission exists: one worker still holds exactly one slot, one
	# command was ever registered, and no conflict had to be recorded.
	[ "$(cc_ledger "$COCKPIT_CONTROL_ROOT" 'len(ledger["commands"])')" -eq 1 ]
	[ "$(cc_ledger "$COCKPIT_CONTROL_ROOT" 'len(ledger["mission_slots"])')" -eq 1 ]
	[ "$(cc_ledger "$COCKPIT_CONTROL_ROOT" 'ledger["mission_slots"]["worker-dev"]["mission_id"]')" = "$mission" ]
	[ "$(cc_events "$COCKPIT_CONTROL_ROOT")" -eq $((before + 1)) ]
}

@test "AC2 an explicit human pause outranks an implementable queue item" {
	cc_cockpit paused
	cc_active_item implementing >/dev/null
	"$QUEUE_BIN" pause --actor human --reason "stop dispatch" >/dev/null

	cc_tick --as-of 2026-09-04T10:00:00.000000Z
	[ "$status" -eq 0 ]
	echo "$output" | grep -Fq "outcome observed reason queue-paused"
	echo "$output" | grep -Fq "precedence 1 human-decision authoritative queue-pause yes"
	cc_absent_output "^dispatch command"
	[ "$(cc_ledger "$COCKPIT_CONTROL_ROOT" 'len(ledger["commands"])')" -eq 0 ]

	# Resuming is also a human decision, and it is the queue that owns both.
	"$QUEUE_BIN" resume --actor human --reason "continue" >/dev/null
	cc_tick --as-of 2026-09-04T10:05:00.000000Z
	[ "$status" -eq 0 ]
	echo "$output" | grep -Fq "outcome dispatched reason queue-item-implementable"
}

@test "AC2 a hand-edited ledger cannot make a claimed worker look idle" {
	cc_cockpit forged
	cc_active_item implementing >/dev/null
	cc_tick --as-of 2026-09-04T10:00:00.000000Z
	[ "$status" -eq 0 ]

	# Forge the derived projection into claiming the worker holds nothing. Rule 5
	# is below rules 3 and 4, so the committed events must still win: the forged
	# projection is discarded and rebuilt from them rather than obeyed, and the
	# tick then reports the mission that actually exists.
	python3 -c '
import json
import sys

path = sys.argv[1] + "/ledger.json"
with open(path) as handle:
    ledger = json.load(handle)
ledger["mission_slots"] = {}
ledger["controller"] = None
with open(path, "w") as handle:
    handle.write(json.dumps(ledger, indent=2, sort_keys=True) + "\n")
' "$COCKPIT_CONTROL_ROOT"

	cc_tick --as-of 2026-09-04T10:05:00.000000Z
	[ "$status" -eq 0 ]
	echo "$output" | grep -Fq "ledger repair repaired from divergent"
	echo "$output" | grep -Fq "outcome observed reason mission-in-progress"
	cc_absent_output "^dispatch command"
	[ "$(cc_ledger "$COCKPIT_CONTROL_ROOT" 'len(ledger["mission_slots"])')" -eq 1 ]
	[ "$(cc_ledger "$COCKPIT_CONTROL_ROOT" 'len(ledger["commands"])')" -eq 1 ]

	# Replaying again is a no-op: the tick already left the projection equal to
	# the committed rebuild, so the explicit repair has nothing left to do.
	run "$CONTROL_BIN" replay-ledger
	[ "$status" -eq 0 ]
	echo "$output" | grep -Fq "projection is current"
}

@test "AC2 the reported precedence ladder is ADR-014 order with the diagnostic rules marked" {
	cc_cockpit ladder
	cc_active_item implementing >/dev/null
	cc_pane cockpit worker-dev "● Working"
	cc_tick --window worker-dev --as-of 2026-09-04T10:00:00.000000Z
	[ "$status" -eq 0 ]

	# The eight rules appear exactly once each, in order, with the role each
	# source plays, and neither diagnostic rule ever claims to advance state.
	local expected="precedence 1 human-decision authoritative
precedence 2 queue-state authoritative
precedence 3 durable-events authoritative
precedence 4 control-journal authoritative
precedence 5 ledger-projection derived
precedence 6 worker-reports evidence
precedence 7 live-status diagnostic
precedence 8 pane-text diagnostic"
	local observed
	observed="$(printf '%s\n' "$output" | grep '^precedence ' | cut -d' ' -f1-4)"
	[ "$observed" = "$expected" ]
	printf '%s\n' "$output" | grep '^precedence 7 ' | grep -Fq "advances-state no"
	printf '%s\n' "$output" | grep '^precedence 8 ' | grep -Fq "advances-state no"
	echo "$output" | grep -Fq "precedence 8 pane-text diagnostic worker-dev working advances-state no"
}

@test "AC2 queue product state decides which worker a mission is implementable by" {
	cc_cockpit roles
	local item
	item="$(cc_active_item testing)"
	cc_tick --as-of 2026-09-04T10:00:00.000000Z
	[ "$status" -eq 0 ]
	echo "$output" | grep -Fq "dispatch command"
	echo "$output" | grep -Fq "worker worker-test"

	# A product state that is overseer or human work is not implementable at all,
	# so the controller records that rather than inventing a mission for it.
	cc_cockpit shaping
	cc_active_item shaping >/dev/null
	cc_tick --as-of 2026-09-04T10:00:00.000000Z
	[ "$status" -eq 0 ]
	echo "$output" | grep -Fq "outcome observed reason queue-item-not-implementable"
	cc_absent_output "^dispatch command"
}

@test "AC2 the controller mirrors the exact product-work vocabulary cockpit-queue owns" {
	run python3 -c '
import re
import sys

sys.path.insert(0, sys.argv[1])
import cockpit_control

source = open(sys.argv[1] + "/cockpit-queue", encoding="utf-8").read()


def literal(name):
    match = re.search(name + r"\s*=\s*\{(.*?)\}", source, re.S)
    if match is None:
        raise SystemExit("cockpit-queue no longer declares " + name)
    return sorted(re.findall(r"\"([^\"]+)\"", match.group(1)))


active = literal("ACTIVE_STATES")
terminal = literal("TERMINAL_STATES")
if active != sorted(cockpit_control.QUEUE_ACTIVE_STATES):
    raise SystemExit("active queue states drifted: %r vs %r" % (active, cockpit_control.QUEUE_ACTIVE_STATES))
if terminal != sorted(cockpit_control.QUEUE_TERMINAL_STATES):
    raise SystemExit("terminal queue states drifted")
if sorted(cockpit_control.QUEUE_STATES) != sorted(set(active + terminal + ["queued", "blocked"])):
    raise SystemExit("queue state vocabulary drifted")
for state in cockpit_control.CONTROLLER_WORKER_BY_QUEUE_STATE:
    if state not in active:
        raise SystemExit("dispatchable state %r is not an active queue state" % state)
print("queue vocabulary agrees")
' "$MODULE_DIR"
	[ "$status" -eq 0 ]
	echo "$output" | grep -Fq "queue vocabulary agrees"
}

@test "AC2 two active queue items block dispatch and name the repair cockpit-queue owns" {
	cc_cockpit ambiguous
	local first second
	first="$(cc_active_item implementing)"
	second="$("$QUEUE_BIN" enqueue --text "/the-copilot-build-method deliver a second change" --title second)"
	run "$QUEUE_BIN" transition "$second" testing --reason "a second active item"
	[ "$status" -eq 0 ]

	# Rule 2 owns which product work exists. Two active items is a question only
	# cockpit-queue can answer, so the controller refuses to pick one.
	cc_tick --as-of 2026-09-04T10:00:00.000000Z
	[ "$status" -eq 1 ]
	echo "$output" | grep -Fq "tick recorded action record-observation outcome blocked reason multiple-active-queue-items"
	echo "$output" | grep -Fq "dispatch is blocked (multiple-active-queue-items)"
	echo "$output" | grep -Fq "[repair: keep the earliest valid active queue item and transition the others with cockpit-queue before dispatch]"
	echo "$output" | grep -Fq "precedence 2 queue-state authoritative items 2 active 2 queued 0 item - state -"
	cc_absent_output "^dispatch command"
	[ "$(cc_events "$COCKPIT_CONTROL_ROOT")" -eq 1 ]
	[ "$(cc_ledger "$COCKPIT_CONTROL_ROOT" 'len(ledger["commands"])')" -eq 0 ]
	[ "$(cc_ledger "$COCKPIT_CONTROL_ROOT" 'ledger["controller"]["reason"]')" = "multiple-active-queue-items" ]
	[ "$(cc_ledger "$COCKPIT_CONTROL_ROOT" 'ledger["controller"]["outcome"]')" = "blocked" ]

	# Both items it refused to choose between are recorded as the evidence the
	# refusal rests on, and neither queue item was touched by the controller.
	grep -Fq "queue:$first" "$COCKPIT_CONTROL_ROOT/events/$(ls "$COCKPIT_CONTROL_ROOT/events")"
	grep -Fq "queue:$second" "$COCKPIT_CONTROL_ROOT/events/$(ls "$COCKPIT_CONTROL_ROOT/events")"
	[ "$("$QUEUE_BIN" list --json | python3 -c 'import json,sys; print(len(json.load(sys.stdin)))')" -eq 2 ]

	# The same ambiguity is not investigated again.
	cc_tick --as-of 2026-09-04T10:05:00.000000Z
	[ "$status" -eq 1 ]
	echo "$output" | grep -Fq "tick unchanged action none"
	[ "$(cc_events "$COCKPIT_CONTROL_ROOT")" -eq 1 ]

	# Resolving it with cockpit-queue, which owns the answer, unblocks dispatch.
	run "$QUEUE_BIN" reject "$second" --reason "keep the earliest active item"
	[ "$status" -eq 0 ]
	cc_tick --as-of 2026-09-04T10:10:00.000000Z
	[ "$status" -eq 0 ]
	echo "$output" | grep -Fq "outcome dispatched reason queue-item-implementable"
	[ "$(cc_ledger "$COCKPIT_CONTROL_ROOT" 'ledger["mission_slots"]["worker-dev"]["queue_item_id"]')" = "$first" ]
}

# --- AC3: no-action ticks terminate cleanly ----------------------------------

@test "AC3 a no-action tick persists its observation once and then persists nothing" {
	cc_cockpit idle

	cc_tick --as-of 2026-09-04T10:00:00.000000Z
	[ "$status" -eq 0 ]
	echo "$output" | grep -Fq "tick recorded action record-observation outcome observed reason no-active-queue-item"
	echo "$output" | grep -Fq "events-committed 1"
	[ "$(cc_events "$COCKPIT_CONTROL_ROOT")" -eq 1 ]

	# Every later tick over the same evidence terminates without investigating
	# it again: no event, no lock churn beyond the read, no growth.
	local index
	for index in 2 3 4; do
		cc_tick --as-of "2026-09-04T10:0${index}:00.000000Z"
		[ "$status" -eq 0 ]
		echo "$output" | grep -Fq "tick unchanged action none"
		echo "$output" | grep -Fq "events-committed 0"
	done
	[ "$(cc_events "$COCKPIT_CONTROL_ROOT")" -eq 1 ]
	[ "$(cc_ledger "$COCKPIT_CONTROL_ROOT" 'ledger["revision"]')" -eq 1 ]

	# A genuinely new situation is a new observation, not a suppressed one.
	cc_active_item implementing >/dev/null
	cc_tick --as-of 2026-09-04T10:10:00.000000Z
	[ "$status" -eq 0 ]
	echo "$output" | grep -Fq "outcome dispatched"
	[ "$(cc_events "$COCKPIT_CONTROL_ROOT")" -eq 2 ]
}

@test "AC3 BDD3 disagreeing roots block dispatch and persist exactly one conflict" {
	cc_cockpit roots
	cc_active_item implementing >/dev/null
	local declared="$COCKPIT_QUEUE_ROOT"
	local other="$BATS_TEST_TMPDIR/other-cockpit-queue"
	mkdir -p "$other"

	export COCKPIT_QUEUE_ROOT="$other"
	cc_tick --as-of 2026-09-04T10:00:00.000000Z
	[ "$status" -eq 1 ]
	echo "$output" | grep -Fq "outcome blocked reason queue-root-disagreement"
	echo "$output" | grep -Fq "dispatch is blocked (queue-root-disagreement)"
	echo "$output" | grep -Fq "[repair: export COCKPIT_QUEUE_ROOT to the queue root $declared"
	cc_absent_output "^dispatch command"
	[ "$(cc_events "$COCKPIT_CONTROL_ROOT")" -eq 1 ]
	[ "$(cc_ledger "$COCKPIT_CONTROL_ROOT" 'ledger["controller"]["outcome"]')" = "blocked" ]
	[ "$(cc_ledger "$COCKPIT_CONTROL_ROOT" 'ledger["controller"]["reason"]')" = "queue-root-disagreement" ]
	[ "$(cc_ledger "$COCKPIT_CONTROL_ROOT" 'len(ledger["commands"])')" -eq 0 ]

	# Ticking again on the same disagreement persists no second conflict: the
	# controller has already said this once and does not re-investigate.
	cc_tick --as-of 2026-09-04T10:05:00.000000Z
	[ "$status" -eq 1 ]
	echo "$output" | grep -Fq "tick unchanged action none"
	[ "$(cc_events "$COCKPIT_CONTROL_ROOT")" -eq 1 ]

	# Correcting the root unblocks the controller without any repair command.
	export COCKPIT_QUEUE_ROOT="$declared"
	cc_tick --as-of 2026-09-04T10:10:00.000000Z
	[ "$status" -eq 0 ]
	echo "$output" | grep -Fq "outcome dispatched reason queue-item-implementable"
	[ "$(cc_events "$COCKPIT_CONTROL_ROOT")" -eq 2 ]
}

@test "AC3 an undeclared queue root blocks and an unexported one observes, each once" {
	export COCKPIT_CONTROL_ROOT="$BATS_TEST_TMPDIR/undeclared-control"
	unset COCKPIT_QUEUE_ROOT
	run "$CONTROL_BIN" init
	[ "$status" -eq 0 ]

	# A mission created without a queue boundary has no product-work authority
	# to read at all. That is something to record, not something to escalate.
	cc_tick --as-of 2026-09-04T10:00:00.000000Z
	[ "$status" -eq 0 ]
	echo "$output" | grep -Fq "tick recorded action record-observation outcome observed reason no-queue-root-declared"
	echo "$output" | grep -Fq "precedence 2 queue-state authoritative queue unobserved"
	cc_absent_output "^dispatch command"
	cc_absent_output "dispatch is blocked"
	[ "$(cc_events "$COCKPIT_CONTROL_ROOT")" -eq 1 ]

	cc_tick --as-of 2026-09-04T10:01:00.000000Z
	[ "$status" -eq 0 ]
	echo "$output" | grep -Fq "tick unchanged action none"
	[ "$(cc_events "$COCKPIT_CONTROL_ROOT")" -eq 1 ]

	# Exporting a queue root the mission never declared is a disagreement about
	# which cockpit this is, so it blocks and names both ways out of it.
	local exported="$BATS_TEST_TMPDIR/undeclared-queue"
	mkdir -p "$exported"
	export COCKPIT_QUEUE_ROOT="$exported"
	cc_tick --as-of 2026-09-04T10:05:00.000000Z
	[ "$status" -eq 1 ]
	echo "$output" | grep -Fq "tick recorded action record-observation outcome blocked reason queue-root-undeclared"
	echo "$output" | grep -Fq "dispatch is blocked (queue-root-undeclared)"
	echo "$output" | grep -Fq "[repair: declare canonical_roots.queue_root as $exported when the mission is created, or unset COCKPIT_QUEUE_ROOT in this shell]"
	cc_absent_output "^dispatch command"
	[ "$(cc_events "$COCKPIT_CONTROL_ROOT")" -eq 2 ]
	[ "$(cc_ledger "$COCKPIT_CONTROL_ROOT" 'ledger["controller"]["outcome"]')" = "blocked" ]
	[ "$(cc_ledger "$COCKPIT_CONTROL_ROOT" 'ledger["controller"]["reason"]')" = "queue-root-undeclared" ]

	cc_tick --as-of 2026-09-04T10:06:00.000000Z
	[ "$status" -eq 1 ]
	echo "$output" | grep -Fq "tick unchanged action none"
	[ "$(cc_events "$COCKPIT_CONTROL_ROOT")" -eq 2 ]

	# The decision keeps its own guard for evidence carrying no product-work
	# authority at all. A tick can only build such a record together with the
	# root fault above, so the guard is exercised here directly: no evidence
	# without a queue may ever fall through to a dispatch.
	run python3 -c '
import dataclasses
import sys

sys.path.insert(0, sys.argv[1])
import cockpit_control

tick = cockpit_control.ControllerTick(sys.argv[2], as_of="2026-09-04T10:07:00.000000Z")
evidence = tick._gathered_evidence()
if evidence.queue is not None:
    raise SystemExit("this store declares no queue root")
probe = dataclasses.replace(evidence, queue_root_fault=None)
action = cockpit_control.select_controller_action(probe)
print("action", action.kind, action.outcome, action.reason)
print("blocking", action.reason in cockpit_control.CONTROLLER_BLOCKING_REASONS)
' "$MODULE_DIR" "$COCKPIT_CONTROL_ROOT"
	[ "$status" -eq 0 ]
	echo "$output" | grep -Fq "action record-observation observed no-queue-root-declared"
	echo "$output" | grep -Fq "blocking False"
	[ "$(cc_events "$COCKPIT_CONTROL_ROOT")" -eq 2 ]
}

@test "AC3 an unreadable queue root blocks the tick and is never repaired by it" {
	cc_cockpit unreadable
	cc_active_item implementing >/dev/null
	local before after
	before="$(cc_control_contents "$COCKPIT_QUEUE_ROOT")"
	printf 'not a queue item\n' >"$COCKPIT_QUEUE_ROOT/items/QI-broken.yaml"

	cc_tick --as-of 2026-09-04T10:00:00.000000Z
	[ "$status" -eq 1 ]
	echo "$output" | grep -Fq "outcome blocked reason queue-root-unreadable"
	echo "$output" | grep -Fq "malformed items/QI-broken.yaml"
	cc_absent_output "^dispatch command"
	printf '%s\n' "$output" >"$BATS_TEST_TMPDIR/unreadable.log"
	cc_no_traceback "$BATS_TEST_TMPDIR/unreadable.log"

	# The queue belongs to cockpit-queue: the controller changed nothing under it.
	rm -f "$COCKPIT_QUEUE_ROOT/items/QI-broken.yaml"
	after="$(cc_control_contents "$COCKPIT_QUEUE_ROOT")"
	[ "$before" = "$after" ]
}

@test "AC3 a malformed queue journal blocks the tick without a traceback" {
	cc_cockpit journal
	cc_active_item implementing >/dev/null
	printf '{"type": "queue-paused"\n' >>"$COCKPIT_QUEUE_ROOT/events.jsonl"

	cc_tick --as-of 2026-09-04T10:00:00.000000Z
	[ "$status" -eq 1 ]
	echo "$output" | grep -Fq "outcome blocked reason queue-root-unreadable"
	echo "$output" | grep -Fq "malformed events.jsonl:"
	printf '%s\n' "$output" >"$BATS_TEST_TMPDIR/journal.log"
	cc_no_traceback "$BATS_TEST_TMPDIR/journal.log"
}

@test "AC3 tick --dry-run reports the decision and changes not one byte" {
	cc_cockpit dryrun
	cc_active_item implementing >/dev/null
	local control_before queue_before

	# The first dry run also creates the process-scoped transition guard the E1
	# ownership protocol acquires under, exactly as `publish-event --dry-run`
	# does.  Byte-inertness is therefore asserted twice: the established
	# "nothing committed, nothing pending, no published lock" contract here, and
	# a full content comparison across the second run below.
	queue_before="$(cc_control_snapshot "$COCKPIT_QUEUE_ROOT")"
	cc_tick --dry-run --as-of 2026-09-04T10:00:00.000000Z
	[ "$status" -eq 0 ]
	echo "$output" | grep -Fq "tick would-dispatch action dispatch-mission"
	echo "$output" | grep -Fq "would take action dispatch-mission"
	echo "$output" | grep -Fq "events-committed 0"
	echo "$output" | grep -Fq "dispatch command"
	[ "$(find "$COCKPIT_CONTROL_ROOT/events" -type f | wc -l)" -eq 0 ]
	[ "$(find "$COCKPIT_CONTROL_ROOT/pending" -type f | wc -l)" -eq 0 ]
	[ ! -e "$COCKPIT_CONTROL_ROOT/locks/control.lock" ]

	control_before="$(cc_control_contents "$COCKPIT_CONTROL_ROOT")"
	cc_tick --dry-run --as-of 2026-09-04T10:01:00.000000Z
	[ "$status" -eq 0 ]
	echo "$output" | grep -Fq "tick would-dispatch action dispatch-mission"
	[ "$(cc_control_contents "$COCKPIT_CONTROL_ROOT")" = "$control_before" ]
	[ "$(cc_control_snapshot "$COCKPIT_QUEUE_ROOT")" = "$queue_before" ]

	# A dry-run observation is equally inert.
	cc_cockpit dryrun-idle
	cc_tick --dry-run --as-of 2026-09-04T10:00:00.000000Z
	[ "$status" -eq 0 ]
	echo "$output" | grep -Fq "tick would-record action record-observation"
	control_before="$(cc_control_contents "$COCKPIT_CONTROL_ROOT")"
	cc_tick --dry-run --as-of 2026-09-04T10:01:00.000000Z
	[ "$status" -eq 0 ]
	echo "$output" | grep -Fq "tick would-record action record-observation"
	[ "$(cc_control_contents "$COCKPIT_CONTROL_ROOT")" = "$control_before" ]
	[ "$(find "$COCKPIT_CONTROL_ROOT/events" -type f | wc -l)" -eq 0 ]

	# And a dry-run refusal still refuses, still inertly.
	COCKPIT_QUEUE_ROOT="$BATS_TEST_TMPDIR/nowhere-queue" cc_tick --dry-run \
		--as-of 2026-09-04T10:02:00.000000Z
	[ "$status" -eq 1 ]
	[ "$(cc_control_contents "$COCKPIT_CONTROL_ROOT")" = "$control_before" ]
	[ "$(find "$COCKPIT_CONTROL_ROOT/events" -type f | wc -l)" -eq 0 ]
}

# --- structural invariants and fail-closed refusals --------------------------

@test "the one-mission-per-worker rule lives in the fold, not in the tick" {
	cc_cockpit fold
	local item mission
	item="$(cc_active_item implementing)"
	cc_tick --as-of 2026-09-04T10:00:00.000000Z
	[ "$status" -eq 0 ]
	mission="$(cc_ledger "$COCKPIT_CONTROL_ROOT" 'ledger["mission_slots"]["worker-dev"]["mission_id"]')"

	# Forge a second dispatch straight through the raw event primitive, which no
	# controller decision is involved in at all. The deterministic fold must
	# still refuse it and record the conflict.
	run python3 -c '
import json
import sys

sys.path.insert(0, sys.argv[1])
import cockpit_control

root = sys.argv[2]
command_id = "22222222-2222-2222-2222-222222222222"
mission_id = "33333333-3333-3333-3333-333333333333"
trace_id = "44444444-4444-4444-4444-444444444444"
dispatch = cockpit_control.build_controller_dispatch(
    command_id=command_id,
    mission_id=mission_id,
    worker_id="worker-dev",
    queue_item_id=sys.argv[3],
    trace_id=trace_id,
    reason=cockpit_control.CONTROLLER_REASON_IMPLEMENTABLE,
    state_key=cockpit_control.controller_state_key({"forged": True}),
    evidence_refs=["queue:" + sys.argv[3]],
)
envelope = cockpit_control.build_command_envelope(
    command_id=command_id,
    command_type=cockpit_control.COMMAND_TYPE_MISSION_DISPATCH,
    mission_id=mission_id,
    queue_item_id=sys.argv[3],
    target_kind="worker",
    target_id="worker-dev",
    trace_id=trace_id,
    payload_digest=cockpit_control.command_payload_digest({"forged": True}),
    control_root=root,
)
result = cockpit_control.publish_control_event(
    root,
    "command-registered",
    actor="forger",
    payload={"command_envelope": envelope, "controller_dispatch": dispatch},
)
print("committed", result.committed)
' "$MODULE_DIR" "$COCKPIT_CONTROL_ROOT" "$item"
	[ "$status" -eq 0 ]
	echo "$output" | grep -Fq "committed True"

	# The forged event is retained as durable audit evidence and moved nothing:
	# the worker still holds exactly one slot, still for the original mission.
	[ "$(cc_ledger "$COCKPIT_CONTROL_ROOT" 'ledger["mission_slots"]["worker-dev"]["mission_id"]')" = "$mission" ]
	[ "$(cc_ledger "$COCKPIT_CONTROL_ROOT" 'ledger["mission_slots"]["worker-dev"]["state"]')" = "reserved" ]
	[ "$(cc_ledger "$COCKPIT_CONTROL_ROOT" 'ledger["mission_slots"]["worker-dev"]["conflicts"][0]["reason"]')" = "second-active-slot" ]
	[ "$(cc_ledger "$COCKPIT_CONTROL_ROOT" 'ledger["mission_slots"]["worker-dev"]["conflicts"][0]["source"]')" = "dispatch" ]
}

@test "a controller dispatch cannot be minted through the generic command surface" {
	cc_cockpit managed
	local out="$BATS_TEST_TMPDIR/managed.out" err="$BATS_TEST_TMPDIR/managed.err"
	local code=0
	"$CONTROL_BIN" register-command \
		--command-id 55555555-5555-5555-5555-555555555555 \
		--type mission-dispatch \
		--mission 66666666-6666-6666-6666-666666666666 \
		--queue-item QI-1 --target worker-dev \
		--trace 77777777-7777-7777-7777-777777777777 \
		--payload '{"brief": "do the thing"}' >"$out" 2>"$err" || code=$?
	[ "$code" -ne 0 ]
	[ ! -s "$out" ]
	head -n 1 "$err" | grep -Fq "cockpit-control: command type 'mission-dispatch' is a managed mission command"
	cc_no_traceback "$err"
	[ "$(cc_events "$COCKPIT_CONTROL_ROOT")" -eq 0 ]
}

@test "a controller observation cannot be forged under an unrelated event type" {
	cc_cockpit forgery
	local out="$BATS_TEST_TMPDIR/forge.out" err="$BATS_TEST_TMPDIR/forge.err"
	local payload code=0
	payload="$(python3 -c '
import json
import sys

sys.path.insert(0, sys.argv[1])
import cockpit_control

record = cockpit_control.build_controller_observation(
    outcome=cockpit_control.CONTROLLER_OBSERVED,
    reason=cockpit_control.CONTROLLER_REASON_QUEUE_EMPTY,
    state_key=cockpit_control.controller_state_key({"forged": True}),
    observed_at="2026-09-04T10:00:00.000000Z",
)
print(json.dumps({"controller_observation": record}))
' "$MODULE_DIR")"

	"$CONTROL_BIN" publish-event --type overseer-decided \
		--payload "$payload" >"$out" 2>"$err" || code=$?
	[ "$code" -ne 0 ]
	[ ! -s "$out" ]
	head -n 1 "$err" | grep -Fq "cockpit-control: "
	grep -Fq "controller-observation-<outcome>" "$err"
	cc_no_traceback "$err"
	[ "$(cc_events "$COCKPIT_CONTROL_ROOT")" -eq 0 ]

	# The mirror image is refused too: the declared type without the record.
	code=0
	"$CONTROL_BIN" publish-event --type controller-observation-observed \
		>"$out" 2>"$err" || code=$?
	[ "$code" -ne 0 ]
	grep -Fq "without its payload.controller_observation record" "$err"
	[ "$(cc_events "$COCKPIT_CONTROL_ROOT")" -eq 0 ]
}

@test "the fold refuses an unchanged observation even when the tick is bypassed" {
	cc_cockpit unchanged
	cc_tick --as-of 2026-09-04T10:00:00.000000Z
	[ "$status" -eq 0 ]
	local key revision
	key="$(cc_ledger "$COCKPIT_CONTROL_ROOT" 'ledger["controller"]["state_key"]')"
	revision="$(cc_ledger "$COCKPIT_CONTROL_ROOT" 'ledger["controller"]["revision"]')"

	# Republish the identical observation straight through the raw event
	# primitive, with no controller decision involved at all. The event is
	# committed as durable audit evidence and the deterministic fold must refuse
	# to let it move the materialized controller state.
	run python3 -c '
import json
import sys

sys.path.insert(0, sys.argv[1])
import cockpit_control

record = cockpit_control.build_controller_observation(
    outcome=cockpit_control.CONTROLLER_OBSERVED,
    reason=cockpit_control.CONTROLLER_REASON_QUEUE_EMPTY,
    state_key=sys.argv[3],
    observed_at="2026-09-04T11:00:00.000000Z",
)
result = cockpit_control.publish_control_event(
    sys.argv[2],
    "controller-observation-observed",
    actor="forger",
    payload={"controller_observation": record},
)
state = cockpit_control.read_mission_state(result.root)
print("committed", result.committed)
print("fold", state.mission_outcomes[result.event_id][1])
' "$MODULE_DIR" "$COCKPIT_CONTROL_ROOT" "$key"
	[ "$status" -eq 0 ]
	echo "$output" | grep -Fq "committed True"
	echo "$output" | grep -Fq "fold retained-unchanged-observation"

	# Two committed events, one unchanged controller decision still pinned to the
	# revision that first recorded it.
	[ "$(cc_events "$COCKPIT_CONTROL_ROOT")" -eq 2 ]
	[ "$(cc_ledger "$COCKPIT_CONTROL_ROOT" 'ledger["controller"]["revision"]')" -eq "$revision" ]
	[ "$(cc_ledger "$COCKPIT_CONTROL_ROOT" 'ledger["controller"]["state_key"]')" = "$key" ]
	[ "$(cc_ledger "$COCKPIT_CONTROL_ROOT" 'ledger["controller"]["recorded_at"]')" != "2026-09-04T11:00:00.000000Z" ]
}

@test "every controller decision is re-derived byte-exactly from events alone" {
	cc_cockpit cold
	local item mission projected
	item="$(cc_active_item implementing)"
	cc_tick --as-of 2026-09-04T10:00:00.000000Z
	[ "$status" -eq 0 ]
	mission="$(cc_ledger "$COCKPIT_CONTROL_ROOT" 'ledger["mission_slots"]["worker-dev"]["mission_id"]')"
	cc_emit --state accepted --worker worker-dev --mission "$mission" --queue-item "$item" \
		--trace 88888888-8888-8888-8888-888888888888 --sequence 1 --fresh-for 3600
	cc_tick --as-of 2026-09-04T10:05:00.000000Z
	[ "$status" -eq 0 ]
	projected="$(cat "$COCKPIT_CONTROL_ROOT/ledger.json")"

	# Destroy every scrap of derived state. Only immutable committed events and
	# root metadata remain.
	rm -f "$COCKPIT_CONTROL_ROOT/ledger.json" "$COCKPIT_CONTROL_ROOT/events.jsonl"
	run "$CONTROL_BIN" replay-ledger
	[ "$status" -eq 0 ]
	echo "$output" | grep -Fq "rebuilt ledger.json"
	[ "$projected" = "$(cat "$COCKPIT_CONTROL_ROOT/ledger.json")" ]

	# Replaying again is idempotent, and the controller reaches the same
	# conclusion from the rebuilt state without acting again.
	run "$CONTROL_BIN" replay-ledger
	[ "$status" -eq 0 ]
	[ "$projected" = "$(cat "$COCKPIT_CONTROL_ROOT/ledger.json")" ]
	local before
	before="$(cc_events "$COCKPIT_CONTROL_ROOT")"
	cc_tick --as-of 2026-09-04T10:10:00.000000Z
	[ "$status" -eq 0 ]
	echo "$output" | grep -Fq "tick unchanged action none"
	echo "$output" | grep -Fq "outcome observed reason mission-in-progress"
	[ "$(cc_events "$COCKPIT_CONTROL_ROOT")" -eq "$before" ]
}

@test "concurrent ticks over one situation register exactly one dispatch command" {
	cc_cockpit concurrent
	cc_active_item implementing >/dev/null
	local barrier="$BATS_TEST_TMPDIR/concurrent-barrier"
	local index

	for index in 1 2 3 4 5 6 7 8; do
		(
			while [ ! -f "$barrier" ]; do :; done
			code=0
			"$OVERSEER_BIN" tick --as-of 2026-09-04T10:00:00.000000Z \
				>"$BATS_TEST_TMPDIR/concurrent-$index.out" 2>&1 || code=$?
			printf '%s\n' "$code" >"$BATS_TEST_TMPDIR/concurrent-$index.code"
		) &
	done
	: >"$barrier"
	wait

	# Delivery is uncertain, so several processes legitimately retry the same
	# command ID; exactly one may register it, exactly one may claim the worker's
	# slot, and no conflict has to be recorded to make that true.
	[ "$(cc_ledger "$COCKPIT_CONTROL_ROOT" 'len(ledger["commands"])')" -eq 1 ]
	[ "$(cc_ledger "$COCKPIT_CONTROL_ROOT" 'len(ledger["mission_slots"])')" -eq 1 ]
	[ "$(cc_ledger "$COCKPIT_CONTROL_ROOT" 'ledger["mission_slots"]["worker-dev"]["state"]')" = "reserved" ]
	[ "$(cc_ledger "$COCKPIT_CONTROL_ROOT" 'len(ledger["mission_slots"]["worker-dev"]["conflicts"])')" -eq 0 ]
	[ "$(cc_ledger "$COCKPIT_CONTROL_ROOT" 'ledger["active_mission_id"]')" = \
		"$(cc_ledger "$COCKPIT_CONTROL_ROOT" 'ledger["mission_slots"]["worker-dev"]["mission_id"]')" ]
	run "$CONTROL_BIN" validate
	[ "$status" -eq 0 ]

	# Exactly one of the eight processes actually created the mission, and each
	# process reported exactly one decision that committed at most one event.
	[ "$(grep -h '^tick ' "$BATS_TEST_TMPDIR"/concurrent-*.out | grep -c 'tick dispatched')" -eq 1 ]
	for index in 1 2 3 4 5 6 7 8; do
		[ "$(grep -c '^tick ' "$BATS_TEST_TMPDIR/concurrent-$index.out")" -eq 1 ]
		grep -Eq 'events-committed (0|1) ' "$BATS_TEST_TMPDIR/concurrent-$index.out"
		# Concurrency is ordinary operation, not an incident: every one of these
		# processes saw a healthy store, so every one of them must exit 0 and
		# none of them may blame the derived projection for the window another
		# writer legitimately had open (architecture section 8.3 steps 4 to 8).
		[ "$(cat "$BATS_TEST_TMPDIR/concurrent-$index.code")" -eq 0 ]
		cc_absent "ledger-projection-divergent" "$BATS_TEST_TMPDIR/concurrent-$index.out"
		cc_absent "outcome blocked" "$BATS_TEST_TMPDIR/concurrent-$index.out"
	done
	cc_absent_events "ledger-projection-divergent"
}

@test "a tick refuses forgeable identifiers and unrepresentable moments" {
	cc_cockpit inputs
	local out="$BATS_TEST_TMPDIR/inputs.out" err="$BATS_TEST_TMPDIR/inputs.err"
	local code=0

	# A worker window is rendered into a parseable position of the precedence
	# payload, so one carrying whitespace could forge an entire extra rule line.
	"$OVERSEER_BIN" tick --window "worker dev" >"$out" 2>"$err" || code=$?
	[ "$code" -ne 0 ]
	[ ! -s "$out" ]
	head -n 1 "$err" | grep -Fq "cockpit-overseer: "
	grep -Fq "printable non-whitespace ASCII" "$err"
	cc_no_traceback "$err"

	code=0
	"$OVERSEER_BIN" tick --as-of "not-a-moment" >"$out" 2>"$err" || code=$?
	[ "$code" -ne 0 ]
	[ ! -s "$out" ]
	head -n 1 "$err" | grep -Fq "cockpit-overseer: "
	cc_no_traceback "$err"

	# A queue item whose identifier could forge a record boundary is refused by
	# the reader rather than rendered, and no dispatch is minted for it.
	mkdir -p "$COCKPIT_QUEUE_ROOT/items"
	python3 -c '
import json
import os
import sys

item_id = "QI-a\tb"
record = {"id": item_id, "state": "implementing", "created_at": "2026-09-04T00:00:00Z"}
with open(os.path.join(sys.argv[1], "items", item_id + ".yaml"), "w") as handle:
    handle.write(json.dumps(record, indent=2, sort_keys=True) + "\n")
' "$COCKPIT_QUEUE_ROOT"
	cc_tick --as-of 2026-09-04T10:00:00.000000Z
	[ "$status" -eq 1 ]
	echo "$output" | grep -Fq "outcome blocked reason queue-root-unreadable"
	cc_absent_output "^dispatch command"
	[ "$(cc_events "$COCKPIT_CONTROL_ROOT")" -eq 1 ]

	# Nothing a queue item says beyond its identifier, state, and creation
	# moment is ever read, so item prose can never reach a committed event.
	cc_absent "source_text" "$COCKPIT_CONTROL_ROOT/events/$(ls "$COCKPIT_CONTROL_ROOT/events")"
}

@test "a refusal about the queue root names the queue root, not the control root" {
	cc_cockpit rootnames
	local out="$BATS_TEST_TMPDIR/queue-root.out" err="$BATS_TEST_TMPDIR/queue-root.err"
	local code=0

	# A refusal that names the wrong variable sends an operator to repair a
	# setting that is already correct, which is a dead end rather than a repair.
	COCKPIT_QUEUE_ROOT="relative/queue" "$OVERSEER_BIN" tick >"$out" 2>"$err" || code=$?
	[ "$code" -ne 0 ]
	[ ! -s "$out" ]
	head -n 1 "$err" | grep -Fq "cockpit-overseer: "
	grep -Fq "shell COCKPIT_QUEUE_ROOT must be an absolute path" "$err"
	cc_absent "COCKPIT_CONTROL_ROOT" "$err"
	cc_no_traceback "$err"

	# The queue boundary the mission itself declares is refused by the same name.
	run python3 -c '
import sys
from pathlib import Path

sys.path.insert(0, sys.argv[1])
import cockpit_control

try:
    cockpit_control.observe_queue(Path("relative/queue"))
except cockpit_control.ControlStoreError as exc:
    print("refused", exc)
else:
    print("BUG: a relative queue root was read")
' "$MODULE_DIR"
	[ "$status" -eq 0 ]
	echo "$output" | grep -Fq "refused declared COCKPIT_QUEUE_ROOT must be an absolute path"
	cc_absent_output "BUG"

	# Naming the boundary is per call site, not a renaming: the control root
	# keeps the diagnostic every other cockpit-control surface already prints.
	code=0
	COCKPIT_CONTROL_ROOT="relative/store" "$OVERSEER_BIN" tick >"$out" 2>"$err" || code=$?
	[ "$code" -ne 0 ]
	grep -Fq "shell COCKPIT_CONTROL_ROOT must be an absolute path" "$err"
	cc_absent "COCKPIT_QUEUE_ROOT" "$err"
	cc_no_traceback "$err"
}

@test "a tick refuses a control root that is missing, relative, or unconfigured" {
	local out="$BATS_TEST_TMPDIR/root.out" err="$BATS_TEST_TMPDIR/root.err"
	local code=0
	"$OVERSEER_BIN" tick >"$out" 2>"$err" || code=$?
	[ "$code" -ne 0 ]
	[ ! -s "$out" ]
	grep -Fq "COCKPIT_CONTROL_ROOT is required" "$err"
	cc_no_traceback "$err"

	code=0
	COCKPIT_CONTROL_ROOT="relative/store" "$OVERSEER_BIN" tick >"$out" 2>"$err" || code=$?
	[ "$code" -ne 0 ]
	grep -Fq "must be an absolute path" "$err"
	cc_no_traceback "$err"

	code=0
	COCKPIT_CONTROL_ROOT="$BATS_TEST_TMPDIR/absent-store" "$OVERSEER_BIN" tick \
		>"$out" 2>"$err" || code=$?
	[ "$code" -ne 0 ]
	[ ! -s "$out" ]
	cc_no_traceback "$err"
}
