#!/usr/bin/env bats
# tests/unit/cmd-overseer.bats — Category-1 unit tests for bin/cockpit-overseer.

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
		file="$state_dir/${target//:/__}.txt"
		[ -f "$file" ] && cat "$file"
		;;
	load-buffer)
		cat "$payload" > "$state_dir/buffer.txt"
		;;
	paste-buffer)
		cp "$state_dir/buffer.txt" "$state_dir/pasted.txt"
		;;
	send-keys)
		: > "$state_dir/send-keys.log"
		;;
	display-message)
		printf '%s\n' "${TMUX_SESSION:-fake-session}"
		;;
	*)
		;;
esac
EOF
	chmod +x "$BATS_TEST_TMPDIR/bin/tmux"
}

@test "cockpit-overseer loop prints a compact delta and then steadies" {
	local pane_file="$BATS_TEST_TMPDIR/tmux-stub/ulysses__worker-test.txt"
	mkdir -p "$(dirname "$pane_file")"
	cat > "$pane_file" <<'EOF'
● Working
RUN-20260618-222040
WORKER-TEST RESULT
  status: GREEN
EOF

	run "$BATS_TEST_DIRNAME/../../bin/cockpit-overseer" loop --session ulysses --window worker-test
	[ "$status" -eq 0 ]
	echo "$output" | grep -q "RUN-20260618-222040"
	echo "$output" | grep -q "working"

	run "$BATS_TEST_DIRNAME/../../bin/cockpit-overseer" loop --session ulysses --window worker-test
	[ "$status" -eq 0 ]
	echo "$output" | grep -q "steady"
}

@test "cockpit-overseer dispatch sends the referenced brief" {
	local brief="$BATS_TEST_TMPDIR/mission.txt"
	cat > "$brief" <<'EOF'
MISSION-ID: M-123
TASK: trim loop traffic
EOF

	run "$BATS_TEST_DIRNAME/../../bin/cockpit-overseer" dispatch --session ulysses --window worker-dev --ref "$brief" --label M-123
	[ "$status" -eq 0 ]
	echo "$output" | grep -q "dispatched M-123"
	cmp -s "$brief" "$BATS_TEST_TMPDIR/tmux-stub/buffer.txt"
}
