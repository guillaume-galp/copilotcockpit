#!/usr/bin/env bats
# tests/unit/cmd-overseer-recovery.bats — TH3.E3.US2 stale-worker recovery and
# conflict reconciliation.
#
# The contract under test has three halves:
#
#   * a stale active worker follows one bounded ladder — nudge, troubleshoot,
#     cancel, replace, escalate — one rung per tick, each rung at most once per
#     staleness episode, and is never marked failed merely because a heartbeat
#     expired; one genuine fresh lifecycle event returns the mission to running
#     and resets the ladder;
#   * two active missions claiming one worker retain the mission that claimed the
#     slot earliest *in committed revision order*, stop further dispatch, and
#     escalate the conflict exactly once;
#   * a derived ledger that disagrees with the committed journal is repaired by
#     replaying that journal, while malformed authoritative journal data is
#     refused with the one explicit repair that exists and no state is guessed.
#
# Nothing here waits on wall-clock timing: every moment is an explicit `--as-of`
# or an explicit `--fresh-until`, and the concurrency proof starts real
# processes and lets the control lock and the deterministic fold order them.

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
# canonical queue boundary in the authoritative root metadata.
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

# cc_slot_mission — print the mission worker-dev's single slot currently holds.
cc_slot_mission() {
	cc_ledger "$COCKPIT_CONTROL_ROOT" 'ledger["mission_slots"]["worker-dev"]["mission_id"]'
}

# cc_running_mission <queue-item> <trace> <fresh-until> — dispatch one mission to
# worker-dev, have the worker accept and run it, and declare exactly when its
# freshness expires. Sets CC_MISSION.
#
# The fixtures below publish their results as variables rather than on stdout on
# purpose: a command substitution runs in a subshell, so a fixture that exported
# the control root there would build one cockpit and hand the test another.
cc_running_mission() {
	cc_tick --as-of 2026-09-04T10:00:00.000000Z
	[ "$status" -eq 0 ]
	CC_MISSION="$(cc_slot_mission)"
	cc_emit --state accepted --worker worker-dev --mission "$CC_MISSION" --queue-item "$1" \
		--trace "$2" --sequence 1 --heartbeat-at 2026-09-04T10:01:00.000000Z \
		--fresh-until "$3"
	cc_emit --state running --worker worker-dev --mission "$CC_MISSION" --queue-item "$1" \
		--trace "$2" --sequence 2 --heartbeat-at 2026-09-04T10:02:00.000000Z \
		--fresh-until "$3"
}

# cc_stale_cockpit <name> — one cockpit whose worker-dev mission is running and
# whose declared freshness expired at 10:07. Sets CC_ITEM and CC_MISSION.
cc_stale_cockpit() {
	cc_cockpit "$1"
	CC_ITEM="$(cc_active_item implementing)"
	cc_running_mission "$CC_ITEM" 11111111-1111-4111-8111-111111111111 \
		2026-09-04T10:07:00.000000Z
}

# cc_recovery_actions <root> — print every committed recovery rung, in order.
cc_recovery_actions() {
	python3 -c '
import json
import sys

with open(sys.argv[1] + "/ledger.json") as handle:
    ledger = json.load(handle)
entries = sorted(ledger["mission_recoveries"].values(), key=lambda entry: entry["revision"])
for entry in entries:
    print(entry["recovery"]["action"], entry["recovery"]["command_id"])
' "$1"
}

# cc_replacement <root> <field> — print one field of the single committed
# mission replacement record. Replacements are not projected into `ledger.json`,
# so the committed event is where the record itself is read from.
cc_replacement() {
	python3 -c '
import json
import pathlib
import sys

for path in sorted(pathlib.Path(sys.argv[1] + "/events").glob("*.json")):
    record = json.loads(path.read_text())
    payload = record.get("payload") or {}
    if "mission_replacement" in payload:
        print(payload["mission_replacement"][sys.argv[2]])
' "$1" "$2"
}

# cc_expired_cockpit <name> — one cockpit whose worker declares a two-minute
# freshness window: the plain cadence misconfiguration in which every lifecycle
# event the worker sends is already expired by the time the controller wakes.
# Sets CC_ITEM and CC_MISSION.
cc_expired_cockpit() {
	cc_cockpit "$1"
	CC_ITEM="$(cc_active_item implementing)"
	cc_tick --as-of 2026-09-04T10:00:00.000000Z
	[ "$status" -eq 0 ]
	CC_MISSION="$(cc_slot_mission)"
	cc_emit --state accepted --worker worker-dev --mission "$CC_MISSION" --queue-item "$CC_ITEM" \
		--trace 11111111-1111-4111-8111-111111111111 --sequence 1 \
		--heartbeat-at 2026-09-04T10:00:00.000000Z --fresh-until 2026-09-04T10:02:00.000000Z
	CC_SEQUENCE=2
}

# cc_expired_report <hh:mm> — the worker reports at <hh:mm> and declares the
# freshness window it always declares: two minutes, already gone by the next
# wake. The event is applied; it simply never ends the staleness.
cc_expired_report() {
	local minute
	minute="${1#*:}"
	cc_emit --state running --worker worker-dev --mission "$CC_MISSION" --queue-item "$CC_ITEM" \
		--trace 11111111-1111-4111-8111-111111111111 --sequence "$CC_SEQUENCE" \
		--heartbeat-at "2026-09-04T$1:00.000000Z" \
		--fresh-until "2026-09-04T${1%%:*}:$(printf '%02d' $((10#$minute + 2))):00.000000Z"
	CC_SEQUENCE=$((CC_SEQUENCE + 1))
}

# cc_plus_forty <hh:mm> — print the moment forty minutes after <hh:mm> on the
# fixture's single day: the window a healthy, responsive worker declares.
cc_plus_forty() {
	python3 -c '
import sys

hour, minute = (int(part) for part in sys.argv[1].split(":"))
total = hour * 60 + minute + 40
print("2026-09-04T%02d:%02d:00.000000Z" % (total // 60, total % 60))
' "$1"
}

# --- AC1: bounded recovery, never failure by timeout -------------------------

@test "AC1 BDD1 a stale worker resumes after one correlated nudge" {
	local item mission nudge
	cc_stale_cockpit nudge
	item="$CC_ITEM"
	mission="$CC_MISSION"
	cc_pane cockpit worker-dev "● Working"

	# Given a running mission is stale but its worker is reachable.
	run "$CONTROL_BIN" lifecycle-status --as-of 2026-09-04T10:30:00.000000Z
	[ "$status" -eq 0 ]
	echo "$output" | grep -Fq "observation stale"
	echo "$output" | grep -Fq "reason heartbeat-expired"
	echo "$output" | grep -Fq "recovery awaiting-bounded-recovery"

	# When the controller sends one correlated nudge.
	cc_tick --window worker-dev --as-of 2026-09-04T10:30:00.000000Z
	[ "$status" -eq 0 ]
	echo "$output" | grep -Fq "tick recovered action recover-mission outcome recovered reason stale-mission-nudged"
	echo "$output" | grep -Fq "events-committed 1"
	echo "$output" | grep -Fq "precedence 7 live-status diagnostic worker-dev reachable advances-state no"
	echo "$output" | grep -Fq "stale-mission $mission worker worker-dev queue-item $item state running reason heartbeat-expired recovery awaiting-bounded-recovery"
	echo "$output" | grep -Eq "^recovery rung nudge command [0-9a-f-]{36} worker worker-dev mission $mission queue-item $item trace [0-9a-f-]{36} parent-trace 11111111-1111-4111-8111-111111111111 replacement - deadline 2026-09-04T10:35:00.000000Z digest sha256:[0-9a-f]{64}$"

	# The nudge is correlated to the mission, the command, and the trace: one
	# committed event carries the envelope and its recovery record together, so
	# a nudge correlated to nothing cannot exist.
	nudge="$(cc_recovery_actions "$COCKPIT_CONTROL_ROOT" | awk '{print $2}')"
	[ -n "$nudge" ]
	[ "$(cc_ledger "$COCKPIT_CONTROL_ROOT" 'len(ledger["mission_recoveries"])')" -eq 1 ]
	[ "$(cc_ledger "$COCKPIT_CONTROL_ROOT" 'ledger["mission_recoveries"]["'"$nudge"'"]["recovery"]["mission_id"]')" = "$mission" ]
	[ "$(cc_ledger "$COCKPIT_CONTROL_ROOT" 'ledger["mission_recoveries"]["'"$nudge"'"]["recovery"]["reason"]')" = "heartbeat-expired" ]
	[ "$(cc_ledger "$COCKPIT_CONTROL_ROOT" 'ledger["commands"]["'"$nudge"'"]["envelope"]["command_type"]')" = "mission-nudge" ]
	[ "$(cc_ledger "$COCKPIT_CONTROL_ROOT" 'ledger["commands"]["'"$nudge"'"]["envelope"]["mission_id"]')" = "$mission" ]
	[ "$(cc_ledger "$COCKPIT_CONTROL_ROOT" 'ledger["commands"]["'"$nudge"'"]["envelope"]["parent_trace_id"]')" = "11111111-1111-4111-8111-111111111111" ]

	# The nudge changed no mission state at all: the mission is still running.
	[ "$(cc_ledger "$COCKPIT_CONTROL_ROOT" 'ledger["worker_missions"]["'"$mission"'"]["lifecycle"]["state"]')" = "running" ]
	[ "$(cc_ledger "$COCKPIT_CONTROL_ROOT" 'ledger["mission_slots"]["worker-dev"]["mission_id"]')" = "$mission" ]

	# Then a fresh lifecycle event returns the mission to running.
	cc_emit --state running --worker worker-dev --mission "$mission" --queue-item "$item" \
		--trace 11111111-1111-4111-8111-111111111111 --sequence 3 \
		--heartbeat-at 2026-09-04T10:31:00.000000Z --fresh-until 2026-09-04T11:31:00.000000Z
	cc_tick --window worker-dev --as-of 2026-09-04T10:32:00.000000Z
	[ "$status" -eq 0 ]
	echo "$output" | grep -Fq "outcome observed reason mission-in-progress"
	echo "$output" | grep -Fq "slot-state active stale 0 contested 0"
	cc_absent_output "^recovery rung"
	cc_absent_output "^stale-mission"
	[ "$(cc_ledger "$COCKPIT_CONTROL_ROOT" 'ledger["worker_missions"]["'"$mission"'"]["lifecycle"]["state"]')" = "running" ]
	[ "$(cc_ledger "$COCKPIT_CONTROL_ROOT" 'len(ledger["mission_recoveries"])')" -eq 1 ]
}

@test "AC1 the bounded recovery ladder walks each rung once and then terminates" {
	local mission moment
	cc_stale_cockpit ladder
	mission="$CC_MISSION"

	# One rung per tick, in the fixed architecture section 9 order, each rung
	# only after the previous rung's own declared response window elapsed.
	cc_tick --as-of 2026-09-04T10:30:00.000000Z
	[ "$status" -eq 0 ]
	echo "$output" | grep -Fq "reason stale-mission-nudged"
	cc_tick --as-of 2026-09-04T10:36:00.000000Z
	[ "$status" -eq 0 ]
	echo "$output" | grep -Fq "reason stale-mission-troubleshot"
	cc_tick --as-of 2026-09-04T10:42:00.000000Z
	[ "$status" -eq 0 ]
	echo "$output" | grep -Fq "reason stale-mission-cancelled"
	cc_tick --as-of 2026-09-04T10:58:00.000000Z
	[ "$status" -eq 0 ]
	echo "$output" | grep -Fq "reason stale-mission-replaced"
	echo "$output" | grep -Eq "^recovery rung replace command [0-9a-f-]{36} .* replacement [0-9a-f-]{36} deadline - "

	# Four rungs, four commands, and no fifth: the ladder is a fixed tuple, so
	# recovery is finite by construction rather than by anyone counting.
	[ "$(cc_ledger "$COCKPIT_CONTROL_ROOT" 'len(ledger["commands"])')" -eq 5 ]
	[ "$(cc_recovery_actions "$COCKPIT_CONTROL_ROOT" | awk '{print $1}' | tr '\n' ' ')" = "nudge troubleshoot " ]
	[ "$(cc_ledger "$COCKPIT_CONTROL_ROOT" 'len(ledger["mission_cancellations"])')" -eq 1 ]

	# The replacement ended the episode by creating a new mission on the same
	# single slot; the prior mission is `replaced`, which is a decision, and is
	# never `failed`, which no timeout may ever produce.
	[ "$(cc_ledger "$COCKPIT_CONTROL_ROOT" 'ledger["worker_missions"]["'"$mission"'"]["lifecycle"]["state"]')" = "replaced" ]
	[ "$(cc_ledger "$COCKPIT_CONTROL_ROOT" 'ledger["mission_slots"]["worker-dev"]["state"]')" = "reserved" ]
	[ "$(cc_ledger "$COCKPIT_CONTROL_ROOT" 'ledger["mission_slots"]["worker-dev"]["replaces"]')" = "$mission" ]
	[ "$(cc_ledger "$COCKPIT_CONTROL_ROOT" 'ledger["active_mission_id"]')" = \
		"$(cc_ledger "$COCKPIT_CONTROL_ROOT" 'ledger["mission_slots"]["worker-dev"]["mission_id"]')" ]
	cc_absent_events '"state": "failed"'

	# Ticking on for a long time adds nothing: the episode is over and every
	# later tick terminates cleanly with the situation already recorded.
	local settled
	settled="$(cc_events "$COCKPIT_CONTROL_ROOT")"
	for moment in 11:00 11:30 12:00 13:00 14:00; do
		cc_tick --as-of "2026-09-04T${moment}:00.000000Z"
		[ "$status" -eq 0 ]
		cc_absent_output "^recovery rung"
	done
	[ "$(cc_events "$COCKPIT_CONTROL_ROOT")" -eq $((settled + 1)) ]
	[ "$(cc_ledger "$COCKPIT_CONTROL_ROOT" 'ledger["controller"]["reason"]')" = "mission-in-progress" ]
	run "$CONTROL_BIN" validate
	[ "$status" -eq 0 ]
	[ "$(cc_ledger "$COCKPIT_CONTROL_ROOT" 'len(ledger["mission_slots"]["worker-dev"]["conflicts"])')" -eq 0 ]
}

@test "AC1 a delivered rung holds the ladder until the window it declared elapses" {
	local mission before
	cc_stale_cockpit window
	mission="$CC_MISSION"

	cc_tick --as-of 2026-09-04T10:30:00.000000Z
	[ "$status" -eq 0 ]
	echo "$output" | grep -Fq "deadline 2026-09-04T10:35:00.000000Z"

	# Inside the window the worker was given, the controller does nothing but
	# record that it is waiting: a silent worker is not a failed worker, and a
	# rung is never spent on one that has not run out of time.
	cc_tick --as-of 2026-09-04T10:31:00.000000Z
	[ "$status" -eq 0 ]
	echo "$output" | grep -Fq "tick recorded action record-observation outcome observed reason awaiting-recovery-response"
	cc_absent_output "^recovery rung"
	before="$(cc_events "$COCKPIT_CONTROL_ROOT")"

	# The same wait is not investigated again, however many wakes arrive.
	cc_tick --as-of 2026-09-04T10:33:00.000000Z
	[ "$status" -eq 0 ]
	echo "$output" | grep -Fq "tick unchanged action none"
	echo "$output" | grep -Fq "events-committed 0"
	cc_tick --as-of 2026-09-04T10:34:59.000000Z
	[ "$status" -eq 0 ]
	echo "$output" | grep -Fq "tick unchanged action none"
	[ "$(cc_events "$COCKPIT_CONTROL_ROOT")" -eq "$before" ]

	# Exactly at the declared moment the window has not elapsed yet; one second
	# later it has, and the ladder advances by exactly one rung.
	cc_tick --as-of 2026-09-04T10:35:00.000000Z
	[ "$status" -eq 0 ]
	echo "$output" | grep -Fq "tick unchanged action none"
	cc_tick --as-of 2026-09-04T10:35:01.000000Z
	[ "$status" -eq 0 ]
	echo "$output" | grep -Fq "reason stale-mission-troubleshot"
	[ "$(cc_ledger "$COCKPIT_CONTROL_ROOT" 'ledger["worker_missions"]["'"$mission"'"]["lifecycle"]["state"]')" = "running" ]
}

@test "AC1 a stale mission is never folded into failed by timeout alone" {
	local item mission moment
	cc_stale_cockpit neverfailed
	item="$CC_ITEM"
	mission="$CC_MISSION"

	# The freshness observation says on its own surface exactly what it is: a
	# recoverable reason and an owed bounded action, never a mission state.
	run "$CONTROL_BIN" lifecycle-status --as-of 2026-09-04T10:30:00.000000Z
	[ "$status" -eq 0 ]
	echo "$output" | grep -Fq "$mission worker worker-dev"
	echo "$output" | grep -Fq "observation stale reason heartbeat-expired recovery awaiting-bounded-recovery"
	echo "$output" | grep -Fq "this is recoverable and is not a mission failure"
	cc_absent_output "state failed"

	# Walk the entire ladder, then keep waking long after every declared
	# deadline has passed. `stale` is an observation the whole way through.
	for moment in 10:30 10:36 10:42 10:58 11:30 12:30 20:00; do
		cc_tick --as-of "2026-09-04T${moment}:00.000000Z"
		cc_absent_output "state failed"
	done
	cc_absent_events '"state": "failed"'
	cc_absent_events '"failed"'
	[ "$(cc_ledger "$COCKPIT_CONTROL_ROOT" 'ledger["worker_missions"]["'"$mission"'"]["lifecycle"]["state"]')" = "replaced" ]

	# Long after every declared deadline expired, the mission the ladder ended is
	# `replaced` -- an explicit decision -- and nothing anywhere is `failed`.
	run "$CONTROL_BIN" lifecycle-status --as-of 2026-09-05T00:00:00.000000Z
	[ "$status" -eq 0 ]
	cc_absent_output "state failed"
	echo "$output" | grep -Fq "$mission worker worker-dev"
	echo "$output" | grep -Fq "state replaced"

	# And the queue item the mission implements was never touched by any of it:
	# the controller reads product-work authority and never writes it.
	[ "$("$QUEUE_BIN" list --json | python3 -c 'import json,sys; print(len(json.load(sys.stdin)))')" -eq 1 ]
	[ "$("$QUEUE_BIN" list --json | python3 -c 'import json,sys; print(json.load(sys.stdin)[0]["state"])')" = "implementing" ]
	run "$QUEUE_BIN" inspect "$item"
	[ "$status" -eq 0 ]
	cc_absent_output "cockpit-overseer"
}

@test "AC1 recovery restarts only on genuine fresh evidence, never on a new wake" {
	local item mission first second
	cc_stale_cockpit reset
	item="$CC_ITEM"
	mission="$CC_MISSION"

	cc_tick --as-of 2026-09-04T10:30:00.000000Z
	[ "$status" -eq 0 ]
	first="$(cc_recovery_actions "$COCKPIT_CONTROL_ROOT" | awk '{print $2}')"

	# A fresh lifecycle event the controller then *sees* is the only thing that
	# resets the ladder: recording that observation moves the whole episode onto
	# identifiers no rung has ever used. The observation here is taken at the
	# very last moment the declared window covers, because "fresh when the
	# controller looked" has to mean exactly what the freshness observation
	# means, boundary included.
	cc_emit --state running --worker worker-dev --mission "$mission" --queue-item "$item" \
		--trace 11111111-1111-4111-8111-111111111111 --sequence 3 \
		--heartbeat-at 2026-09-04T10:31:00.000000Z --fresh-until 2026-09-04T11:00:00.000000Z
	cc_tick --as-of 2026-09-04T11:00:00.000000Z
	[ "$status" -eq 0 ]
	echo "$output" | grep -Fq "outcome observed reason mission-in-progress"
	cc_absent_output "^stale-mission"

	# The worker then goes silent again. That is a *new* staleness episode, so
	# the ladder starts at its first rung with a new command rather than
	# resuming where the previous episode stopped.
	cc_tick --as-of 2026-09-04T11:30:00.000000Z
	[ "$status" -eq 0 ]
	echo "$output" | grep -Fq "reason stale-mission-nudged"
	[ "$(cc_recovery_actions "$COCKPIT_CONTROL_ROOT" | awk '{print $1}' | sort | tr '\n' ' ')" = "nudge nudge " ]
	second="$(cc_recovery_actions "$COCKPIT_CONTROL_ROOT" | tail -n 1 | awk '{print $2}')"
	[ "$first" != "$second" ]
	[ "$(cc_ledger "$COCKPIT_CONTROL_ROOT" 'len(ledger["commands"])')" -eq 3 ]
}

@test "AC1 a responsive worker blips repeatedly and is never cancelled or replaced" {
	local item mission moment
	cc_cockpit responsive
	item="$(cc_active_item implementing)"
	cc_running_mission "$item" 11111111-1111-4111-8111-111111111111 \
		2026-09-04T10:40:00.000000Z
	mission="$CC_MISSION"

	# The controller records the mission in progress once while it is fresh, so
	# every later look at a *fresh* mission repeats that reason. Nothing else
	# ever happens to this cockpit: no queue transition, no second worker, no
	# operator. The only thing that changes is the worker's own freshness.
	cc_tick --as-of 2026-09-04T10:10:00.000000Z
	[ "$status" -eq 0 ]
	echo "$output" | grep -Fq "outcome observed reason mission-in-progress"

	# This worker is healthy: it answers every nudge within a minute and every
	# answer declares a forty-minute window. It simply misses a heartbeat now
	# and then. Each blip is a *new* staleness episode, so the ladder must open
	# at `nudge` every time -- never walk on to troubleshoot, cancel, or
	# replace, which is being marked failed by timeout in all but name.
	local sequence=3
	for moment in 10:50 12:00 13:30 15:00; do
		cc_tick --as-of "2026-09-04T${moment}:00.000000Z"
		[ "$status" -eq 0 ]
		echo "$output" | grep -Fq "reason stale-mission-nudged"
		cc_absent_output "reason stale-mission-troubleshot"
		cc_absent_output "reason stale-mission-cancelled"
		cc_absent_output "reason stale-mission-replaced"
		cc_absent_output "reason bounded-recovery-escalated"

		# The worker answers inside the minute and declares forty more.
		cc_emit --state running --worker worker-dev --mission "$mission" \
			--queue-item "$item" --trace 11111111-1111-4111-8111-111111111111 \
			--sequence "$sequence" --heartbeat-at "2026-09-04T${moment}:30.000000Z" \
			--fresh-until "$(cc_plus_forty "$moment")"
		sequence=$((sequence + 1))

		# The controller looks again while that answer still covers the moment,
		# and records what it saw: the mission is in progress, so the episode
		# the nudge opened is over.
		cc_tick --as-of "2026-09-04T${moment}:59.000000Z"
		[ "$status" -eq 0 ]
		echo "$output" | grep -Fq "outcome observed reason mission-in-progress"
		cc_absent_output "^stale-mission"
		cc_absent_output "^recovery rung"
	done

	# The episode the last look closed is keyed on the newest committed evidence
	# and is closed, which is what makes each blip a new episode with its own
	# identifiers rather than a continuation of the previous one.
	run python3 -c '
import sys

sys.path.insert(0, sys.argv[1])
import cockpit_control

state = cockpit_control.read_mission_state(sys.argv[2])
revision, opened = state.recovery_episodes[sys.argv[3]]
print("episode-open", opened)
print("episode-keyed-on-newest-evidence", revision == state.missions[sys.argv[3]]["revision"])
' "$MODULE_DIR" "$COCKPIT_CONTROL_ROOT" "$mission"
	[ "$status" -eq 0 ]
	echo "$output" | grep -Fq "episode-open False"
	echo "$output" | grep -Fq "episode-keyed-on-newest-evidence True"

	# Four blips, four nudges, four distinct commands, and not one later rung.
	[ "$(cc_recovery_actions "$COCKPIT_CONTROL_ROOT" | awk '{print $1}' | tr '\n' ' ')" = "nudge nudge nudge nudge " ]
	[ "$(cc_recovery_actions "$COCKPIT_CONTROL_ROOT" | awk '{print $2}' | sort -u | grep -c .)" -eq 4 ]
	[ "$(cc_ledger "$COCKPIT_CONTROL_ROOT" 'len(ledger["mission_cancellations"])')" -eq 0 ]
	[ "$(cc_ledger "$COCKPIT_CONTROL_ROOT" 'len(ledger["commands"])')" -eq 5 ]

	# The mission the worker was always answering for is still its own, still
	# running, and still holds the one slot it ever claimed.
	[ "$(cc_ledger "$COCKPIT_CONTROL_ROOT" 'ledger["worker_missions"]["'"$mission"'"]["lifecycle"]["state"]')" = "running" ]
	[ "$(cc_slot_mission)" = "$mission" ]
	[ "$(cc_ledger "$COCKPIT_CONTROL_ROOT" 'ledger["mission_slots"]["worker-dev"]["state"]')" = "active" ]
	[ "$(cc_ledger "$COCKPIT_CONTROL_ROOT" 'ledger["mission_slots"]["worker-dev"]["replaces"]')" = "None" ]
	cc_absent_events '"state": "cancelled"'
	cc_absent_events '"state": "replaced"'
	cc_absent_events '"state": "failed"'
	run "$CONTROL_BIN" validate
	[ "$status" -eq 0 ]

	# A tick that sees the very same freshness again investigates nothing: the
	# situation is unchanged, so it commits nothing and terminates cleanly.
	local before
	before="$(cc_events "$COCKPIT_CONTROL_ROOT")"
	cc_tick --as-of 2026-09-04T15:02:00.000000Z
	[ "$status" -eq 0 ]
	echo "$output" | grep -Fq "tick unchanged action none"
	echo "$output" | grep -Fq "events-committed 0"
	[ "$(cc_events "$COCKPIT_CONTROL_ROOT")" -eq "$before" ]
}

@test "AC1 lifecycle evidence that is already expired cannot restart the ladder" {
	local mission moment settled
	cc_expired_cockpit cadence
	mission="$CC_MISSION"

	# The worker is not silent and is not hostile: it reports every ten minutes
	# and declares a two-minute freshness window, and the controller wakes every
	# ten minutes. Every event it sends is therefore applied, moves the mission
	# revision on, and is *still expired* at the only moments the controller
	# ever looks. Such an event did not end the staleness, so it does not start
	# a new episode: the ladder walks on rather than restarting at `nudge`.
	cc_expired_report 10:11
	cc_tick --as-of 2026-09-04T10:20:00.000000Z
	[ "$status" -eq 0 ]
	echo "$output" | grep -Fq "reason stale-mission-nudged"
	cc_expired_report 10:21
	cc_tick --as-of 2026-09-04T10:30:00.000000Z
	[ "$status" -eq 0 ]
	echo "$output" | grep -Fq "reason stale-mission-troubleshot"
	cc_expired_report 10:31
	cc_tick --as-of 2026-09-04T10:40:00.000000Z
	[ "$status" -eq 0 ]
	echo "$output" | grep -Fq "reason stale-mission-cancelled"
	cc_expired_report 10:41
	cc_tick --as-of 2026-09-04T10:50:00.000000Z
	[ "$status" -eq 0 ]
	echo "$output" | grep -Fq "reason awaiting-recovery-response"
	cc_absent_output "^recovery rung"
	cc_expired_report 10:51
	cc_tick --as-of 2026-09-04T11:00:00.000000Z
	[ "$status" -eq 0 ]
	echo "$output" | grep -Fq "reason stale-mission-replaced"

	# The committed rung sequence is the fixed ladder walked exactly once, in
	# order, and not one repeated rung anywhere in it.
	[ "$(cc_recovery_actions "$COCKPIT_CONTROL_ROOT" | awk '{print $1}' | tr '\n' ' ')" = "nudge troubleshoot " ]
	[ "$(cc_ledger "$COCKPIT_CONTROL_ROOT" 'len(ledger["mission_cancellations"])')" -eq 1 ]
	[ "$(cc_ledger "$COCKPIT_CONTROL_ROOT" 'ledger["mission_slots"]["worker-dev"]["replaces"]')" = "$mission" ]
	[ "$(cc_ledger "$COCKPIT_CONTROL_ROOT" 'len(ledger["commands"])')" -eq 5 ]

	# The replacement rung pinned its record to the explicit moment the tick was
	# asked about, never to the wall clock the tick happened to run on.
	[ "$(cc_replacement "$COCKPIT_CONTROL_ROOT" requested_at)" = "2026-09-04T11:00:00.000000Z" ]
	[ "$(cc_replacement "$COCKPIT_CONTROL_ROOT" replaced_mission_id)" = "$mission" ]

	# The episode is over and the same cadence goes on forever without producing
	# one further rung: the ladder is finite in wall-clock time, not merely
	# finite per key. Nothing anywhere was ever marked failed by a timeout.
	for moment in 11:11 11:21 11:31 11:41; do
		cc_expired_report "$moment"
	done
	settled="$(cc_events "$COCKPIT_CONTROL_ROOT")"
	for moment in 11:20 11:30 11:40 12:40 20:00; do
		cc_tick --as-of "2026-09-04T${moment}:00.000000Z"
		[ "$status" -eq 0 ]
		cc_absent_output "^recovery rung"
	done
	[ "$(cc_recovery_actions "$COCKPIT_CONTROL_ROOT" | awk '{print $1}' | tr '\n' ' ')" = "nudge troubleshoot " ]
	[ "$(cc_ledger "$COCKPIT_CONTROL_ROOT" 'len(ledger["commands"])')" -eq 5 ]
	[ "$(cc_ledger "$COCKPIT_CONTROL_ROOT" 'ledger["worker_missions"]["'"$mission"'"]["lifecycle"]["state"]')" = "replaced" ]
	cc_absent_events '"state": "failed"'
	# Five wakes, one observation recorded once: no rung, and no re-investigation.
	[ "$(cc_events "$COCKPIT_CONTROL_ROOT")" -eq $((settled + 1)) ]
	run "$CONTROL_BIN" validate
	[ "$status" -eq 0 ]
}

@test "AC1 a worker whose evidence is always expired is escalated, never nudged forever" {
	local mission moment before
	cc_expired_cockpit escalated
	mission="$CC_MISSION"

	# The queue item leaves the active set, so the ladder is `cancel -> escalate`
	# and escalation is the rung that tells a human. The same cadence applies:
	# the worker keeps reporting, and every report is already expired.
	run "$QUEUE_BIN" reject "$CC_ITEM" --reason "abandoned by the operator"
	[ "$status" -eq 0 ]
	cc_expired_report 10:11
	cc_tick --as-of 2026-09-04T10:20:00.000000Z
	[ "$status" -eq 0 ]
	echo "$output" | grep -Fq "reason queue-item-terminal"
	cc_expired_report 10:21
	cc_tick --as-of 2026-09-04T10:30:00.000000Z
	[ "$status" -eq 0 ]
	echo "$output" | grep -Fq "reason awaiting-recovery-response"
	cc_expired_report 10:31

	# The ladder runs off its end into `escalate`, which delivers nothing, blocks
	# dispatch, and names the decision it needs from a human.
	cc_tick --as-of 2026-09-04T10:40:00.000000Z
	[ "$status" -eq 1 ]
	echo "$output" | grep -Fq "outcome blocked reason bounded-recovery-escalated"
	echo "$output" | grep -Fq "[repair: decide mission $mission on worker worker-dev yourself"

	# Every later wake, however much already-expired evidence keeps arriving,
	# stays escalated and commits nothing: one human decision is owed, and the
	# controller does not investigate it again.
	cc_expired_report 10:41
	cc_tick --as-of 2026-09-04T10:50:00.000000Z
	[ "$status" -eq 1 ]
	echo "$output" | grep -Fq "tick unchanged action none"
	echo "$output" | grep -Fq "outcome blocked reason bounded-recovery-escalated"
	cc_expired_report 10:51
	cc_tick --as-of 2026-09-04T11:00:00.000000Z
	[ "$status" -eq 1 ]
	echo "$output" | grep -Fq "tick unchanged action none"
	echo "$output" | grep -Fq "events-committed 0"
	cc_expired_report 11:01
	cc_tick --as-of 2026-09-04T11:10:00.000000Z
	[ "$status" -eq 1 ]
	echo "$output" | grep -Fq "tick unchanged action none"
	cc_absent_output "^recovery rung"
	before="$(cc_events "$COCKPIT_CONTROL_ROOT")"
	for moment in 11:20 12:20 13:20; do
		cc_tick --as-of "2026-09-04T${moment}:00.000000Z"
		[ "$status" -eq 1 ]
		echo "$output" | grep -Fq "outcome blocked reason bounded-recovery-escalated"
		cc_absent_output "^recovery rung"
	done
	[ "$(cc_ledger "$COCKPIT_CONTROL_ROOT" 'len(ledger["mission_cancellations"])')" -eq 1 ]
	[ "$(cc_ledger "$COCKPIT_CONTROL_ROOT" 'len(ledger["commands"])')" -eq 2 ]
	[ "$(cc_ledger "$COCKPIT_CONTROL_ROOT" 'ledger["worker_missions"]["'"$mission"'"]["lifecycle"]["state"]')" = "running" ]
	cc_absent_events '"state": "failed"'
	# Four escalated wakes committed nothing at all: the decision is a human's.
	[ "$(cc_events "$COCKPIT_CONTROL_ROOT")" -eq "$before" ]
}

@test "AC1 a terminal queue item is cancelled, escalated once, and never reopened" {
	local item mission before
	cc_stale_cockpit terminal
	item="$CC_ITEM"
	mission="$CC_MISSION"

	# Product work leaves the active set while the worker is still running the
	# mission it authorized. `cockpit-queue` owns that transition and the
	# controller never undoes it; it asks the worker to stop cooperatively.
	run "$QUEUE_BIN" reject "$item" --reason "abandoned by the operator"
	[ "$status" -eq 0 ]
	cc_tick --as-of 2026-09-04T10:05:00.000000Z
	[ "$status" -eq 0 ]
	echo "$output" | grep -Fq "tick recovered action recover-mission outcome recovered reason queue-item-terminal"
	echo "$output" | grep -Fq "recovery rung cancel"
	[ "$(cc_ledger "$COCKPIT_CONTROL_ROOT" 'len(ledger["mission_cancellations"])')" -eq 1 ]

	# Nudging or replacing work whose reason to exist is gone would be recovery
	# for its own sake, so this ladder has exactly one rung before escalation.
	[ "$(cc_recovery_actions "$COCKPIT_CONTROL_ROOT" | grep -c .)" -eq 0 ]
	cc_tick --as-of 2026-09-04T10:10:00.000000Z
	[ "$status" -eq 0 ]
	echo "$output" | grep -Fq "reason awaiting-recovery-response"

	# The worker never acknowledges within the window the cancellation declared.
	# Bounded recovery is now exhausted, so the
	# controller escalates: it blocks, names the human decision it needs, and
	# stops. It does not guess, and it does not reopen the queue item.
	cc_tick --as-of 2026-09-04T11:00:00.000000Z
	[ "$status" -eq 1 ]
	echo "$output" | grep -Fq "tick recorded action record-observation outcome blocked reason bounded-recovery-escalated"
	echo "$output" | grep -Fq "dispatch is blocked (bounded-recovery-escalated)"
	echo "$output" | grep -Fq "[repair: decide mission $mission on worker worker-dev yourself: bounded recovery is exhausted"
	echo "$output" | grep -Fq "transition queue item $item with cockpit-queue]"

	# Escalation is terminal and is recorded exactly once: every later wake
	# terminates cleanly without investigating the same situation again.
	before="$(cc_events "$COCKPIT_CONTROL_ROOT")"
	cc_tick --as-of 2026-09-04T12:00:00.000000Z
	[ "$status" -eq 1 ]
	echo "$output" | grep -Fq "tick unchanged action none"
	echo "$output" | grep -Fq "events-committed 0"
	cc_tick --as-of 2026-09-04T13:00:00.000000Z
	[ "$status" -eq 1 ]
	echo "$output" | grep -Fq "tick unchanged action none"
	[ "$(cc_events "$COCKPIT_CONTROL_ROOT")" -eq "$before" ]

	# The queue item is exactly as cockpit-queue left it: never reopened, never
	# transitioned, and never touched by any actor but cockpit-queue itself.
	[ "$(cc_ledger "$COCKPIT_CONTROL_ROOT" 'ledger["worker_missions"]["'"$mission"'"]["lifecycle"]["state"]')" = "running" ]
	[ "$("$QUEUE_BIN" list --json)" = "[]" ]
	run "$QUEUE_BIN" inspect "$item"
	[ "$status" -eq 0 ]
	printf '%s\n' "$output" | python3 -c '
import json
import sys

record = json.load(sys.stdin)
print("state", record["item"]["state"])
print("actors", ",".join(sorted({event["actor"] for event in record["events"]})))
' >"$BATS_TEST_TMPDIR/item.txt"
	grep -Fqx "state rejected" "$BATS_TEST_TMPDIR/item.txt"
	grep -Fqx "actors overseer" "$BATS_TEST_TMPDIR/item.txt"
	cc_absent "cockpit-overseer" "$BATS_TEST_TMPDIR/item.txt"
}

@test "AC1 a mission whose queue item ended is escalated once, even while it reports fresh" {
	local item mission before
	cc_cockpit freshterminal
	item="$(cc_active_item implementing)"
	cc_running_mission "$item" cccccccc-1111-4111-8111-cccccccccccc \
		2026-09-05T10:00:00.000000Z
	mission="$CC_MISSION"

	# Product work leaves the active set while the worker is perfectly healthy,
	# so recovery is owed for a mission that is *fresh* at every moment the
	# controller looks. The ladder is `cancel -> escalate`, and the observations
	# the controller records while walking it are about that very episode: they
	# must not be read back as evidence that the episode ended, or the cancel
	# rung would be re-minted on every wake and no human would ever be told.
	run "$QUEUE_BIN" reject "$item" --reason "abandoned by the operator"
	[ "$status" -eq 0 ]
	cc_tick --as-of 2026-09-04T10:10:00.000000Z
	[ "$status" -eq 0 ]
	echo "$output" | grep -Fq "reason queue-item-terminal"
	cc_emit --state running --worker worker-dev --mission "$mission" --queue-item "$item" \
		--trace cccccccc-1111-4111-8111-cccccccccccc --sequence 3 \
		--heartbeat-at 2026-09-04T10:12:00.000000Z --fresh-until 2026-09-05T10:12:00.000000Z
	cc_tick --as-of 2026-09-04T10:15:00.000000Z
	[ "$status" -eq 0 ]
	echo "$output" | grep -Fq "reason awaiting-recovery-response"
	cc_absent_output "^recovery rung"
	cc_emit --state running --worker worker-dev --mission "$mission" --queue-item "$item" \
		--trace cccccccc-1111-4111-8111-cccccccccccc --sequence 4 \
		--heartbeat-at 2026-09-04T10:20:00.000000Z --fresh-until 2026-09-05T10:20:00.000000Z
	cc_tick --as-of 2026-09-04T10:22:00.000000Z
	[ "$status" -eq 0 ]
	cc_absent_output "^recovery rung"

	# The acknowledgement window the cancel rung declared elapses and the ladder
	# runs off its end into escalation -- once, with exactly one cancellation
	# command ever minted for this episode.
	cc_emit --state running --worker worker-dev --mission "$mission" --queue-item "$item" \
		--trace cccccccc-1111-4111-8111-cccccccccccc --sequence 5 \
		--heartbeat-at 2026-09-04T10:26:00.000000Z --fresh-until 2026-09-05T10:26:00.000000Z
	cc_tick --as-of 2026-09-04T10:30:00.000000Z
	[ "$status" -eq 1 ]
	echo "$output" | grep -Fq "outcome blocked reason bounded-recovery-escalated"
	[ "$(cc_ledger "$COCKPIT_CONTROL_ROOT" 'len(ledger["mission_cancellations"])')" -eq 1 ]
	[ "$(cc_ledger "$COCKPIT_CONTROL_ROOT" 'len(ledger["commands"])')" -eq 2 ]

	before="$(cc_events "$COCKPIT_CONTROL_ROOT")"
	cc_tick --as-of 2026-09-04T10:40:00.000000Z
	[ "$status" -eq 1 ]
	echo "$output" | grep -Fq "tick unchanged action none"
	cc_tick --as-of 2026-09-04T11:40:00.000000Z
	[ "$status" -eq 1 ]
	echo "$output" | grep -Fq "outcome blocked reason bounded-recovery-escalated"
	[ "$(cc_events "$COCKPIT_CONTROL_ROOT")" -eq "$before" ]
	[ "$(cc_ledger "$COCKPIT_CONTROL_ROOT" 'len(ledger["mission_cancellations"])')" -eq 1 ]
	[ "$(cc_ledger "$COCKPIT_CONTROL_ROOT" 'ledger["worker_missions"]["'"$mission"'"]["lifecycle"]["state"]')" = "running" ]
}

@test "AC1 a queue item another worker now owns loses the replace rung only" {
	local item mission
	cc_stale_cockpit reassigned
	item="$CC_ITEM"
	mission="$CC_MISSION"

	# The product state moves to work `cockpit-queue` names worker-test for.
	# Cooperative recovery of the mission worker-dev still holds is allowed;
	# minting a replacement mission on worker-dev is not.
	run "$QUEUE_BIN" transition "$item" testing --reason "handed to test"
	[ "$status" -eq 0 ]
	cc_tick --as-of 2026-09-04T10:30:00.000000Z
	[ "$status" -eq 0 ]
	echo "$output" | grep -Fq "reason stale-mission-nudged"
	cc_tick --as-of 2026-09-04T10:36:00.000000Z
	[ "$status" -eq 0 ]
	echo "$output" | grep -Fq "reason stale-mission-troubleshot"
	cc_tick --as-of 2026-09-04T10:42:00.000000Z
	[ "$status" -eq 0 ]
	echo "$output" | grep -Fq "reason stale-mission-cancelled"

	cc_tick --as-of 2026-09-04T11:00:00.000000Z
	[ "$status" -eq 1 ]
	echo "$output" | grep -Fq "outcome blocked reason bounded-recovery-escalated"
	cc_absent_output "reason stale-mission-replaced"
	[ "$(cc_ledger "$COCKPIT_CONTROL_ROOT" 'ledger["worker_missions"]["'"$mission"'"]["lifecycle"]["state"]')" = "running" ]
	[ "$(cc_ledger "$COCKPIT_CONTROL_ROOT" 'ledger["mission_slots"]["worker-dev"]["mission_id"]')" = "$mission" ]
	[ "$(cc_ledger "$COCKPIT_CONTROL_ROOT" 'ledger["mission_slots"]["worker-dev"]["replaces"]')" = "None" ]
}

@test "AC1 concurrent ticks at one rung register exactly one recovery command" {
	local index barrier
	cc_stale_cockpit concurrent
	barrier="$BATS_TEST_TMPDIR/recovery-barrier"

	for index in 1 2 3 4 5 6 7 8; do
		(
			while [ ! -f "$barrier" ]; do :; done
			code=0
			"$OVERSEER_BIN" tick --as-of 2026-09-04T10:30:00.000000Z \
				>"$BATS_TEST_TMPDIR/recovery-$index.out" 2>&1 || code=$?
			printf '%s\n' "$code" >"$BATS_TEST_TMPDIR/recovery-$index.code"
		) &
	done
	: >"$barrier"
	wait

	# One rung owns exactly one derived command ID per staleness episode, so
	# eight processes reading the same committed prefix mint the same command
	# and exactly one of them registers it. No rung is skipped and no conflict
	# has to be recorded to make that true.
	[ "$(cc_ledger "$COCKPIT_CONTROL_ROOT" 'len(ledger["mission_recoveries"])')" -eq 1 ]
	[ "$(cc_recovery_actions "$COCKPIT_CONTROL_ROOT" | awk '{print $1}')" = "nudge" ]
	[ "$(cc_ledger "$COCKPIT_CONTROL_ROOT" 'len(ledger["mission_slots"]["worker-dev"]["conflicts"])')" -eq 0 ]
	[ "$(grep -h '^tick ' "$BATS_TEST_TMPDIR"/recovery-*.out | grep -c 'tick recovered')" -eq 1 ]
	for index in 1 2 3 4 5 6 7 8; do
		[ "$(grep -c '^tick ' "$BATS_TEST_TMPDIR/recovery-$index.out")" -eq 1 ]
		grep -Eq 'events-committed (0|1) ' "$BATS_TEST_TMPDIR/recovery-$index.out"
		[ "$(cat "$BATS_TEST_TMPDIR/recovery-$index.code")" -eq 0 ]
		cc_absent "outcome blocked" "$BATS_TEST_TMPDIR/recovery-$index.out"
	done
	run "$CONTROL_BIN" validate
	[ "$status" -eq 0 ]
}

@test "AC1 a recovery command cannot be minted or forged outside the controller" {
	cc_cockpit forgery
	local out="$BATS_TEST_TMPDIR/forge.out" err="$BATS_TEST_TMPDIR/forge.err"
	local code=0

	# The generic delivery surface refuses every managed command type, so a
	# nudge correlated to nothing at all cannot be created.
	"$CONTROL_BIN" register-command \
		--command-id 55555555-5555-4555-8555-555555555555 \
		--type mission-nudge \
		--mission 66666666-6666-4666-8666-666666666666 \
		--queue-item QI-1 --target worker-dev \
		--trace 77777777-7777-4777-8777-777777777777 \
		--payload '{"brief": "wake up"}' >"$out" 2>"$err" || code=$?
	[ "$code" -ne 0 ]
	[ ! -s "$out" ]
	head -n 1 "$err" | grep -Fq "cockpit-control: command type 'mission-nudge' is a managed mission command"
	cc_no_traceback "$err"
	[ "$(cc_events "$COCKPIT_CONTROL_ROOT")" -eq 0 ]

	# A recovery record whose envelope declares the wrong managed command type
	# is refused before it can be committed: the record and its command type are
	# two halves of one contract, exactly as every other managed record is.
	run python3 -c '
import sys

sys.path.insert(0, sys.argv[1])
import cockpit_control

root = sys.argv[2]
command_id = "88888888-8888-4888-8888-888888888888"
mission_id = "99999999-9999-4999-8999-999999999999"
trace_id = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
recovery = cockpit_control.build_mission_recovery(
    command_id=command_id,
    action=cockpit_control.MISSION_RECOVERY_NUDGE,
    mission_id=mission_id,
    worker_id="worker-dev",
    queue_item_id="QI-1",
    trace_id=trace_id,
    reason=cockpit_control.LIFECYCLE_STALE_REASON,
    respond_deadline_at="2026-09-04T10:35:00.000000Z",
    evidence_refs=["mission:" + mission_id],
    requested_at="2026-09-04T10:30:00.000000Z",
)
envelope = cockpit_control.build_command_envelope(
    command_id=command_id,
    command_type=cockpit_control.COMMAND_TYPE_MISSION_TROUBLESHOOT,
    mission_id=mission_id,
    queue_item_id="QI-1",
    target_kind="worker",
    target_id="worker-dev",
    trace_id=trace_id,
    payload_digest=cockpit_control.command_payload_digest({"forged": True}),
    control_root=root,
)
try:
    cockpit_control.publish_control_event(
        root,
        "command-registered",
        actor="forger",
        payload={"command_envelope": envelope, "mission_recovery": recovery},
    )
except cockpit_control.ControlStoreError as exc:
    print("refused", exc)
else:
    print("BUG: a mistyped recovery command was committed")

# A rung outside the closed vocabulary is refused by the validator itself.
try:
    cockpit_control.build_mission_recovery(
        command_id=command_id,
        action="restart",
        mission_id=mission_id,
        worker_id="worker-dev",
        queue_item_id="QI-1",
        trace_id=trace_id,
        reason=cockpit_control.LIFECYCLE_STALE_REASON,
        respond_deadline_at="2026-09-04T10:35:00.000000Z",
        evidence_refs=["mission:" + mission_id],
        requested_at="2026-09-04T10:30:00.000000Z",
    )
except cockpit_control.ControlStoreError as exc:
    print("rung-refused", exc)
else:
    print("BUG: an invented rung was accepted")

# A response window that closes before it opens could never be observed as
# elapsed, so it is refused rather than silently normalized.
try:
    cockpit_control.build_mission_recovery(
        command_id=command_id,
        action=cockpit_control.MISSION_RECOVERY_NUDGE,
        mission_id=mission_id,
        worker_id="worker-dev",
        queue_item_id="QI-1",
        trace_id=trace_id,
        reason=cockpit_control.LIFECYCLE_STALE_REASON,
        respond_deadline_at="2026-09-04T10:30:00.000000Z",
        evidence_refs=["mission:" + mission_id],
        requested_at="2026-09-04T10:30:00.000000Z",
    )
except cockpit_control.ControlStoreError as exc:
    print("deadline-refused", exc)
else:
    print("BUG: a recovery window that never opens was accepted")
' "$MODULE_DIR" "$COCKPIT_CONTROL_ROOT"
	[ "$status" -eq 0 ]
	echo "$output" | grep -Fq "refused"
	echo "$output" | grep -Fq "requires command_type 'mission-nudge'"
	echo "$output" | grep -Fq "rung-refused"
	echo "$output" | grep -Fq "declares unknown action 'restart'"
	echo "$output" | grep -Fq "deadline-refused"
	echo "$output" | grep -Fq "requires respond_deadline_at after requested_at"
	cc_absent_output "BUG"
	[ "$(cc_events "$COCKPIT_CONTROL_ROOT")" -eq 0 ]
}

@test "AC1 a recovery command the fold refuses is reported as changing nothing" {
	local item mission replacement
	cc_stale_cockpit refused
	item="$CC_ITEM"
	mission="$CC_MISSION"

	cc_tick --as-of 2026-09-04T10:30:00.000000Z
	[ "$status" -eq 0 ]
	cc_tick --as-of 2026-09-04T10:36:00.000000Z
	[ "$status" -eq 0 ]
	cc_tick --as-of 2026-09-04T10:42:00.000000Z
	[ "$status" -eq 0 ]
	echo "$output" | grep -Fq "reason stale-mission-cancelled"

	# Every identifier a rung uses is derived, so the mission ID the replacement
	# rung is about to create is knowable in advance. Give it to another worker
	# first: the replacement is then a mission ID already in use, which the
	# deterministic fold refuses however it was committed.
	replacement="$(python3 -c '
import sys

sys.path.insert(0, sys.argv[1])
import cockpit_control

tick = cockpit_control.ControllerTick(sys.argv[2], as_of="2026-09-04T10:58:00.000000Z")
evidence = tick._gathered_evidence()
print(evidence.recovery_mission_id(sys.argv[3], evidence.episode_revision(sys.argv[3])))
' "$MODULE_DIR" "$COCKPIT_CONTROL_ROOT" "$mission")"
	[ -n "$replacement" ]
	cc_emit --state accepted --worker worker-fix --mission "$replacement" --queue-item "$item" \
		--trace 77777777-7777-4777-8777-777777777777 --sequence 1 \
		--heartbeat-at 2026-09-04T10:50:00.000000Z --fresh-until 2026-09-05T10:50:00.000000Z

	# The command is durably committed either way; the fold decides whether it
	# also moved a mission slot, and a refusal is reported rather than silently
	# counted as recovery that happened.
	cc_tick --as-of 2026-09-04T10:58:00.000000Z
	[ "$status" -eq 1 ]
	echo "$output" | grep -Fq "tick unchanged action recover-mission"
	echo "$output" | grep -Fq "the replace recovery command is committed but did not move worker worker-dev's mission slot (conflict-recorded); no mission state changed"
	echo "$output" | grep -Fq "mission-conflict worker-dev mission $replacement reason reused-mission-id source replacement"

	# Nothing was replaced: worker-dev still holds the mission it always held,
	# and the refusal is retained as durable evidence rather than discarded.
	[ "$(cc_ledger "$COCKPIT_CONTROL_ROOT" 'ledger["worker_missions"]["'"$mission"'"]["lifecycle"]["state"]')" = "running" ]
	[ "$(cc_ledger "$COCKPIT_CONTROL_ROOT" 'ledger["mission_slots"]["worker-dev"]["mission_id"]')" = "$mission" ]
	[ "$(cc_ledger "$COCKPIT_CONTROL_ROOT" 'ledger["mission_slots"]["worker-dev"]["replaces"]')" = "None" ]
	[ "$(cc_ledger "$COCKPIT_CONTROL_ROOT" 'len(ledger["mission_slots"]["worker-dev"]["conflicts"])')" -eq 1 ]

	# The rung was spent, so the ladder never retries the refusal. The refusal
	# itself left a duplicate claim against the mission worker-dev still holds,
	# which is exactly the AC2 rule: dispatch stops and the conflict is
	# escalated once, rather than the same replacement being attempted forever.
	cc_tick --as-of 2026-09-04T11:30:00.000000Z
	[ "$status" -eq 1 ]
	echo "$output" | grep -Fq "outcome blocked reason worker-mission-conflict"
	echo "$output" | grep -Fq "mission-contest worker-dev retained $mission refused $replacement reason reused-mission-id source replacement"
	cc_absent_output "reason stale-mission-replaced"
	local before
	before="$(cc_events "$COCKPIT_CONTROL_ROOT")"
	cc_tick --as-of 2026-09-04T12:30:00.000000Z
	[ "$status" -eq 1 ]
	echo "$output" | grep -Fq "tick unchanged action none"
	[ "$(cc_events "$COCKPIT_CONTROL_ROOT")" -eq "$before" ]
}

# --- AC2: two active missions for one worker ---------------------------------

@test "AC2 BDD2 two missions claim one worker: the earliest is kept and dispatch stops" {
	local item kept intruder
	cc_stale_cockpit contest
	item="$CC_ITEM"
	kept="$CC_MISSION"
	intruder=99999999-9999-4999-8999-999999999999

	# Given two active records target the same worker. The second arrives
	# through the ordinary lifecycle surface, so nothing about it is special
	# except that worker-dev's single slot is already claimed.
	cc_emit --state accepted --worker worker-dev --mission "$intruder" --queue-item "$item" \
		--trace 22222222-2222-4222-8222-222222222222 --sequence 1 \
		--heartbeat-at 2026-09-04T10:03:00.000000Z --fresh-until 2026-09-05T10:03:00.000000Z

	# When reconciliation runs, then the earliest valid accepted mission is
	# retained and dispatch stops.
	cc_tick --as-of 2026-09-04T10:04:00.000000Z
	[ "$status" -eq 1 ]
	echo "$output" | grep -Fq "tick recorded action record-observation outcome blocked reason worker-mission-conflict"
	echo "$output" | grep -Fq "mission-contest worker-dev retained $kept refused $intruder reason second-active-slot source lifecycle"
	echo "$output" | grep -Fq "slot-state active stale 0 contested 1"
	echo "$output" | grep -Fq "dispatch is blocked (worker-mission-conflict)"
	echo "$output" | grep -Fq "[repair: worker worker-dev keeps its earliest accepted mission $kept; dispatch resumes when the duplicate mission $intruder ends"
	echo "$output" | grep -Fq "and never by ending $kept, which this control root is keeping"
	cc_absent_output "^dispatch command"
	cc_absent_output "^recovery rung"

	# The worker still holds exactly one slot, still the earliest mission, and
	# the refused claim is retained as durable evidence rather than discarded.
	[ "$(cc_ledger "$COCKPIT_CONTROL_ROOT" 'len(ledger["mission_slots"])')" -eq 1 ]
	[ "$(cc_ledger "$COCKPIT_CONTROL_ROOT" 'ledger["mission_slots"]["worker-dev"]["mission_id"]')" = "$kept" ]
	[ "$(cc_ledger "$COCKPIT_CONTROL_ROOT" 'ledger["mission_slots"]["worker-dev"]["state"]')" = "active" ]
	[ "$(cc_ledger "$COCKPIT_CONTROL_ROOT" 'len(ledger["mission_slots"]["worker-dev"]["conflicts"])')" -eq 1 ]
	[ "$(cc_ledger "$COCKPIT_CONTROL_ROOT" 'ledger["mission_slots"]["worker-dev"]["conflicts"][0]["mission_id"]')" = "$intruder" ]
	[ "$(cc_ledger "$COCKPIT_CONTROL_ROOT" 'ledger["controller"]["outcome"]')" = "blocked" ]

	# Both missions are named as the evidence the escalation rests on, and the
	# conflict is escalated exactly once however many wakes arrive.
	grep -rlFq "mission:$intruder" "$COCKPIT_CONTROL_ROOT/events" >/dev/null
	local before
	before="$(cc_events "$COCKPIT_CONTROL_ROOT")"
	cc_tick --as-of 2026-09-04T10:05:00.000000Z
	[ "$status" -eq 1 ]
	echo "$output" | grep -Fq "tick unchanged action none"
	echo "$output" | grep -Fq "events-committed 0"
	[ "$(cc_events "$COCKPIT_CONTROL_ROOT")" -eq "$before" ]

	# Dispatch stays stopped even when new product work becomes implementable.
	run "$QUEUE_BIN" reject "$item" --reason "give up on this item"
	[ "$status" -eq 0 ]
	cc_active_item implementing >/dev/null
	cc_tick --as-of 2026-09-04T10:06:00.000000Z
	[ "$status" -eq 1 ]
	echo "$output" | grep -Fq "outcome blocked reason worker-mission-conflict"
	cc_absent_output "^dispatch command"
	[ "$(cc_ledger "$COCKPIT_CONTROL_ROOT" 'len(ledger["commands"])')" -eq 1 ]
}

@test "AC2 the mission that is kept is the earliest committed, not the earliest dated" {
	cc_cockpit clocks
	local item first second
	item="$(cc_active_item implementing)"
	first=aaaaaaaa-1111-4111-8111-aaaaaaaaaaaa
	second=bbbbbbbb-2222-4222-8222-bbbbbbbbbbbb

	# The mission committed *second* carries much earlier wall-clock stamps. A
	# "latest timestamp wins" or "earliest timestamp wins" rule would hand the
	# slot to it; ADR-014 rejects both, because clocks and late observations do
	# not establish ownership. Commit order does.
	cc_emit --state accepted --worker worker-dev --mission "$first" --queue-item "$item" \
		--trace 33333333-3333-4333-8333-333333333333 --sequence 1 \
		--heartbeat-at 2026-09-04T23:59:00.000000Z --fresh-until 2026-09-05T23:59:00.000000Z
	cc_emit --state accepted --worker worker-dev --mission "$second" --queue-item "$item" \
		--trace 44444444-4444-4444-8444-444444444444 --sequence 1 \
		--heartbeat-at 2020-01-01T00:00:00.000000Z --fresh-until 2030-01-01T00:00:00.000000Z

	[ "$(cc_ledger "$COCKPIT_CONTROL_ROOT" 'ledger["mission_slots"]["worker-dev"]["mission_id"]')" = "$first" ]
	[ "$(cc_ledger "$COCKPIT_CONTROL_ROOT" 'ledger["mission_slots"]["worker-dev"]["conflicts"][0]["mission_id"]')" = "$second" ]

	cc_tick --as-of 2026-09-04T10:10:00.000000Z
	[ "$status" -eq 1 ]
	echo "$output" | grep -Fq "mission-contest worker-dev retained $first refused $second"
	echo "$output" | grep -Fq "keeps its earliest accepted mission $first"

	# The retained mission is the one the fold admitted first, and the ordering
	# is proved against committed revisions rather than against any timestamp.
	run python3 -c '
import sys

sys.path.insert(0, sys.argv[1])
import cockpit_control

state = cockpit_control.read_mission_state(sys.argv[2])
kept = state.worker_slots["worker-dev"]
conflict = kept["conflicts"][0]
print("kept-revision", kept["revision"])
print("refused-revision", conflict["revision"])
print("ordered", kept["revision"] < conflict["revision"])
print(
    "kept-heartbeat",
    state.missions[kept["mission_id"]]["lifecycle"]["heartbeat_at"],
)
print(
    "refused-heartbeat",
    state.missions[conflict["mission_id"]]["lifecycle"]["heartbeat_at"],
)
' "$MODULE_DIR" "$COCKPIT_CONTROL_ROOT"
	[ "$status" -eq 0 ]
	echo "$output" | grep -Fq "ordered True"
	echo "$output" | grep -Fq "kept-heartbeat 2026-09-04T23:59:00.000000Z"
	echo "$output" | grep -Fq "refused-heartbeat 2020-01-01T00:00:00.000000Z"
}

@test "AC2 a contest blocks only while the claim it was refused against is held" {
	cc_cockpit cleared
	local item kept intruder
	item="$(cc_active_item implementing)"
	cc_running_mission "$item" 55555555-5555-4555-8555-555555555555 \
		2026-09-05T10:00:00.000000Z
	kept="$CC_MISSION"
	intruder=cccccccc-3333-4333-8333-cccccccccccc
	cc_emit --state accepted --worker worker-dev --mission "$intruder" --queue-item "$item" \
		--trace 66666666-6666-4666-8666-666666666666 --sequence 1 \
		--heartbeat-at 2026-09-04T10:03:00.000000Z --fresh-until 2026-09-05T10:03:00.000000Z

	cc_tick --as-of 2026-09-04T10:04:00.000000Z
	[ "$status" -eq 1 ]
	echo "$output" | grep -Fq "outcome blocked reason worker-mission-conflict"

	# The contested mission ends the way missions end: the worker says so. The
	# conflict stays in the ledger as durable evidence forever, but it is
	# evidence about a finished episode and must not block the cockpit for good.
	cc_emit --state completed --worker worker-dev --mission "$kept" --queue-item "$item" \
		--trace 55555555-5555-4555-8555-555555555555 --sequence 3 \
		--evidence "queue:$item"
	[ "$(cc_ledger "$COCKPIT_CONTROL_ROOT" 'ledger["mission_slots"]["worker-dev"]["state"]')" = "released" ]
	[ "$(cc_ledger "$COCKPIT_CONTROL_ROOT" 'len(ledger["mission_slots"]["worker-dev"]["conflicts"])')" -eq 1 ]

	run "$QUEUE_BIN" transition "$item" testing --reason "ready for testing"
	[ "$status" -eq 0 ]
	cc_tick --as-of 2026-09-04T10:20:00.000000Z
	[ "$status" -eq 0 ]
	echo "$output" | grep -Fq "outcome dispatched reason queue-item-implementable"
	echo "$output" | grep -Fq "worker worker-test"
	cc_absent_output "worker-mission-conflict"

	# A brand-new claim on the very worker the contest was recorded against is
	# not blocked by evidence about the episode that has since ended.
	run "$QUEUE_BIN" reject "$item" --reason "delivered by hand"
	[ "$status" -eq 0 ]
	cc_active_item implementing >/dev/null
	cc_tick --as-of 2026-09-04T10:30:00.000000Z
	[ "$status" -eq 0 ]
	echo "$output" | grep -Fq "outcome dispatched reason queue-item-implementable"
	echo "$output" | grep -Fq "worker worker-dev"
	cc_absent_output "worker-mission-conflict"
	[ "$(cc_ledger "$COCKPIT_CONTROL_ROOT" 'ledger["mission_slots"]["worker-dev"]["state"]')" = "reserved" ]
	[ "$(cc_ledger "$COCKPIT_CONTROL_ROOT" 'ledger["mission_slots"]["worker-dev"]["mission_id"] != "'"$kept"'"')" = "True" ]

	# The refused claim is still retained as durable evidence; it simply no
	# longer describes the claim the slot now holds.
	[ "$(cc_ledger "$COCKPIT_CONTROL_ROOT" 'len(ledger["mission_slots"]["worker-dev"]["conflicts"])')" -eq 1 ]
	[ "$(cc_ledger "$COCKPIT_CONTROL_ROOT" 'ledger["mission_slots"]["worker-dev"]["conflicts"][0]["mission_id"]')" = "$intruder" ]
	cc_tick --as-of 2026-09-04T10:35:00.000000Z
	[ "$status" -eq 0 ]
	echo "$output" | grep -Fq "contested 0"
	cc_absent_output "worker-mission-conflict"
}

@test "AC2 following the printed repair ends the duplicate and unblocks the tick" {
	local item kept intruder duplicate
	cc_cockpit repair
	item="$(cc_active_item implementing)"
	cc_running_mission "$item" 55555555-5555-4555-8555-555555555555 \
		2026-09-05T10:00:00.000000Z
	kept="$CC_MISSION"
	intruder=dddddddd-4444-4444-8444-dddddddddddd
	cc_emit --state accepted --worker worker-dev --mission "$intruder" --queue-item "$item" \
		--trace 66666666-6666-4666-8666-666666666666 --sequence 1 \
		--heartbeat-at 2026-09-04T10:03:00.000000Z --fresh-until 2026-09-05T10:03:00.000000Z
	cc_emit --state running --worker worker-dev --mission "$intruder" --queue-item "$item" \
		--trace 66666666-6666-4666-8666-666666666666 --sequence 2 \
		--heartbeat-at 2026-09-04T10:03:30.000000Z --fresh-until 2026-09-05T10:03:30.000000Z

	cc_tick --as-of 2026-09-04T10:04:00.000000Z
	[ "$status" -eq 1 ]
	echo "$output" | grep -Fq "outcome blocked reason worker-mission-conflict"

	# The repair is *followed*, not paraphrased: the mission an operator is told
	# to end is read back out of the printed sentence itself, so a refusal that
	# named a mission it is not blocking on would fail here.
	duplicate="$(printf '%s\n' "$output" |
		sed -n 's/.*dispatch resumes when the duplicate mission \([0-9a-f-]*\) ends.*/\1/p' |
		head -n 1)"
	[ "$duplicate" = "$intruder" ]

	# Requesting the cancellation is not itself the repair: the duplicate claim
	# is still live until its worker ends it, and the tick keeps saying so
	# rather than resuming dispatch on a promise.
	run "$CONTROL_BIN" cancel-mission --command-id 7f7f7f7f-7f7f-4f7f-8f7f-7f7f7f7f7f7f \
		--worker worker-dev --mission "$duplicate" --queue-item "$item" \
		--trace 88888888-8888-4888-8888-888888888888 --reason "duplicate claim on worker-dev" \
		--evidence "mission:$duplicate" --requested-at 2026-09-04T10:05:00.000000Z \
		--acknowledge-within 600 --payload '{"cancel":"cooperative"}'
	[ "$status" -eq 0 ]
	cc_tick --as-of 2026-09-04T10:06:00.000000Z
	[ "$status" -eq 1 ]
	echo "$output" | grep -Fq "outcome blocked reason worker-mission-conflict"
	echo "$output" | grep -Fq "contested 1"

	# The worker ends the duplicate exactly as the repair says. The block clears,
	# the retained mission is still the one the slot holds, and nothing had to
	# happen to it: the tool never asked an operator to destroy the mission it
	# had just said it was keeping.
	cc_emit --state cancelled --worker worker-dev --mission "$duplicate" --queue-item "$item" \
		--trace 66666666-6666-4666-8666-666666666666 --sequence 3 \
		--reason "the cancellation was honoured" --evidence "mission:$duplicate"
	cc_tick --as-of 2026-09-04T10:07:00.000000Z
	[ "$status" -eq 0 ]
	echo "$output" | grep -Fq "contested 0"
	cc_absent_output "worker-mission-conflict"
	[ "$(cc_slot_mission)" = "$kept" ]
	[ "$(cc_ledger "$COCKPIT_CONTROL_ROOT" 'ledger["worker_missions"]["'"$kept"'"]["lifecycle"]["state"]')" = "running" ]
	[ "$(cc_ledger "$COCKPIT_CONTROL_ROOT" 'ledger["worker_missions"]["'"$duplicate"'"]["lifecycle"]["state"]')" = "cancelled" ]

	# Both refused claims are still retained as durable evidence; they simply no
	# longer describe a live mission.
	[ "$(cc_ledger "$COCKPIT_CONTROL_ROOT" 'len(ledger["mission_slots"]["worker-dev"]["conflicts"])')" -eq 2 ]
	[ "$(cc_ledger "$COCKPIT_CONTROL_ROOT" '" ".join(sorted({c["mission_id"] for c in ledger["mission_slots"]["worker-dev"]["conflicts"]}))')" = "$duplicate" ]
	run "$CONTROL_BIN" validate
	[ "$status" -eq 0 ]
}

@test "AC2 the printed repair is followable for a duplicate that only ever accepted" {
	local item kept intruder duplicate route steps state sequence
	cc_cockpit repairaccepted
	item="$(cc_active_item implementing)"
	cc_running_mission "$item" 55555555-5555-4555-8555-555555555555 \
		2026-09-05T10:00:00.000000Z
	kept="$CC_MISSION"
	intruder=dddddddd-5555-4555-8555-dddddddddddd

	# The duplicate never gets past `accepted` -- the primary shape of this
	# conflict, because accepting a mission is exactly what claims a slot.
	cc_emit --state accepted --worker worker-dev --mission "$intruder" --queue-item "$item" \
		--trace 66666666-6666-4666-8666-666666666666 --sequence 1 \
		--heartbeat-at 2026-09-04T10:03:00.000000Z --fresh-until 2026-09-05T10:03:00.000000Z

	cc_tick --as-of 2026-09-04T10:04:00.000000Z
	[ "$status" -eq 1 ]
	echo "$output" | grep -Fq "outcome blocked reason worker-mission-conflict"

	# The repair is followed, not paraphrased: both the mission to end and the
	# lifecycle events that end it are read back out of the printed sentence.
	duplicate="$(printf '%s\n' "$output" |
		sed -n 's/.*dispatch resumes when the duplicate mission \([0-9a-f-]*\) ends.*/\1/p' |
		head -n 1)"
	[ "$duplicate" = "$intruder" ]
	route="$(printf '%s\n' "$output" |
		sed -n 's/.*have its worker record \(.*\) -- and never by ending.*/\1/p' | head -n 1)"
	[ -n "$route" ]
	steps="$(printf '%s\n' "${route%%, because*}" | grep -o '`[a-z-]*`' | tr -d '`')"
	[ "$(printf '%s\n' "$steps" | tr '\n' ' ')" = "running cancelled " ]

	# A mission a worker has only accepted cannot be cancelled in one step, and
	# the sentence says so rather than naming a step that changes nothing.
	echo "$output" | grep -Fq 'because `cancelled` is not reachable from `accepted` in one step'

	run "$CONTROL_BIN" cancel-mission --command-id 7f7f7f7f-7f7f-4f7f-8f7f-7f7f7f7f7f7f \
		--worker worker-dev --mission "$duplicate" --queue-item "$item" \
		--trace 88888888-8888-4888-8888-888888888888 --reason "duplicate claim on worker-dev" \
		--evidence "mission:$duplicate" --requested-at 2026-09-04T10:05:00.000000Z \
		--acknowledge-within 600 --payload '{"cancel":"cooperative"}'
	[ "$status" -eq 0 ]
	cc_tick --as-of 2026-09-04T10:06:00.000000Z
	[ "$status" -eq 1 ]
	echo "$output" | grep -Fq "outcome blocked reason worker-mission-conflict"
	echo "$output" | grep -Fq "contested 1"

	# The step the transition table cannot take is committed as audit evidence
	# and says exactly why it moved nothing, instead of reporting success and
	# leaving a scripted operator blocked with no diagnosis.
	run "$CONTROL_BIN" record-lifecycle --state cancelled --worker worker-dev \
		--mission "$duplicate" --queue-item "$item" \
		--trace 66666666-6666-4666-8666-666666666666 --sequence 2 \
		--reason "not reachable from accepted" --evidence "mission:$duplicate"
	[ "$status" -eq 0 ]
	echo "$output" | grep -Fq "retained for audit only (retained-invalid-transition"
	echo "$output" | grep -Fq "cancelled is not reachable from accepted; this mission can record accepted, running, replaced next"
	cc_tick --as-of 2026-09-04T10:06:30.000000Z
	[ "$status" -eq 1 ]
	echo "$output" | grep -Fq "outcome blocked reason worker-mission-conflict"

	# Every step the sentence names moves materialized state, in the order it
	# names them, and the last one ends the duplicate.
	sequence=3
	for state in $steps; do
		case "$state" in
		cancelled | completed | failed | replaced)
			run "$CONTROL_BIN" record-lifecycle --state "$state" --worker worker-dev \
				--mission "$duplicate" --queue-item "$item" \
				--trace 66666666-6666-4666-8666-666666666666 --sequence "$sequence" \
				--reason "the cancellation was honoured" --evidence "mission:$duplicate"
			;;
		*)
			run "$CONTROL_BIN" record-lifecycle --state "$state" --worker worker-dev \
				--mission "$duplicate" --queue-item "$item" \
				--trace 66666666-6666-4666-8666-666666666666 --sequence "$sequence" \
				--heartbeat-at "2026-09-04T10:0${sequence}:00.000000Z" \
				--fresh-until 2026-09-05T10:03:00.000000Z
			;;
		esac
		[ "$status" -eq 0 ]
		echo "$output" | grep -Fq "advanced the materialized mission state"
		sequence=$((sequence + 1))
	done

	# The block clears, and nothing had to happen to the mission the control
	# root said it was keeping.
	cc_tick --as-of 2026-09-04T10:07:00.000000Z
	[ "$status" -eq 0 ]
	echo "$output" | grep -Fq "contested 0"
	cc_absent_output "worker-mission-conflict"
	[ "$(cc_slot_mission)" = "$kept" ]
	[ "$(cc_ledger "$COCKPIT_CONTROL_ROOT" 'ledger["worker_missions"]["'"$kept"'"]["lifecycle"]["state"]')" = "running" ]
	[ "$(cc_ledger "$COCKPIT_CONTROL_ROOT" 'ledger["worker_missions"]["'"$duplicate"'"]["lifecycle"]["state"]')" = "cancelled" ]
	[ "$(cc_ledger "$COCKPIT_CONTROL_ROOT" '" ".join(sorted({c["mission_id"] for c in ledger["mission_slots"]["worker-dev"]["conflicts"]}))')" = "$duplicate" ]
	run "$CONTROL_BIN" validate
	[ "$status" -eq 0 ]
}

@test "AC2 a claim the fold refused before it ever ran does not block the cockpit" {
	local item kept intruder ghost
	cc_cockpit ghost
	item="$(cc_active_item implementing)"
	cc_running_mission "$item" 99999999-1111-4111-8111-999999999999 \
		2026-09-05T10:00:00.000000Z
	kept="$CC_MISSION"
	intruder=eeeeeeee-5555-4555-8555-eeeeeeeeeeee
	ghost=ffffffff-6666-4666-8666-ffffffffffff
	cc_emit --state accepted --worker worker-dev --mission "$intruder" --queue-item "$item" \
		--trace aaaaaaaa-1111-4111-8111-aaaaaaaaaaaa --sequence 1 \
		--heartbeat-at 2026-09-04T10:03:00.000000Z --fresh-until 2026-09-05T10:03:00.000000Z
	cc_emit --state running --worker worker-dev --mission "$intruder" --queue-item "$item" \
		--trace aaaaaaaa-1111-4111-8111-aaaaaaaaaaaa --sequence 2 \
		--heartbeat-at 2026-09-04T10:03:30.000000Z --fresh-until 2026-09-05T10:03:30.000000Z
	cc_tick --as-of 2026-09-04T10:04:00.000000Z
	[ "$status" -eq 1 ]
	echo "$output" | grep -Fq "outcome blocked reason worker-mission-conflict"

	# An operator reaches for `replace-mission` on the duplicate. The fold
	# refuses it -- replacing a mission that does not hold the slot would reserve
	# that slot for a second mission -- and retains the refusal as a durable
	# conflict naming a mission that was never materialized at all. No worker can
	# ever end a mission that never existed, so a claim like this one must never
	# be what a block is waiting on, or the refusal would be exactly the dead end
	# the repair table exists to prevent.
	run "$CONTROL_BIN" replace-mission --command-id 6a6a6a6a-6a6a-4a6a-8a6a-6a6a6a6a6a6a \
		--worker worker-dev --mission "$intruder" --replacement-mission "$ghost" \
		--queue-item "$item" --trace bbbbbbbb-1111-4111-8111-bbbbbbbbbbbb \
		--reason "end the duplicate claim" --evidence "mission:$intruder" \
		--payload '{"replace":"now"}'
	[ "$status" -eq 1 ]
	echo "$output" | grep -Fq "is refused because it would leave worker worker-dev holding more than one active mission slot"
	[ "$(cc_ledger "$COCKPIT_CONTROL_ROOT" '" ".join(c["mission_id"] for c in ledger["mission_slots"]["worker-dev"]["conflicts"])')" = \
		"$intruder $intruder $ghost" ]
	# The ghost mission was never materialized, so no worker could ever end it.
	[ "$(cc_ledger "$COCKPIT_CONTROL_ROOT" 'len(ledger["worker_missions"])')" -eq 2 ]

	# The live duplicate still blocks, and ending it still clears the block: the
	# refused replacement added durable evidence, not a second thing to repair.
	cc_tick --as-of 2026-09-04T10:05:00.000000Z
	[ "$status" -eq 1 ]
	echo "$output" | grep -Fq "outcome blocked reason worker-mission-conflict"
	echo "$output" | grep -Fq "mission-contest worker-dev retained $kept refused $intruder"
	cc_emit --state cancelled --worker worker-dev --mission "$intruder" --queue-item "$item" \
		--trace aaaaaaaa-1111-4111-8111-aaaaaaaaaaaa --sequence 3 \
		--reason "the duplicate claim was ended" --evidence "mission:$intruder"
	cc_tick --as-of 2026-09-04T10:06:00.000000Z
	[ "$status" -eq 0 ]
	echo "$output" | grep -Fq "contested 0"
	cc_absent_output "worker-mission-conflict"
	[ "$(cc_slot_mission)" = "$kept" ]
	[ "$(cc_ledger "$COCKPIT_CONTROL_ROOT" 'len(ledger["mission_slots"]["worker-dev"]["conflicts"])')" -eq 3 ]
}

# --- AC3: repair derived state, never guess authoritative state --------------

@test "AC3 BDD3 a journal that cannot be replayed is refused and nothing is guessed" {
	local contents out err code target
	cc_stale_cockpit journal
	out="$BATS_TEST_TMPDIR/journal.out"
	err="$BATS_TEST_TMPDIR/journal.err"

	# Given the journal contains malformed authoritative data.
	target="$(find "$COCKPIT_CONTROL_ROOT/events" -name '*.json' | sort | sed -n 2p)"
	printf 'not an event at all\n' >"$target"
	contents="$(cc_control_contents "$COCKPIT_CONTROL_ROOT")"

	# When recovery runs, then no state is guessed and explicit repair is
	# requested. The refusal names the one file and the one repair that exists.
	code=0
	"$OVERSEER_BIN" tick --as-of 2026-09-04T10:30:00.000000Z >"$out" 2>"$err" || code=$?
	[ "$code" -ne 0 ]
	[ ! -s "$out" ]
	head -n 1 "$err" | grep -Fq "cockpit-overseer: the committed control journal cannot be replayed:"
	grep -Fq "no mission state was derived and nothing was guessed" "$err"
	grep -Fq "[repair: run \`cockpit-control preflight\` to name the exact file under events/, then restore or correct that one file yourself; cockpit-control never rewrites, reorders, or removes committed authority]" "$err"
	grep -Eq "malformed events/[0-9]{12}-[0-9a-f-]{36}\.json" "$err"
	cc_no_traceback "$err"
	cc_absent "$COCKPIT_CONTROL_ROOT" "$err"

	# Not one byte changed: no repair, no quarantine, no rebuilt projection, and
	# above all no rung of the recovery ladder acted on a store it cannot read.
	[ "$(cc_control_contents "$COCKPIT_CONTROL_ROOT")" = "$contents" ]

	# A dry run refuses identically, and the explicit repair really does name
	# the file: preflight blocks on it and classifies it as authoritative.
	code=0
	"$OVERSEER_BIN" tick --dry-run --as-of 2026-09-04T10:31:00.000000Z \
		>"$out" 2>"$err" || code=$?
	[ "$code" -ne 0 ]
	[ ! -s "$out" ]
	[ "$(cc_control_contents "$COCKPIT_CONTROL_ROOT")" = "$contents" ]
	run "$CONTROL_BIN" preflight
	[ "$status" -ne 0 ]
	echo "$output" | grep -Fq "committed-revisions blocked authoritative"
	echo "$output" | grep -Fq "cockpit-control never rewrites, reorders, or removes committed authority"

	# The guarded store repair is equally refusing: it quarantines derived debris
	# and never rewrites, reorders, or removes one committed event.
	run "$CONTROL_BIN" repair-store
	[ "$(cc_control_contents "$COCKPIT_CONTROL_ROOT")" = "$contents" ]
	[ -f "$target" ]
	grep -Fqx "not an event at all" "$target"
}

@test "AC3 the derived projection is repaired by replay, atomically and idempotently" {
	local mission clean before
	cc_stale_cockpit repair
	mission="$CC_MISSION"
	cc_tick --as-of 2026-09-04T10:30:00.000000Z
	[ "$status" -eq 0 ]
	echo "$output" | grep -Fq "reason stale-mission-nudged"
	cc_tick --as-of 2026-09-04T10:31:00.000000Z
	[ "$status" -eq 0 ]
	echo "$output" | grep -Fq "reason awaiting-recovery-response"

	# From here the situation is settled, so the ticks below decide nothing and
	# commit nothing: what they measure is repair alone.
	before="$(cc_events "$COCKPIT_CONTROL_ROOT")"
	clean="$(cat "$COCKPIT_CONTROL_ROOT/ledger.json")"

	# A revision mismatch between the journal and the derived ledger is repaired
	# by replaying that journal, and the rebuild is byte-identical to the
	# projection a clean store carries at that revision.
	python3 -c '
import json
import sys

path = sys.argv[1] + "/ledger.json"
with open(path) as handle:
    ledger = json.load(handle)
ledger["revision"] = 1
ledger["mission_recoveries"] = {}
with open(path, "w") as handle:
    handle.write(json.dumps(ledger, indent=2, sort_keys=True) + "\n")
' "$COCKPIT_CONTROL_ROOT"
	cc_tick --as-of 2026-09-04T10:32:00.000000Z
	[ "$status" -eq 0 ]
	echo "$output" | grep -Fq "ledger repair repaired from stale"
	echo "$output" | grep -Fq "tick unchanged action none"
	echo "$output" | grep -Fq "events-committed 0"
	[ "$clean" = "$(cat "$COCKPIT_CONTROL_ROOT/ledger.json")" ]
	[ "$(cc_events "$COCKPIT_CONTROL_ROOT")" -eq "$before" ]
	[ "$(cc_ledger "$COCKPIT_CONTROL_ROOT" 'len(ledger["mission_recoveries"])')" -eq 1 ]

	# Repairing again is a no-op, and so is the explicit repair command: the
	# projection already equals the committed rebuild.
	local repaired
	repaired="$(cat "$COCKPIT_CONTROL_ROOT/ledger.json")"
	cc_tick --as-of 2026-09-04T10:33:00.000000Z
	[ "$status" -eq 0 ]
	echo "$output" | grep -Fq "ledger repair current"
	[ "$repaired" = "$(cat "$COCKPIT_CONTROL_ROOT/ledger.json")" ]
	run "$CONTROL_BIN" replay-ledger
	[ "$status" -eq 0 ]
	echo "$output" | grep -Fq "projection is current"
	[ "$repaired" = "$(cat "$COCKPIT_CONTROL_ROOT/ledger.json")" ]

	# Every recovery decision survives a cold restart: delete all derived state
	# and re-derive it byte-exactly from the immutable committed events alone.
	rm -f "$COCKPIT_CONTROL_ROOT/ledger.json" "$COCKPIT_CONTROL_ROOT/events.jsonl"
	run "$CONTROL_BIN" replay-ledger
	[ "$status" -eq 0 ]
	echo "$output" | grep -Fq "rebuilt ledger.json"
	[ "$repaired" = "$(cat "$COCKPIT_CONTROL_ROOT/ledger.json")" ]
	[ "$(cc_ledger "$COCKPIT_CONTROL_ROOT" 'ledger["mission_recoveries"] != {}')" = "True" ]
	[ "$(cc_events "$COCKPIT_CONTROL_ROOT")" -eq "$before" ]

	# And the controller reaches the same conclusion from the rebuilt store.
	cc_tick --as-of 2026-09-04T10:34:00.000000Z
	[ "$status" -eq 0 ]
	echo "$output" | grep -Fq "tick unchanged action none"
	[ "$(cc_ledger "$COCKPIT_CONTROL_ROOT" 'ledger["worker_missions"]["'"$mission"'"]["lifecycle"]["state"]')" = "running" ]
}

@test "AC3 a replay that does not settle the projection stops the tick, committing nothing" {
	local before contents
	cc_stale_cockpit unsettled
	before="$(cc_events "$COCKPIT_CONTROL_ROOT")"

	# Something outside the control protocol keeps rewriting derived state, so
	# replay runs and the projection still does not equal the committed rebuild.
	# The tick refuses rather than deciding from it, and above all it commits
	# nothing: committing would itself replace the projection and destroy the
	# evidence of whatever is rewriting it.
	printf 'not a projection\n' >"$COCKPIT_CONTROL_ROOT/ledger.json"
	contents="$(cc_control_contents "$COCKPIT_CONTROL_ROOT")"
	run python3 -c '
import sys

sys.path.insert(0, sys.argv[1])
import cockpit_control

corrupt = sys.argv[2] + "/ledger.json"


def rewrite_after_replacement(boundary, projection):
    if boundary == "ledger-replaced":
        with open(corrupt, "w") as handle:
            handle.write("still not a projection\n")


cockpit_control._ledger_projection_fault = rewrite_after_replacement
try:
    cockpit_control.controller_tick(sys.argv[2], as_of="2026-09-04T10:30:00.000000Z")
except cockpit_control.ControlStoreError as exc:
    print("refused", exc)
else:
    print("BUG: a tick decided from derived state replay could not settle")
' "$MODULE_DIR" "$COCKPIT_CONTROL_ROOT"
	[ "$status" -eq 0 ]
	echo "$output" | grep -Fq "refused ledger.json still does not equal the committed rebuild after replay (corrupt)"
	echo "$output" | grep -Fq "no decision was taken and no event was committed"
	echo "$output" | grep -Fq "[repair: repair ledger.json yourself and run \`cockpit-control preflight\`"
	cc_absent_output "BUG"
	[ "$(cc_events "$COCKPIT_CONTROL_ROOT")" -eq "$before" ]
	[ ! -e "$COCKPIT_CONTROL_ROOT/locks/control.lock" ]

	# With the interference removed, the very next tick repairs and proceeds.
	cc_tick --as-of 2026-09-04T10:31:00.000000Z
	[ "$status" -eq 0 ]
	echo "$output" | grep -Fq "ledger repair repaired from corrupt"
	echo "$output" | grep -Fq "reason stale-mission-nudged"
	run "$CONTROL_BIN" validate
	[ "$status" -eq 0 ]
}

@test "AC3 tick --dry-run reports recovery and repair and changes not one byte" {
	local contents ledger_before
	cc_stale_cockpit dryrun

	# The first dry run also creates the process-scoped transition guard, so
	# byte-inertness is compared across the second and third runs.
	cc_tick --dry-run --as-of 2026-09-04T10:30:00.000000Z
	[ "$status" -eq 0 ]
	echo "$output" | grep -Fq "tick would-recover action recover-mission"
	echo "$output" | grep -Fq "would take action recover-mission"
	echo "$output" | grep -Fq "events-committed 0"
	echo "$output" | grep -Fq "recovery rung nudge"
	contents="$(cc_control_contents "$COCKPIT_CONTROL_ROOT")"
	cc_tick --dry-run --as-of 2026-09-04T10:30:00.000000Z
	[ "$status" -eq 0 ]
	echo "$output" | grep -Fq "tick would-recover action recover-mission"
	[ "$(cc_control_contents "$COCKPIT_CONTROL_ROOT")" = "$contents" ]
	[ "$(find "$COCKPIT_CONTROL_ROOT/pending" -type f | wc -l)" -eq 0 ]
	[ ! -e "$COCKPIT_CONTROL_ROOT/locks/control.lock" ]

	# A dry run reports the derived repair it would perform and performs none of
	# it: the projection it refused to trust is left exactly as it found it.
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
	ledger_before="$(cat "$COCKPIT_CONTROL_ROOT/ledger.json")"
	contents="$(cc_control_contents "$COCKPIT_CONTROL_ROOT")"
	cc_tick --dry-run --as-of 2026-09-04T10:31:00.000000Z
	[ "$status" -eq 0 ]
	echo "$output" | grep -Fq "ledger repair would-repair from divergent"
	echo "$output" | grep -Fq "tick would-recover action recover-mission"
	[ "$ledger_before" = "$(cat "$COCKPIT_CONTROL_ROOT/ledger.json")" ]
	[ "$(cc_control_contents "$COCKPIT_CONTROL_ROOT")" = "$contents" ]
	[ "$(find "$COCKPIT_CONTROL_ROOT/events" -name '*.json' | wc -l)" -eq 3 ]
}

# --- structural invariants ---------------------------------------------------

@test "the bounded recovery vocabulary is closed, finite, and reaches no record it may not" {
	run python3 -c '
import inspect
import sys

sys.path.insert(0, sys.argv[1])
import cockpit_control as c

# The ladder is a fixed tuple that ends in the one rung that delivers nothing,
# which is what makes recovery terminate rather than repeat.
if c.CONTROLLER_RECOVERY_LADDER[-1] != c.CONTROLLER_RECOVERY_ESCALATE:
    raise SystemExit("the ladder does not end in escalation")
if c.CONTROLLER_RECOVERY_ESCALATE in c.CONTROLLER_RECOVERY_COMMAND_RUNGS:
    raise SystemExit("the terminal rung delivers a command")
if tuple(c.CONTROLLER_RECOVERY_COMMAND_RUNGS) != tuple(c.CONTROLLER_RECOVERY_LADDER[:-1]):
    raise SystemExit("the delivering rungs are not the ladder minus its terminal rung")
for ladder in (
    c.CONTROLLER_RECOVERY_LADDER,
    c.CONTROLLER_TERMINAL_ITEM_LADDER,
    c.CONTROLLER_UNREPLACEABLE_LADDER,
):
    if len(set(ladder)) != len(ladder):
        raise SystemExit("a ladder repeats a rung: %r" % (ladder,))
    if ladder[-1] != c.CONTROLLER_RECOVERY_ESCALATE:
        raise SystemExit("a ladder does not terminate: %r" % (ladder,))
    if not set(ladder[:-1]) <= set(c.CONTROLLER_RECOVERY_COMMAND_RUNGS):
        raise SystemExit("a ladder invents a rung: %r" % (ladder,))
    order = [c.CONTROLLER_RECOVERY_LADDER.index(rung) for rung in ladder]
    if order != sorted(order):
        raise SystemExit("a ladder reorders the fixed ladder: %r" % (ladder,))
print("ladders", len(c.CONTROLLER_RECOVERY_LADDER))

# Every command rung has exactly one reason, one command type, and one closed
# way of being reported; nothing is left to be assembled from prose.
for rung in c.CONTROLLER_RECOVERY_COMMAND_RUNGS:
    reason = c.CONTROLLER_RECOVERY_RUNG_REASONS[rung]
    if reason not in c.CONTROLLER_RECOVERY_REASONS:
        raise SystemExit("rung %r records a reason outside the vocabulary" % rung)
    if rung not in c.CONTROLLER_RECOVERY_COMMAND_PAYLOAD_TYPES:
        raise SystemExit("rung %r digests no command type" % rung)
    if c.CONTROLLER_RECOVERY_COMMAND_PAYLOAD_TYPES[rung] not in c.MANAGED_COMMAND_TYPES:
        raise SystemExit("rung %r delivers an unmanaged command type" % rung)

# A recovery reason is carried by a managed mission-control record and never by
# a controller observation, so no observation record can ever claim one.
overlap = set(c.CONTROLLER_RECOVERY_REASONS) & set(c.CONTROLLER_REASONS)
if overlap:
    raise SystemExit("a recovery reason can be recorded as an observation: %r" % overlap)
if set(c.CONTROLLER_ACTION_REASONS) != set(c.CONTROLLER_REASONS) | set(c.CONTROLLER_RECOVERY_REASONS):
    raise SystemExit("the decidable reason vocabulary drifted")
for reason in c.CONTROLLER_BLOCKING_REASONS:
    if reason not in c.CONTROLLER_OBSERVATION_REASONS:
        raise SystemExit("a blocking reason cannot be recorded: %r" % reason)
print("reasons", len(c.CONTROLLER_ACTION_REASONS))

# The decision function still cannot see rules 7 and 8.
parameters = inspect.signature(c.select_controller_action).parameters
if list(parameters) != ["evidence"]:
    raise SystemExit("the decision takes something other than reconciled evidence")
for forbidden in ("live_status", "pane_status", "diagnostics"):
    if forbidden in c.ControllerEvidence.__dataclass_fields__:
        raise SystemExit("diagnostic evidence reached the decision: %r" % forbidden)
print("precedence intact")

# Every vocabulary above is load-bearing rather than documentation: an action or
# a derived-state classification outside it is refused at the one choke point
# every decision passes through, so no future branch can put free text where a
# skill or a test reads a closed token.
for kind, reason in (
    ("restart-worker", c.CONTROLLER_REASON_QUEUE_EMPTY),
    (c.CONTROLLER_ACTION_OBSERVE, "looked-wrong-to-me"),
):
    try:
        c.ControllerAction(
            kind=kind,
            outcome=c.CONTROLLER_OBSERVED,
            reason=reason,
            state_key=c.controller_state_key({"probe": True}),
        )
    except c.ControlStoreError as exc:
        print("action-refused", exc)
    else:
        raise SystemExit("BUG: %r/%r was accepted" % (kind, reason))
print("vocabularies enforced")
' "$MODULE_DIR"
	[ "$status" -eq 0 ]
	echo "$output" | grep -Fq "ladders 5"
	echo "$output" | grep -Fq "precedence intact"
	echo "$output" | grep -Fq "action-refused a controller tick cannot take unknown action 'restart-worker'"
	echo "$output" | grep -Fq "action-refused a controller tick cannot decide for unknown reason 'looked-wrong-to-me'"
	echo "$output" | grep -Fq "vocabularies enforced"
	cc_absent_output "BUG"

	# The same guard covers the derived-state classification the evidence holds.
	cc_cockpit vocabulary
	cc_active_item implementing >/dev/null
	run python3 -c '
import dataclasses
import sys

sys.path.insert(0, sys.argv[1])
import cockpit_control

tick = cockpit_control.ControllerTick(sys.argv[2], as_of="2026-09-04T10:00:00.000000Z")
try:
    dataclasses.replace(tick._gathered_evidence(), ledger_repair="looks-fine")
except cockpit_control.ControlStoreError as exc:
    print("evidence-refused", exc)
else:
    print("BUG: an invented derived-state classification was accepted")
' "$MODULE_DIR" "$COCKPIT_CONTROL_ROOT"
	[ "$status" -eq 0 ]
	echo "$output" | grep -Fq "evidence-refused unknown derived-state classification 'looks-fine'"
	cc_absent_output "BUG"
}

@test "an observation an earlier build recorded still replays after the repair moved" {
	cc_cockpit legacy

	# TH3.E3.US1 could record `ledger-projection-divergent` as a blocking
	# observation. TH3.E3.US2 no longer selects it, but a committed event is
	# never rewritten, so the reason must stay in the closed vocabulary or every
	# store that ever recorded one would become unreplayable.
	run python3 -c '
import sys

sys.path.insert(0, sys.argv[1])
import cockpit_control

record = cockpit_control.build_controller_observation(
    outcome=cockpit_control.CONTROLLER_BLOCKED,
    reason=cockpit_control.CONTROLLER_REASON_LEDGER_DIVERGENT,
    state_key=cockpit_control.controller_state_key({"legacy": True}),
    observed_at="2026-09-04T10:00:00.000000Z",
)
result = cockpit_control.publish_control_event(
    sys.argv[2],
    "controller-observation-blocked",
    actor="cockpit-overseer",
    payload={"controller_observation": record},
)
state = cockpit_control.read_mission_state(result.root)
print("committed", result.committed)
print("fold", state.mission_outcomes[result.event_id][1])
print("reason", state.controller["reason"])
' "$MODULE_DIR" "$COCKPIT_CONTROL_ROOT"
	[ "$status" -eq 0 ]
	echo "$output" | grep -Fq "committed True"
	echo "$output" | grep -Fq "fold observed"
	echo "$output" | grep -Fq "reason ledger-projection-divergent"

	# The whole store still replays, still validates, and still ticks.
	run "$CONTROL_BIN" replay-ledger
	[ "$status" -eq 0 ]
	run "$CONTROL_BIN" validate
	[ "$status" -eq 0 ]
	cc_tick --as-of 2026-09-04T10:05:00.000000Z
	[ "$status" -eq 0 ]
	echo "$output" | grep -Fq "outcome observed reason no-active-queue-item"
}
