#!/usr/bin/env bats
# tests/unit/cmd-wake.bats — Category-1 unit tests for bin/cockpit-wake.

load helper

setup() {
	cc_setup_fake_home
	mkdir -p "$BATS_TEST_TMPDIR/bin" "$BATS_TEST_TMPDIR/tmux"
	export PATH="$BATS_TEST_TMPDIR/bin:$PATH"
	export WAKE_BIN="$BATS_TEST_DIRNAME/../../bin/cockpit-wake"
	unset TMUX TMUX_CONTROL_ROOT
	export COCKPIT_CONTROL_ROOT="$BATS_TEST_TMPDIR/control-root"

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

	cat >"$BATS_TEST_TMPDIR/bin/notify-send" <<'EOF'
#!/usr/bin/env bash
exit 0
EOF
	chmod +x "$BATS_TEST_TMPDIR/bin/notify-send"

	cat >"$BATS_TEST_TMPDIR/bin/tmux" <<EOF
#!/usr/bin/env bash
	if [ "\${1:-}" = "show-environment" ] && [ "\${2:-}" = "COCKPIT_CONTROL_ROOT" ] && [ -n "\${TMUX_CONTROL_ROOT:-}" ]; then
		printf 'COCKPIT_CONTROL_ROOT=%s\n' "\$TMUX_CONTROL_ROOT"
		exit 0
	fi
	printf '%s\n' "\$*" >> "$BATS_TEST_TMPDIR/tmux/calls.log"
	exit 0
EOF
	chmod +x "$BATS_TEST_TMPDIR/bin/tmux"

	"$BATS_TEST_DIRNAME/../../bin/cockpit-control" init >/dev/null
}

schedule_generated_job() {
	local schedule_output
	schedule_output="$("$WAKE_BIN" schedule \
		--once "23:59 2099-01-01" \
		-s cockpit-a \
		-w overseer \
		-m "Generated wake must fail closed" \
		--label "fail-closed")" || {
		printf '%s\n' "$schedule_output" >&2
		return 1
	}
	printf '%s\n' "$schedule_output" |
		sed -nE 's/.*id=(wake-[0-9]+).*/\1/p' |
		head -n1
}

assert_generated_job_fails_without_tmux() {
	local id="$1"
	local expected_error="$2"

	run "$HOME/.config/cockpit-wake/jobs/$id.sh"
	[ "$status" -eq 1 ]
	echo "$output" | grep -Fq "$expected_error"
	[ ! -e "$HOME/.config/cockpit-wake/inbox.md" ]
	[ ! -e "$BATS_TEST_TMPDIR/tmux/calls.log" ]

	run python3 -c '
import json
import sys
from pathlib import Path

state = json.loads(Path(sys.argv[1]).read_text())
wake = next(item for item in state["awakenings"] if item["id"] == sys.argv[2])
assert wake["status"] == "pending"
assert wake["fired_at"] is None
' "$HOME/.config/cockpit-wake/awakenings.json" "$id"
	[ "$status" -eq 0 ]
}

@test "fire pastes the wake message and presses Enter in the target pane" {
	run "$WAKE_BIN" schedule \
		--once "23:59 2099-01-01" \
		-s cockpit-a \
		-w overseer \
		-m "Wake up and run the loop" \
		--label "loop"
	[ "$status" -eq 0 ]
	id="$(printf '%s\n' "$output" | sed -nE 's/.*id=(wake-[0-9]+).*/\1/p' | head -n1)"
	[ -n "$id" ]

	run "$WAKE_BIN" fire "$id"
	[ "$status" -eq 0 ]

	grep -q "load-buffer .*/${id}.msg" "$BATS_TEST_TMPDIR/tmux/calls.log"
	grep -q "paste-buffer -t cockpit-a:overseer" "$BATS_TEST_TMPDIR/tmux/calls.log"
	grep -q "send-keys -t cockpit-a:overseer Enter" "$BATS_TEST_TMPDIR/tmux/calls.log"
	! grep -q 'send-keys -t cockpit-a:overseer "" Enter' "$BATS_TEST_TMPDIR/tmux/calls.log"
}

@test "wake schedule accepts only the active tmux control-root fallback" {
	export TMUX="$BATS_TEST_TMPDIR/tmux-socket,123,0"
	export TMUX_CONTROL_ROOT="$COCKPIT_CONTROL_ROOT"
	unset COCKPIT_CONTROL_ROOT

	run "$WAKE_BIN" schedule \
		--once "23:59 2099-01-01" \
		-s cockpit-a \
		-w overseer \
		-m "Wake from the tmux root"
	[ "$status" -eq 0 ]
	echo "$output" | grep -Fq "Scheduled"
}

@test "wake mutations reject a missing control root before creating or changing wake state" {
	unset COCKPIT_CONTROL_ROOT

	run "$WAKE_BIN" schedule \
		--once "23:59 2099-01-01" \
		-s cockpit-a \
		-w overseer \
		-m "Wake must not schedule"
	[ "$status" -ne 0 ]
	echo "$output" | grep -Fq "COCKPIT_CONTROL_ROOT is required"
	[ ! -e "$HOME/.config/cockpit-wake" ]
	[ ! -e "$BATS_TEST_TMPDIR/tmux/calls.log" ]
}

@test "wake cancel fire clean and inbox mutation fail before changing existing wake state" {
	run "$WAKE_BIN" schedule \
		--once "23:59 2099-01-01" \
		-s cockpit-a \
		-w overseer \
		-m "Wake must remain pending"
	[ "$status" -eq 0 ]
	local id
	id="$(printf '%s\n' "$output" | sed -nE 's/.*id=(wake-[0-9]+).*/\1/p' | head -n1)"
	[ -n "$id" ]
	printf 'unread wake\n' > "$HOME/.config/cockpit-wake/inbox.md"
	local before
	before="$(find "$HOME/.config/cockpit-wake" -type f -exec cksum '{}' ';' | LC_ALL=C sort)"
	unset COCKPIT_CONTROL_ROOT

	run "$WAKE_BIN" cancel "$id"
	[ "$status" -ne 0 ]
	echo "$output" | grep -Fq "COCKPIT_CONTROL_ROOT is required"

	run "$WAKE_BIN" fire "$id"
	[ "$status" -ne 0 ]
	echo "$output" | grep -Fq "COCKPIT_CONTROL_ROOT is required"

	run "$WAKE_BIN" clean
	[ "$status" -ne 0 ]
	echo "$output" | grep -Fq "COCKPIT_CONTROL_ROOT is required"

	run "$WAKE_BIN" inbox-clear
	[ "$status" -ne 0 ]
	echo "$output" | grep -Fq "COCKPIT_CONTROL_ROOT is required"
	[ "$(find "$HOME/.config/cockpit-wake" -type f -exec cksum '{}' ';' | LC_ALL=C sort)" = "$before" ]
	[ ! -e "$BATS_TEST_TMPDIR/tmux/calls.log" ]
}

@test "generated scheduled job rejects a missing control store before tmux mutation" {
	local id
	id="$(schedule_generated_job)"
	[ -n "$id" ]
	rm -rf "$COCKPIT_CONTROL_ROOT"

	assert_generated_job_fails_without_tmux "$id" "missing required COCKPIT_CONTROL_ROOT"
}

@test "generated scheduled job rejects malformed control metadata before tmux mutation" {
	local id
	id="$(schedule_generated_job)"
	[ -n "$id" ]
	python3 -c 'from pathlib import Path; Path(__import__("sys").argv[1]).write_text("{malformed\\n")' \
		"$COCKPIT_CONTROL_ROOT/control.json"

	assert_generated_job_fails_without_tmux "$id" "malformed control.json"
}

@test "generated scheduled job rejects future-versioned control metadata before tmux mutation" {
	local id
	id="$(schedule_generated_job)"
	[ -n "$id" ]
	python3 -c '
import json
import sys
from pathlib import Path

path = Path(sys.argv[1])
metadata = json.loads(path.read_text())
metadata["schema_version"] = 2
path.write_text(json.dumps(metadata) + "\n")
' "$COCKPIT_CONTROL_ROOT/control.json"

	assert_generated_job_fails_without_tmux "$id" "unsupported future schema_version 2"
}

@test "generated scheduled job rejects a relative control root before tmux mutation" {
	local id
	id="$(schedule_generated_job)"
	[ -n "$id" ]
	python3 -c '
import sys
from pathlib import Path

path = Path(sys.argv[1])
lines = path.read_text().splitlines()
lines = [
    "export COCKPIT_CONTROL_ROOT=relative-control-root"
    if line.startswith("export COCKPIT_CONTROL_ROOT=")
    else line
    for line in lines
]
path.write_text("\n".join(lines) + "\n")
' "$HOME/.config/cockpit-wake/jobs/$id.sh"

	assert_generated_job_fails_without_tmux "$id" "must be an absolute path"
}

@test "generated scheduled job stops before tmux mutation when inbox writing fails" {
	local id
	id="$(schedule_generated_job)"
	[ -n "$id" ]
	mkdir "$HOME/.config/cockpit-wake/inbox.md"

	run "$HOME/.config/cockpit-wake/jobs/$id.sh"
	[ "$status" -eq 1 ]
	echo "$output" | grep -Fq "inbox.md"
	[ -d "$HOME/.config/cockpit-wake/inbox.md" ]
	[ ! -e "$BATS_TEST_TMPDIR/tmux/calls.log" ]

	run python3 -c '
import json
import sys
from pathlib import Path

state = json.loads(Path(sys.argv[1]).read_text())
wake = next(item for item in state["awakenings"] if item["id"] == sys.argv[2])
assert wake["status"] == "pending"
assert wake["fired_at"] is None
' "$HOME/.config/cockpit-wake/awakenings.json" "$id"
	[ "$status" -eq 0 ]
}
