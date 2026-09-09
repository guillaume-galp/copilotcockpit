#!/usr/bin/env bats
# tests/unit/cmd-protocol.bats — Category-1 unit tests for bin/cockpit-protocol.

load helper

setup() {
	cc_setup_fake_home
	export PATH="$BATS_TEST_TMPDIR/bin:$PATH"
	mkdir -p "$BATS_TEST_TMPDIR/bin"
	cc_setup_tmux_stub
	unset TMUX TMUX_CONTROL_ROOT
	export COCKPIT_CONTROL_ROOT="$BATS_TEST_TMPDIR/control-root"
	"$BATS_TEST_DIRNAME/../../bin/cockpit-control" init >/dev/null
}

cc_setup_tmux_stub() {
	cat > "$BATS_TEST_TMPDIR/bin/tmux" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail

if [ -n "${TMUX_PASTE_STATE:-}" ]; then
	exec python3 "$TMUX_PASTE_FIXTURE" "$@"
fi
state_dir="${BATS_TEST_TMPDIR:-/tmp}/tmux-stub"
mkdir -p "$state_dir"

cmd="${1:-}"
shift || true
payload="${1:-}"
target=""

while [ $# -gt 0 ]; do
	case "$1" in
		-t)
			target="$2"
			shift 2
			;;
		-p|-F)
			shift
			;;
		-b)
			shift 2
			;;
		*)
			payload="$1"
			shift
			;;
	esac
done

case "$cmd" in
	show-environment)
		if [ "$payload" = "COCKPIT_CONTROL_ROOT" ] && [ -n "${TMUX_CONTROL_ROOT:-}" ]; then
			printf 'COCKPIT_CONTROL_ROOT=%s\n' "$TMUX_CONTROL_ROOT"
			exit 0
		fi
		exit 1
		;;
	capture-pane)
		case "$target" in
			portal-local:worker-dev)
				printf '❯ ready\nTRACE-ID: trace-dev\nWORKER-DEV DONE\n  story/task: protocol meta\n  tests: pass\n'
				;;
			*:worker-dev)
				printf '◉ Working\n'
				;;
			*:worker-fix)
				printf 'ROOT CAUSE: selector drift\nFIX: updated fixture\n'
				;;
			*:worker-test)
				printf '◉ Working\n'
				;;
			*)
				printf '◉ Working\n'
				;;
		esac
		;;
	load-buffer)
		cat "$payload" > "$state_dir/buffer.txt"
		;;
	paste-buffer)
		cp "$state_dir/buffer.txt" "$state_dir/pasted.txt"
		;;
	send-keys)
		printf '%s\n' "$target" > "$state_dir/send-keys-target.txt"
		;;
	display-message)
		printf 'portal-local\n'
		;;
	list-sessions)
		printf 'portal-local\n'
		;;
	list-windows)
		printf 'overseer\nworker-test\nworker-dev\nworker-fix\nchromium\n'
		;;
	*)
		;;
esac
EOF
	chmod +x "$BATS_TEST_TMPDIR/bin/tmux"
}

@test "cockpit-protocol bootstrap enqueue reports pane markers as diagnostics not acceptance" {
	local brief="$BATS_TEST_TMPDIR/mission.txt"
	cat > "$brief" <<'EOF'
MISSION-ID: M-123
TASK: validate working marker
EOF

	run "$BATS_TEST_DIRNAME/../../bin/cockpit-protocol" dispatch --bootstrap --target ulysses:worker-dev --message-file "$brief" --enter-delay 0 --confirm-delay 0
	[ "$status" -eq 0 ]
	echo "$output" | grep -q "◉ Working"
	echo "$output" | grep -Fq "transport=enqueued; worker start/acceptance not confirmed"
	grep -q "TASK: validate working marker" "$BATS_TEST_TMPDIR/tmux-stub/buffer.txt"
	grep -q "ulysses:worker-dev" "$BATS_TEST_TMPDIR/tmux-stub/send-keys-target.txt"
}

@test "cockpit-protocol bootstrap separates delayed paste from a single Enter with an isolated buffer" {
	export TMUX_PASTE_STATE="$BATS_TEST_TMPDIR/paste-state"
	export TMUX_PASTE_FIXTURE="$CC_REPO_ROOT/tests/transport/delayed-paste-tmux.py"
	local brief="$BATS_TEST_TMPDIR/brief.txt"
	printf 'TASK: delayed paste\nliteral $HOME and Enter\n' >"$brief"
	run "$CC_REPO_ROOT/bin/cockpit-protocol" dispatch --bootstrap --target isolated:worker-fix \
		--message-file "$brief" --enter-delay 0 --confirm-delay 0
	[ "$status" -eq 0 ]
	echo "$output" | grep -Fq "worker start/acceptance not confirmed"
	cmp "$brief" "$TMUX_PASTE_STATE/submitted.txt"
	python3 - "$TMUX_PASTE_STATE" <<'PY'
import json, sys
from pathlib import Path
root = Path(sys.argv[1])
calls = [json.loads(line) for line in (root / "calls.jsonl").read_text().splitlines()]
assert [call[0] for call in calls] == ["load-buffer", "paste-buffer", "send-keys", "capture-pane"]
assert calls[1] == ["paste-buffer", "-p", "-r", "-d", "-b", calls[0][2], "-t", "isolated:worker-fix"]
assert calls[2] == ["send-keys", "-t", "isolated:worker-fix", "Enter"]
assert json.loads((root / "state.json").read_text())["buffers"] == {"ambient": "foreign clipboard"}
PY
}

@test "cockpit-protocol bootstrap transport errors never retry paste or Enter" {
	export TMUX_PASTE_FIXTURE="$CC_REPO_ROOT/tests/transport/delayed-paste-tmux.py"
	local phase
	for phase in load-buffer paste-buffer send-keys; do
		export TMUX_PASTE_STATE="$BATS_TEST_TMPDIR/failure-$phase"
		export TMUX_PASTE_FAIL="$phase"
		run "$CC_REPO_ROOT/bin/cockpit-protocol" dispatch --bootstrap --target isolated:worker-fix \
			--message "TASK: fail safely" --confirm-delay 0
		[ "$status" -ne 0 ]
		echo "$output" | grep -Fq "delivery unknown; do not automatically resubmit"
		python3 - "$TMUX_PASTE_STATE" "$phase" <<'PY'
import json, sys
from pathlib import Path
calls = [json.loads(line)[0] for line in (Path(sys.argv[1]) / "calls.jsonl").read_text().splitlines()]
expected = {
    "load-buffer": ["load-buffer", "delete-buffer"],
    "paste-buffer": ["load-buffer", "paste-buffer", "delete-buffer"],
    "send-keys": ["load-buffer", "paste-buffer", "send-keys"],
}
assert calls == expected[sys.argv[2]], calls
assert not any(key.startswith("cockpit-") for key in
               json.loads((Path(sys.argv[1]) / "state.json").read_text())["buffers"])
PY
	done
}

@test "cockpit-protocol rejects invalid paste delays before transport" {
	local delay
	for delay in -1 31; do
		run "$CC_REPO_ROOT/bin/cockpit-protocol" dispatch --bootstrap --target isolated:worker-fix \
			--message "TASK: invalid" --enter-delay "$delay"
		[ "$status" -ne 0 ]
		echo "$output" | grep -Fq -- "--enter-delay must be between 0 and 30"
		[ ! -e "$BATS_TEST_TMPDIR/tmux-stub" ]
	done
}

@test "cockpit-protocol meta cockpit reports current cockpit as json" {
	run "$BATS_TEST_DIRNAME/../../bin/cockpit-protocol" meta cockpit --json
	[ "$status" -eq 0 ]
	echo "$output" | grep -q '"current_session": "portal-local"'
	echo "$output" | grep -q '"worker-dev": "portal-local:worker-dev"'
	echo "$output" | grep -q '"health": "ok"'
}

@test "cockpit-protocol worker shortcuts resolve session and tail panes" {
	run "$BATS_TEST_DIRNAME/../../bin/cockpit-protocol" tail --worker worker-dev --lines 10
	[ "$status" -eq 0 ]
	echo "$output" | grep -q "WORKER-DEV DONE"

	run "$BATS_TEST_DIRNAME/../../bin/cockpit-protocol" send --worker worker-dev --text "git status"
	[ "$status" -eq 0 ]
	grep -q "portal-local:worker-dev" "$BATS_TEST_TMPDIR/tmux-stub/send-keys-target.txt"
}

@test "cockpit-protocol worker dispatch refuses the direct mission bypass even when forced" {
	run "$BATS_TEST_DIRNAME/../../bin/cockpit-protocol" dispatch --worker worker-test --message "TASK: busy" --enter-delay 0 --confirm-delay 0
	[ "$status" -ne 0 ]
	echo "$output" | grep -Fq "direct mission dispatch is retired"

	run "$BATS_TEST_DIRNAME/../../bin/cockpit-protocol" dispatch --worker worker-test --message "TASK: forced" --enter-delay 0 --confirm-delay 0 --force
	[ "$status" -ne 0 ]
	echo "$output" | grep -Fq "direct mission dispatch is retired"
	[ ! -e "$BATS_TEST_TMPDIR/tmux-stub/buffer.txt" ]
}

@test "cockpit-protocol mission refuses the retired direct mission bypass" {
	run "$BATS_TEST_DIRNAME/../../bin/cockpit-protocol" mission \
		--worker worker-dev \
		--id 0d8d7b28-8c9f-4d10-9d2f-9ebd1dc96af8 \
		--message "TASK: must be durable"
	[ "$status" -ne 0 ]
	echo "$output" | grep -Fq "mission is retired"
	echo "$output" | grep -Fq "cockpit-overseer tick"
	[ ! -e "$BATS_TEST_TMPDIR/tmux-stub/buffer.txt" ]
}

@test "cockpit-protocol status json includes worker states and reports" {
	run "$BATS_TEST_DIRNAME/../../bin/cockpit-protocol" status --workers worker-dev,worker-test --json
	[ "$status" -eq 0 ]
	echo "$output" | grep -q '"session": "portal-local"'
	echo "$output" | grep -q '"status": "available"'
	echo "$output" | grep -q '"observation": "working"'
	echo "$output" | grep -q '"report": "WORKER-DEV DONE"'
}

@test "cockpit-protocol report extracts latest structured worker block" {
	run "$BATS_TEST_DIRNAME/../../bin/cockpit-protocol" report --worker worker-fix --format markdown
	[ "$status" -eq 0 ]
	echo "$output" | grep -q '```'
	echo "$output" | grep -q "ROOT CAUSE: selector drift"
	echo "$output" | grep -q "FIX: updated fixture"
}

@test "cockpit-protocol uses only the active tmux control-root fallback" {
	export TMUX="$BATS_TEST_TMPDIR/tmux-socket,123,0"
	export TMUX_CONTROL_ROOT="$COCKPIT_CONTROL_ROOT"
	unset COCKPIT_CONTROL_ROOT

	run "$BATS_TEST_DIRNAME/../../bin/cockpit-protocol" report --worker worker-fix
	[ "$status" -eq 0 ]
	echo "$output" | grep -Fq "ROOT CAUSE: selector drift"
}

@test "cockpit-protocol control commands fail before protocol or tmux state changes" {
	local worker="root-validation-$BATS_TEST_NUMBER"
	unset COCKPIT_CONTROL_ROOT

	run "$BATS_TEST_DIRNAME/../../bin/cockpit-protocol" dispatch \
		--target portal-local:worker-dev \
		--message "TASK: must not dispatch" \
		--enter-delay 0 \
		--confirm-delay 0
	[ "$status" -ne 0 ]
	echo "$output" | grep -Fq "COCKPIT_CONTROL_ROOT is required"

	run "$BATS_TEST_DIRNAME/../../bin/cockpit-protocol" mission \
		--worker worker-dev \
		--id 0d8d7b28-8c9f-4d10-9d2f-9ebd1dc96af8 \
		--message "TASK: must not dispatch"
	[ "$status" -ne 0 ]
	echo "$output" | grep -Fq "COCKPIT_CONTROL_ROOT is required"

	run "$BATS_TEST_DIRNAME/../../bin/cockpit-protocol" report --worker worker-dev
	[ "$status" -ne 0 ]
	echo "$output" | grep -Fq "COCKPIT_CONTROL_ROOT is required"

	run "$BATS_TEST_DIRNAME/../../bin/cockpit-protocol" ask \
		--worker "$worker" \
		--command-id 0d8d7b28-8c9f-4d10-9d2f-9ebd1dc96af8 \
		--mission 0d8d7b28-8c9f-4d10-9d2f-9ebd1dc96af9 \
		--queue-item QI-root-test --trace 0d8d7b28-8c9f-4d10-9d2f-9ebd1dc96af7 \
		--category root-validation --body-ref note:question --payload '{}'
	[ "$status" -ne 0 ]
	echo "$output" | grep -Fq "COCKPIT_CONTROL_ROOT is required"

	run "$BATS_TEST_DIRNAME/../../bin/cockpit-protocol" reply \
		--command-id 0d8d7b28-8c9f-4d10-9d2f-9ebd1dc96af6 \
		--answers 0d8d7b28-8c9f-4d10-9d2f-9ebd1dc96af8 \
		--by operator --trace 0d8d7b28-8c9f-4d10-9d2f-9ebd1dc96af5 \
		--category root-validation --body-ref note:answer --payload '{}'
	[ "$status" -ne 0 ]
	echo "$output" | grep -Fq "COCKPIT_CONTROL_ROOT is required"

	[ ! -e "$BATS_TEST_TMPDIR/tmux-stub" ]
	[ ! -e "/tmp/${worker}-question.txt" ]
	[ ! -e "/tmp/${worker}-answer.txt" ]
}
