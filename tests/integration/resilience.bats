#!/usr/bin/env bats
# tests/integration/resilience.bats — TH3.E5.US3 end-to-end resilience proof.
#
# Keeps the proof local and deterministic by:
#   * running only repository binaries;
#   * stubbing scheduler/tmux edges (no external services);
#   * driving explicit lifecycle timestamps instead of waiting on wall clock.

load ../unit/helper

setup() {
	cc_setup_fake_home
	mkdir -p "$BATS_TEST_TMPDIR/bin" "$BATS_TEST_TMPDIR/tmux-stub"
	export PATH="$BATS_TEST_TMPDIR/bin:$PATH"
	export CONTROL_BIN="$BATS_TEST_DIRNAME/../../bin/cockpit-control"
	export OVERSEER_BIN="$BATS_TEST_DIRNAME/../../bin/cockpit-overseer"
	export QUEUE_BIN="$BATS_TEST_DIRNAME/../../bin/cockpit-queue"
	export WAKE_BIN="$BATS_TEST_DIRNAME/../../bin/cockpit-wake"

	export COCKPIT_CONTROL_ROOT="$BATS_TEST_TMPDIR/control-root"
	export COCKPIT_QUEUE_ROOT="$BATS_TEST_TMPDIR/queue-root"
	mkdir -p "$COCKPIT_QUEUE_ROOT"
	unset TMUX TMUX_SESSION COCKPIT_SESSION_ID COCKPIT_ID

	cc_setup_at_stub
	cc_setup_crontab_stub
	cc_setup_notify_stub
	cc_setup_tmux_stub
	cc_init_cockpit
}

cc_setup_at_stub() {
	cat >"$BATS_TEST_TMPDIR/bin/at" <<'EOF'
#!/usr/bin/env bash
cat >/dev/null
printf 'job 123 at someday\n' >&2
EOF
	chmod +x "$BATS_TEST_TMPDIR/bin/at"

	cat >"$BATS_TEST_TMPDIR/bin/atrm" <<'EOF'
#!/usr/bin/env bash
exit 0
EOF
	chmod +x "$BATS_TEST_TMPDIR/bin/atrm"
}

cc_setup_crontab_stub() {
	cat >"$BATS_TEST_TMPDIR/bin/crontab" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
state="${BATS_TEST_TMPDIR:-/tmp}/crontab.state"
case "${1:-}" in
	-l)
		if [ -f "$state" ]; then
			cat "$state"
			exit 0
		fi
		printf 'no crontab for isolated-test\n' >&2
		exit 1
		;;
	-)
		cat >"$state"
		exit 0
		;;
	*)
		exit 0
		;;
esac
EOF
	chmod +x "$BATS_TEST_TMPDIR/bin/crontab"
}

cc_setup_notify_stub() {
	cat >"$BATS_TEST_TMPDIR/bin/notify-send" <<'EOF'
#!/usr/bin/env bash
exit 0
EOF
	chmod +x "$BATS_TEST_TMPDIR/bin/notify-send"
}

cc_setup_tmux_stub() {
	cat >"$BATS_TEST_TMPDIR/bin/tmux" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
state_dir="${BATS_TEST_TMPDIR:-/tmp}/tmux-stub"
mkdir -p "$state_dir"
cmd="${1:-}"
shift || true
target=""
key=""
if [ "$cmd" = "show-environment" ]; then
	key="${1:-}"
fi
while [ $# -gt 0 ]; do
	case "$1" in
	-t)
		target="$2"
		shift 2
		;;
	-p)
		shift
		;;
	*)
		shift
		;;
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
	show-environment)
		if [ "$key" = "COCKPIT_CONTROL_ROOT" ] && [ -n "${COCKPIT_CONTROL_ROOT:-}" ]; then
			printf 'COCKPIT_CONTROL_ROOT=%s\n' "$COCKPIT_CONTROL_ROOT"
			exit 0
		fi
		if [ "$key" = "COCKPIT_QUEUE_ROOT" ] && [ -n "${COCKPIT_QUEUE_ROOT:-}" ]; then
			printf 'COCKPIT_QUEUE_ROOT=%s\n' "$COCKPIT_QUEUE_ROOT"
			exit 0
		fi
		exit 1
		;;
	*)
		;;
esac
EOF
	chmod +x "$BATS_TEST_TMPDIR/bin/tmux"
}

cc_init_cockpit() {
	"$CONTROL_BIN" init --queue-root "$COCKPIT_QUEUE_ROOT" \
		--planning-root "$BATS_TEST_TMPDIR/planning" \
		--implementation-root "$BATS_TEST_TMPDIR/implementation" >/dev/null
}

cc_schedule_recurrent_wake() {
	run "$WAKE_BIN" schedule --cron "*/5 * * * *" \
		-s cockpit -w overseer -m "recurrent wake" \
		--intent "bounded resilience observation" --stop-condition "mission terminal or escalation" \
		--label "resilience" --mission "$1" --owner "overseer" --queue-item "$2"
	[ "$status" -eq 0 ]
	printf '%s\n' "$output" | sed -nE 's/.*id=(wake-[0-9a-f-]+).*/\1/p' | head -n1
}

cc_active_item() {
	local item
	item="$("$QUEUE_BIN" enqueue --text "/the-copilot-build-method resilience proof" --title "resilience proof")"
	"$QUEUE_BIN" start-next >/dev/null
	"$QUEUE_BIN" transition "$item" implementing --reason "ready for implementation" >/dev/null
	printf '%s' "$item"
}

cc_ledger_value() {
	python3 -c '
import json
import sys
with open(sys.argv[1] + "/ledger.json") as handle:
    ledger = json.load(handle)
print(eval(sys.argv[2]))
' "$1" "$2"
}

cc_emit_lifecycle() {
	run "$CONTROL_BIN" record-lifecycle "$@"
	[ "$status" -eq 0 ]
}

@test "BDD1 queue-backed mission survives reset, replaces stalled worker, and clears with governed evidence" {
	local item wake_id mission replacement replace_command digest trace
	item="$(cc_active_item)"
	wake_id="$(cc_schedule_recurrent_wake MISSION-TH3 "$item")"
	[ -n "$wake_id" ]

	run "$OVERSEER_BIN" tick --as-of 2026-09-04T10:00:00.000000Z
	[ "$status" -eq 0 ]
	echo "$output" | grep -Fq "action dispatch-mission"
	mission="$(cc_ledger_value "$COCKPIT_CONTROL_ROOT" 'ledger["mission_slots"]["worker-dev"]["mission_id"]')"
	[ -n "$mission" ]

	cc_accept_dispatch "$mission" 2026-09-04T10:01:00.000000Z 2026-09-04T10:07:00.000000Z
	cc_emit_lifecycle --state running --worker worker-dev --mission "$mission" --queue-item "$item" \
		--trace 11111111-1111-4111-8111-111111111111 --sequence 2 \
		--heartbeat-at 2026-09-04T10:02:00.000000Z --fresh-until 2026-09-04T10:07:00.000000Z

	run "$OVERSEER_BIN" reset -s cockpit
	[ "$status" -eq 0 ]
	echo "$output" | grep -Fq "state cleared"

	run "$OVERSEER_BIN" tick --as-of 2026-09-04T10:05:00.000000Z
	[ "$status" -eq 0 ]
	echo "$output" | grep -Fq "reason mission-in-progress"
	echo "$output" | grep -Fq "mission $mission"

	run "$OVERSEER_BIN" tick --as-of 2026-09-04T10:30:00.000000Z
	[ "$status" -eq 0 ]
	run "$OVERSEER_BIN" tick --as-of 2026-09-04T10:40:00.000000Z
	[ "$status" -eq 0 ]
	run "$OVERSEER_BIN" tick --as-of 2026-09-04T10:50:00.000000Z
	[ "$status" -eq 0 ]
	run "$OVERSEER_BIN" tick --as-of 2026-09-04T11:00:00.000000Z
	[ "$status" -eq 0 ]
	echo "$output" | grep -Fq "reason awaiting-recovery-response"
	run "$OVERSEER_BIN" tick --as-of 2026-09-04T11:10:00.000000Z
	[ "$status" -eq 0 ]
	echo "$output" | grep -Fq "reason stale-mission-replaced"

	# Replacement is a request, not evidence that an unresponsive worker stopped.
	[ "$(cc_ledger_value "$COCKPIT_CONTROL_ROOT" 'ledger["mission_slots"]["worker-dev"]["mission_id"]')" = "$mission" ]
	replace_command="$(cc_ledger_value "$COCKPIT_CONTROL_ROOT" 'next(k for k,v in ledger["commands"].items() if v["envelope"]["command_type"] == "mission-replace")')"
	digest="$(cc_ledger_value "$COCKPIT_CONTROL_ROOT" 'ledger["commands"]["'"$replace_command"'"]["envelope"]["payload_digest"]')"
	[ "$(cc_ledger_value "$COCKPIT_CONTROL_ROOT" 'ledger["commands"]["'"$replace_command"'"]["envelope"]["parent_trace_id"]')" = "11111111-1111-4111-8111-111111111111" ]
	run "$CONTROL_BIN" acknowledge-command --command-id "$replace_command" --digest "$digest" --outcome accepted --by worker-dev
	[ "$status" -eq 0 ]
	[ "$(cc_ledger_value "$COCKPIT_CONTROL_ROOT" 'ledger["mission_slots"]["worker-dev"]["mission_id"]')" = "$mission" ]
	run "$CONTROL_BIN" acknowledge-command --command-id "$replace_command" --digest "$digest" --outcome applied --by worker-dev --result file:worker/stopped
	[ "$status" -eq 0 ]
	replacement="$(cc_ledger_value "$COCKPIT_CONTROL_ROOT" 'ledger["mission_slots"]["worker-dev"]["mission_id"]')"
	[ -n "$replacement" ]
	[ "$replacement" != "$mission" ]

	# The replacement must receive its own controller envelope and joint receipt.
	run "$OVERSEER_BIN" tick --as-of 2026-09-04T11:20:00.000000Z
	[ "$status" -eq 0 ]
	echo "$output" | grep -Fq "action dispatch-mission"
	echo "$output" | grep -Fq "mission $replacement"
	cc_accept_dispatch "$replacement" 2026-09-04T11:21:00.000000Z 2026-09-04T11:40:00.000000Z
	trace="$(cc_ledger_value "$COCKPIT_CONTROL_ROOT" 'ledger["worker_missions"]["'"$replacement"'"]["lifecycle"]["trace_id"]')"
	[ -n "$trace" ]
	cc_emit_lifecycle --state running --worker worker-dev --mission "$replacement" --queue-item "$item" \
		--trace "$trace" --sequence 2 \
		--heartbeat-at 2026-09-04T11:22:00.000000Z --fresh-until 2026-09-04T11:40:00.000000Z
	cc_emit_lifecycle --state completed --worker worker-dev --mission "$replacement" --queue-item "$item" \
		--trace "$trace" --sequence 3 \
		--evidence "queue:$item" --evidence "test:RUN-TH3-E5-US3"

	run "$QUEUE_BIN" transition "$item" delivered --reason "replacement finished"
	[ "$status" -eq 0 ]
	run "$QUEUE_BIN" clear-current --reason "governed run complete" \
		--e2e-run RUN-TH3-E5-US3 --e2e-result passed
	[ "$status" -eq 0 ]

	run python3 -c '
import json
import sys
from pathlib import Path

root = Path(sys.argv[1])
item_id = sys.argv[2]
replacement = sys.argv[3]
item = json.loads((root / "queue-root" / "items" / f"{item_id}.yaml").read_text())
assert item["state"] == "cleared", item
assert item["e2e_run"] == "RUN-TH3-E5-US3", item
ledger = json.loads((root / "control-root" / "ledger.json").read_text())
refs = set(ledger["worker_missions"][replacement]["lifecycle"]["evidence_refs"])
assert f"queue:{item_id}" in refs, refs
assert "test:RUN-TH3-E5-US3" in refs, refs
' "$BATS_TEST_TMPDIR" "$item" "$replacement"
	[ "$status" -eq 0 ]

	run "$WAKE_BIN" cancel "$wake_id"
	[ "$status" -eq 0 ]
	run "$WAKE_BIN" _guard-fire "$wake_id"
	[ "$status" -eq 1 ]
	echo "$output" | grep -Fq "lifecycle/status"
}

@test "BDD2 three blocked ticks escalate once and suspend recurrent wake for human decision" {
	local item wake_id mission escalation
	item="$(cc_active_item)"
	wake_id="$(cc_schedule_recurrent_wake MISSION-ESC "$item")"
	[ -n "$wake_id" ]

	run "$OVERSEER_BIN" tick --as-of 2026-09-04T10:00:00.000000Z
	[ "$status" -eq 0 ]
	mission="$(cc_ledger_value "$COCKPIT_CONTROL_ROOT" 'ledger["mission_slots"]["worker-dev"]["mission_id"]')"
	[ -n "$mission" ]
	cc_accept_dispatch "$mission" 2026-09-04T10:01:00.000000Z 2026-09-04T10:07:00.000000Z
	cc_emit_lifecycle --state running --worker worker-dev --mission "$mission" --queue-item "$item" \
		--trace 33333333-3333-4333-8333-333333333333 --sequence 2 \
		--heartbeat-at 2026-09-04T10:02:00.000000Z --fresh-until 2026-09-04T10:07:00.000000Z

	run "$QUEUE_BIN" reject "$item" --reason "operator cancelled this item"
	[ "$status" -eq 0 ]
	run "$OVERSEER_BIN" tick --as-of 2026-09-04T10:20:00.000000Z
	[ "$status" -eq 0 ]
	run "$OVERSEER_BIN" tick --as-of 2026-09-04T10:30:00.000000Z
	[ "$status" -eq 0 ]
	run "$OVERSEER_BIN" tick --as-of 2026-09-04T10:40:00.000000Z
	[ "$status" -eq 1 ]
	run "$OVERSEER_BIN" tick --as-of 2026-09-04T10:50:00.000000Z
	[ "$status" -eq 1 ]
	run "$OVERSEER_BIN" tick --as-of 2026-09-04T11:00:00.000000Z
	[ "$status" -eq 1 ]
	echo "$output" | grep -Fq "bounded-recovery-escalated"

	escalation="$COCKPIT_CONTROL_ROOT/escalations/$(ls "$COCKPIT_CONTROL_ROOT/escalations" | head -n1)"
	[ -f "$escalation" ]
	run python3 -c '
import json
import sys
doc = json.loads(open(sys.argv[1]).read())
assert doc["status"] == "decision-requested", doc
assert doc["count"] == 3, doc
assert doc["pending_decision"] == "decide mission", doc
assert len(doc.get("evidence_refs", [])) >= 2, doc
' "$escalation"
	[ "$status" -eq 0 ]

	run "$WAKE_BIN" cancel "$wake_id"
	[ "$status" -eq 0 ]
	run "$WAKE_BIN" _guard-fire "$wake_id"
	[ "$status" -eq 1 ]
	echo "$output" | grep -Fq "lifecycle/status"
}

@test "BDD3 completion without governed test result fails clearance and no wake delivery success is reported" {
	local item wake_id mission
	item="$(cc_active_item)"
	wake_id="$(cc_schedule_recurrent_wake MISSION-NOGOV "$item")"
	[ -n "$wake_id" ]

	run "$OVERSEER_BIN" tick --as-of 2026-09-04T10:00:00.000000Z
	[ "$status" -eq 0 ]
	mission="$(cc_ledger_value "$COCKPIT_CONTROL_ROOT" 'ledger["mission_slots"]["worker-dev"]["mission_id"]')"
	[ -n "$mission" ]
	cc_accept_dispatch "$mission" 2026-09-04T10:01:00.000000Z 2026-09-04T11:01:00.000000Z
	cc_emit_lifecycle --state running --worker worker-dev --mission "$mission" --queue-item "$item" \
		--trace 44444444-4444-4444-8444-444444444444 --sequence 2 \
		--heartbeat-at 2026-09-04T10:02:00.000000Z --fresh-until 2026-09-04T11:01:00.000000Z
	cc_emit_lifecycle --state completed --worker worker-dev --mission "$mission" --queue-item "$item" \
		--trace 44444444-4444-4444-8444-444444444444 --sequence 3 \
		--evidence "queue:$item"

	run "$QUEUE_BIN" transition "$item" delivered --reason "worker reported completion"
	[ "$status" -eq 0 ]
	run "$QUEUE_BIN" clear-current --reason "attempt without governed result"
	[ "$status" -eq 1 ]
	echo "$output" | grep -Fq "clear-current requires --e2e-run or --waiver"

	run python3 -c '
import json
import sys
from pathlib import Path
item = json.loads((Path(sys.argv[1]) / "queue-root" / "items" / f"{sys.argv[2]}.yaml").read_text())
assert item["state"] == "delivered", item
' "$BATS_TEST_TMPDIR" "$item"
	[ "$status" -eq 0 ]

	run "$WAKE_BIN" status
	[ "$status" -eq 0 ]
	echo "$output" | grep -Eq "✅ fired[[:space:]]*: 0"
	echo "$output" | grep -Eq "⏳ pending[[:space:]]*: 1"
}
