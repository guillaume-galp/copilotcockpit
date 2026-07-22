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
		printf '◉ Working\n'
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
