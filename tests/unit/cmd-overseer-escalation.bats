#!/usr/bin/env bats
# tests/unit/cmd-overseer-escalation.bats — TH3.E3.US3 minimal escalation bookkeeping

load helper

setup() {
    cc_setup_fake_home
    export PATH="$BATS_TEST_TMPDIR/bin:$PATH"
    mkdir -p "$BATS_TEST_TMPDIR/bin"
    export OVERSEER_BIN="$BATS_TEST_DIRNAME/../../bin/cockpit-overseer"
    export CONTROL_BIN="$BATS_TEST_DIRNAME/../../bin/cockpit-control"
    export QUEUE_BIN="$BATS_TEST_DIRNAME/../../bin/cockpit-queue"
    unset COCKPIT_CONTROL_ROOT COCKPIT_QUEUE_ROOT TMUX TMUX_SESSION COCKPIT_SESSION_ID COCKPIT_ID
    cc_setup_tmux_stub
}


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
	printf '%s
' "${TMUX_SESSION:-cockpit}"
	;;
*) ;;
esac
EOF
	chmod +x "$BATS_TEST_TMPDIR/bin/tmux"
}

# cc_pane <session> <window> <text> — publish the exact pane text rule 8 sees.
cc_pane() {
	mkdir -p "$BATS_TEST_TMPDIR/tmux-stub"
	printf '%s
' "$3" >"$BATS_TEST_TMPDIR/tmux-stub/$1_$2.txt"
}

# cc_declare_queue_root <control-root> <queue-root> — declare the mission's
# canonical queue boundary in the authoritative root metadata.
cc_declare_queue_root() {
	python3 -c '
import json
import sys

path = sys.argv[1] + "/control.json"
with open(path) as handle:
    record = json.load(handle)
record["canonical_roots"]["queue_root"] = sys.argv[2]
record["canonical_roots"]["planning_root"] = sys.argv[1] + "-planning"
record["canonical_roots"]["implementation_roots"] = [sys.argv[1] + "-implementation"]
record["planning_root"] = record["canonical_roots"]["planning_root"]
record["implementation_roots"] = record["canonical_roots"]["implementation_roots"]
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

# cc_slot_mission — print the mission worker-dev's single slot currently holds.
cc_slot_mission() {
	cc_ledger "$COCKPIT_CONTROL_ROOT" 'ledger["mission_slots"]["worker-dev"]["mission_id"]'
}


cc_running_mission() {
	cc_tick --as-of 2026-09-04T10:00:00.000000Z
	[ "$status" -eq 0 ]
	CC_MISSION="$(cc_slot_mission)"
	cc_accept_dispatch "$CC_MISSION" 2026-09-04T10:01:00.000000Z "$3"
	cc_emit --state running --worker worker-dev --mission "$CC_MISSION" --queue-item "$1" \
		--trace "$2" --sequence 2 --heartbeat-at 2026-09-04T10:02:00.000000Z \
		--fresh-until "$3"
}

@test "AC2 repeated blocked ticks create and advance escalation records" {
	cc_cockpit esc
	local item before
	item="$(cc_active_item implementing)"
	cc_running_mission "$item" 11111111-1111-4111-8111-111111111111 2026-09-04T10:07:00.000000Z

	# Queue authority makes this mission orphaned: the item is terminal and the
	# stale episode can only walk `cancel -> escalate`.
	run "$QUEUE_BIN" reject "$item" --reason "abandoned by operator"
	[ "$status" -eq 0 ]
	cc_tick --as-of 2026-09-04T10:20:00.000000Z
	[ "$status" -eq 0 ]
	echo "$output" | grep -Fq "reason queue-item-terminal"
	cc_tick --as-of 2026-09-04T10:30:00.000000Z
	[ "$status" -eq 0 ]
	echo "$output" | grep -Fq "reason awaiting-recovery-response"

	# First blocked tick: escalation is raised and includes actionable evidence.
	cc_tick --as-of 2026-09-04T10:40:00.000000Z
	[ "$status" -eq 1 ]
	echo "$output" | grep -Fq "outcome blocked reason bounded-recovery-escalated"
	files=("$COCKPIT_CONTROL_ROOT/escalations"/*.json)
	[ -f "${files[0]}" ]
	readout="$(python3 -c "import json,sys; doc=json.load(open(sys.argv[1])); impact=doc.get('impact',{}); print('status=' + str(doc.get('status'))); print('mission=' + str(doc.get('mission_id'))); print('queue=' + str(doc.get('queue_item_id'))); print('evidence=' + str(bool(doc.get('evidence_refs')))); print('impact_worker=' + str('worker_id' in impact)); print('impact_queue=' + str('queue_item_id' in impact)); print('options=' + str(bool(doc.get('options')))); print('pending=' + str(doc.get('pending_decision')))" "${files[0]}")"
	echo "$readout" | grep -Fxq "status=raised"
	echo "$readout" | grep -Fxq "mission=$CC_MISSION"
	echo "$readout" | grep -Fxq "queue=$item"
	echo "$readout" | grep -Fxq "evidence=True"
	echo "$readout" | grep -Fxq "impact_worker=True"
	echo "$readout" | grep -Fxq "impact_queue=True"
	echo "$readout" | grep -Fxq "options=True"
	echo "$readout" | grep -Fxq "pending=decide mission"

	# Second blocked tick: no second observation event, escalation is updated.
	before="$(cc_events "$COCKPIT_CONTROL_ROOT")"
	cc_tick --as-of 2026-09-04T10:50:00.000000Z
	[ "$status" -eq 1 ]
	echo "$output" | grep -Fq "tick unchanged action none"
	[ "$(cc_events "$COCKPIT_CONTROL_ROOT")" -eq "$before" ]
	python3 -c "import json,sys; doc=json.load(open(sys.argv[1])); print(doc.get('status')); print(doc.get('count')); print([step.get('action') for step in doc.get('attempted_recovery', [])])" "${files[0]}" | grep -Fq "escalated"
	python3 -c "import json,sys; doc=json.load(open(sys.argv[1])); print(doc.get('count'))" "${files[0]}" | grep -Fxq "2"
	python3 -c "import json,sys; doc=json.load(open(sys.argv[1])); print([step.get('action') for step in doc.get('attempted_recovery', [])])" "${files[0]}" | grep -Fq "troubleshoot"

	# Third blocked tick: decision requested and recurrent action suspended.
	cc_tick --as-of 2026-09-04T11:00:00.000000Z
	[ "$status" -eq 1 ]
	echo "$output" | grep -Fq "tick unchanged action none"
	echo "$output" | grep -Fq "outcome blocked reason bounded-recovery-escalated"
	python3 -c "import json,sys; doc=json.load(open(sys.argv[1])); print(doc.get('status')); print(doc.get('count')); print([step.get('action') for step in doc.get('attempted_recovery', [])])" "${files[0]}" | grep -Fq "decision-requested"
	python3 -c "import json,sys; doc=json.load(open(sys.argv[1])); print(doc.get('count'))" "${files[0]}" | grep -Fxq "3"
	python3 -c "import json,sys; doc=json.load(open(sys.argv[1])); print([step.get('action') for step in doc.get('attempted_recovery', [])])" "${files[0]}" | grep -Fq "escalation-recorded"
}
