#!/usr/bin/env bats
# tests/unit/cmd-protocol.bats — Category-1 unit tests for bin/cockpit-protocol.

load helper

setup() {
	cc_setup_fake_home
	export PATH="$BATS_TEST_TMPDIR/bin:$PATH"
	mkdir -p "$BATS_TEST_TMPDIR/bin"
	cc_setup_tmux_stub
}

cc_setup_tmux_stub() {
	cat > "$BATS_TEST_TMPDIR/bin/tmux" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail

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
		*)
			shift
			;;
	esac
done

case "$cmd" in
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

@test "cockpit-protocol dispatch confirms alternate working markers" {
	local brief="$BATS_TEST_TMPDIR/mission.txt"
	cat > "$brief" <<'EOF'
MISSION-ID: M-123
TASK: validate working marker
EOF

	run "$BATS_TEST_DIRNAME/../../bin/cockpit-protocol" dispatch --target ulysses:worker-dev --message-file "$brief" --enter-delay 0 --confirm-delay 0
	[ "$status" -eq 0 ]
	echo "$output" | grep -q "◉ Working"
	grep -q "TASK: validate working marker" "$BATS_TEST_TMPDIR/tmux-stub/buffer.txt"
	grep -q "ulysses:worker-dev" "$BATS_TEST_TMPDIR/tmux-stub/send-keys-target.txt"
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

@test "cockpit-protocol worker dispatch refuses busy panes unless forced" {
	run "$BATS_TEST_DIRNAME/../../bin/cockpit-protocol" dispatch --worker worker-test --message "TASK: busy" --enter-delay 0 --confirm-delay 0
	[ "$status" -ne 0 ]
	echo "$output" | grep -q "looks busy"

	run "$BATS_TEST_DIRNAME/../../bin/cockpit-protocol" dispatch --worker worker-test --message "TASK: forced" --enter-delay 0 --confirm-delay 0 --force
	[ "$status" -eq 0 ]
	grep -q "TASK: forced" "$BATS_TEST_TMPDIR/tmux-stub/buffer.txt"
	grep -q "portal-local:worker-test" "$BATS_TEST_TMPDIR/tmux-stub/send-keys-target.txt"
}

@test "cockpit-protocol status json includes worker states and reports" {
	run "$BATS_TEST_DIRNAME/../../bin/cockpit-protocol" status --workers worker-dev,worker-test --json
	[ "$status" -eq 0 ]
	echo "$output" | grep -q '"session": "portal-local"'
	echo "$output" | grep -q '"status": "available"'
	echo "$output" | grep -q '"status": "working"'
	echo "$output" | grep -q '"report": "WORKER-DEV DONE"'
}

@test "cockpit-protocol report extracts latest structured worker block" {
	run "$BATS_TEST_DIRNAME/../../bin/cockpit-protocol" report --worker worker-fix --format markdown
	[ "$status" -eq 0 ]
	echo "$output" | grep -q '```'
	echo "$output" | grep -q "ROOT CAUSE: selector drift"
	echo "$output" | grep -q "FIX: updated fixture"
}
