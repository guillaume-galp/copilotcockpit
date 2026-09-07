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

	# fake overseer to observe tick invocations from generated jobs
	cat >"$BATS_TEST_TMPDIR/bin/cockpit-overseer" <<'EOF'
#!/usr/bin/env bash
if [ -n "${COCKPIT_OVERSEER_SLEEP_SECONDS:-}" ]; then
	sleep "${COCKPIT_OVERSEER_SLEEP_SECONDS}"
fi
echo "$*" >> "$BATS_TEST_TMPDIR/overseer.log"
exit 0
EOF
	chmod +x "$BATS_TEST_TMPDIR/bin/cockpit-overseer"

	"$BATS_TEST_DIRNAME/../../bin/cockpit-control" init >/dev/null
}

schedule_generated_job() {
	local schedule_output
	schedule_output="$("$WAKE_BIN" schedule \
		--once "23:59 2099-01-01" \
		-s cockpit-a \
		-w overseer \
		-m "Generated wake must fail closed" \
		--label "fail-closed" --mission "M-1" --owner "o1")" || {
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
		--label "loop" \
		--mission "MISSION-1" --owner "overseer" --queue-item "QI-1"
	[ "$status" -eq 0 ]
id="$(printf '%s\n' "$output" | sed -nE 's/.*id=(wake-[0-9]+).*/\1/p' | head -n1)"
	[ -n "$id" ]

	# the generated job script should invoke the lease-wrapped controller tick
	job="$HOME/.config/cockpit-wake/jobs/$id.sh"
	[ -f "$job" ]
	grep -q "_tick-with-lease" "$job"
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

@test "generated scheduled job exports full wake metadata" {
    run "$WAKE_BIN" schedule         --once "23:59 2099-01-01"         -s cockpit-a         -w overseer         -m "Metadata wake"         --label "meta"         --mission "MISSION-XYZ" --owner "owner-id" --queue-item "QI-ABC"         --intent "run-tests" --stop-condition "no_more_work" --blocker-threshold 5 --lifecycle "pending"
    [ "$status" -eq 0 ]
    id="$(printf '%s
' "$output" | sed -nE 's/.*id=(wake-[0-9]+).*/\1/p' | head -n1)"
    [ -n "$id" ]
    job="$HOME/.config/cockpit-wake/jobs/$id.sh"
    # values may be single- or double-quoted depending on platform; match generically
    grep -Eq "export COCKPIT_WAKE_MISSION=.*MISSION-XYZ" "$job"
    grep -Eq "export COCKPIT_WAKE_OWNER.*owner-id" "$job"
    grep -Eq "export COCKPIT_WAKE_QUEUE_ITEM.*QI-ABC" "$job"
    grep -Eq "export COCKPIT_WAKE_INTENT.*run-tests" "$job"
    grep -Eq "export COCKPIT_WAKE_STOP_CONDITION.*no_more_work" "$job"
    grep -Eq "export COCKPIT_WAKE_BLOCKER_THRESHOLD.*5" "$job"
    grep -Eq "export COCKPIT_WAKE_LIFECYCLE_STATE.*pending" "$job"
}

@test "controller tick runs for active VP3 wake" {
    run "$WAKE_BIN" schedule         --once "23:59 2099-01-01"         -s cockpit-a         -w overseer         -m "Active wake"         --label "active"         --mission "M-1" --owner "o1" --queue-item "qi1"
    [ "$status" -eq 0 ]
    id="$(printf '%s
' "$output" | sed -nE 's/.*id=(wake-[0-9]+).*/\1/p' | head -n1)"
    [ -n "$id" ]
    # run the job script — it should invoke the overseer tick
    run "$HOME/.config/cockpit-wake/jobs/$id.sh"
    [ "$status" -eq 0 ]
    grep -q "tick -s" "$BATS_TEST_TMPDIR/overseer.log"
    [ ! -e "$COCKPIT_CONTROL_ROOT/wake-leases/mission-tick-lease.json" ]
    run python3 -c '
import sys
from pathlib import Path

released = Path(sys.argv[1]) / "wake-leases" / "released"
assert released.is_dir()
assert any(released.iterdir())
' "$COCKPIT_CONTROL_ROOT"
    [ "$status" -eq 0 ]
}

@test "overlapping wake losing the lease records wake-duplicate-skipped and exits without dispatch" {
    run "$WAKE_BIN" schedule --once "23:59 2099-01-01" -s cockpit-a -w overseer -m "Overlapping wake" --label "overlap" --mission "M-2" --owner "o2" --queue-item "qi2"
    [ "$status" -eq 0 ]
    id="$(printf '%s\n' "$output" | sed -nE 's/.*id=(wake-[0-9]+).*/\1/p' | head -n1)"
    [ -n "$id" ]
    job="$HOME/.config/cockpit-wake/jobs/$id.sh"
    export COCKPIT_OVERSEER_SLEEP_SECONDS=1
    "$job" >/dev/null 2>&1 &
    first_pid="$!"

    lease_path="$COCKPIT_CONTROL_ROOT/wake-leases/mission-tick-lease.json"
    for _ in 1 2 3 4 5 6 7 8 9 10 11 12 13 14 15 16 17 18 19 20; do
        [ -e "$lease_path" ] && break
        sleep 0.05
    done
    [ -e "$lease_path" ]

    run "$job"
    [ "$status" -eq 0 ]
    echo "$output" | grep -Fq "duplicate lease holder"

    wait "$first_pid"
    unset COCKPIT_OVERSEER_SLEEP_SECONDS

    run python3 -c '
import json
import sys
from pathlib import Path

events = Path(sys.argv[1]) / "events"
records = [json.loads(path.read_text()) for path in sorted(events.glob("*.json"))]
duplicate = [r for r in records if r["event_type"] == "wake-duplicate-skipped"]
assert len(duplicate) == 1, len(duplicate)
' "$COCKPIT_CONTROL_ROOT"
    [ "$status" -eq 0 ]

    run python3 -c '
import sys
from pathlib import Path

lines = Path(sys.argv[1]).read_text().splitlines()
assert len(lines) == 1, len(lines)
assert lines[0].startswith("tick ")
' "$BATS_TEST_TMPDIR/overseer.log"
    [ "$status" -eq 0 ]
}

@test "expired wake lease is reconciled durably before the next tick acquires a new lease" {
    run "$WAKE_BIN" schedule --once "23:59 2099-01-01" -s cockpit-a -w overseer -m "Stale lease wake" --label "stale" --mission "M-3" --owner "o3" --queue-item "qi3"
    [ "$status" -eq 0 ]
    id="$(printf '%s\n' "$output" | sed -nE 's/.*id=(wake-[0-9]+).*/\1/p' | head -n1)"
    [ -n "$id" ]
    job="$HOME/.config/cockpit-wake/jobs/$id.sh"

    run python3 -c '
import json
import sys
from pathlib import Path

lease_dir = Path(sys.argv[1]) / "wake-leases"
lease_dir.mkdir(parents=True, exist_ok=True)
record = {
    "schema_version": 1,
    "lease_id": "dead-process-lease",
    "wake_id": "abandoned",
    "pid": 999999,
    "session": "cockpit-a",
    "window": "overseer",
    "acquired_at": "2000-01-01T00:00:00Z",
    "expires_at": "2000-01-01T00:01:00Z",
}
(lease_dir / "mission-tick-lease.json").write_text(json.dumps(record) + "\n")
' "$COCKPIT_CONTROL_ROOT"
    [ "$status" -eq 0 ]

    run "$job"
    [ "$status" -eq 0 ]
    grep -q "tick -s" "$BATS_TEST_TMPDIR/overseer.log"
    [ ! -e "$COCKPIT_CONTROL_ROOT/wake-leases/mission-tick-lease.json" ]

    run python3 -c '
import json
import sys
from pathlib import Path

root = Path(sys.argv[1])
events = root / "events"
records = [json.loads(path.read_text()) for path in sorted(events.glob("*.json"))]
recovered = [r for r in records if r["event_type"] == "wake-lease-recovered"]
assert len(recovered) == 1, len(recovered)
assert recovered[0]["payload"]["reason"] == "expired"
recovered_dir = root / "wake-leases" / "recovered"
assert recovered_dir.is_dir()
assert any(recovered_dir.iterdir())
' "$COCKPIT_CONTROL_ROOT"
    [ "$status" -eq 0 ]
}

@test "generated scheduled job blocks legacy wake missing owner or mission" {
    run "$WAKE_BIN" schedule         --once "23:59 2099-01-01"         -s cockpit-a         -w overseer         -m "Legacy wake"         --label "legacy"
    [ "$status" -eq 0 ]
    id="$(printf '%s
' "$output" | sed -nE 's/.*id=(wake-[0-9]+).*/\1/p' | head -n1)"
    [ -n "$id" ]

    run "$HOME/.config/cockpit-wake/jobs/$id.sh"
    [ "$status" -eq 1 ]
    echo "$output" | grep -Fq "blocked: legacy wake missing owner or mission"
    [ ! -e "$BATS_TEST_TMPDIR/overseer.log" ]
    [ ! -e "$HOME/.config/cockpit-wake/inbox.md" ]

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

@test "generated scheduled job safely quotes malicious metadata to prevent shell injection" {
    marker="$BATS_TEST_TMPDIR/inject.marker"
    # malicious payload that would create a file if executed unsafely
    malicious_owner=$(printf 'attacker$(printf "INJECTED" > %s)' "$marker")

    run "$WAKE_BIN" schedule         --once "23:59 2099-01-01"         -s cockpit-a -w overseer -m "malicious"         --label "mal" --mission "MAL" --owner "$malicious_owner"
    [ "$status" -eq 0 ]
    id="$(printf '%s\n' "$output" | sed -nE 's/.*id=(wake-[0-9]+).*/\1/p' | head -n1)"
    [ -n "$id" ]
    job="$HOME/.config/cockpit-wake/jobs/$id.sh"
    [ -f "$job" ]

    # Ensure the malicious payload did NOT execute when running the generated job
    [ ! -e "$marker" ]
    run "$job"
    [ "$status" -eq 0 ]
    [ ! -e "$marker" ]
}


@test "generated scheduled job safely quotes malicious label to prevent shell injection" {
    marker="$BATS_TEST_TMPDIR/inject-label.marker"
    # malicious label that would create a file if executed unsafely
    malicious_label=$(printf 'label$(printf "INJECTED-LABEL" > %s)' "$marker")

    run "$WAKE_BIN" schedule         --once "23:59 2099-01-01"         -s cockpit-a -w overseer -m "malicious-label"         --label "$malicious_label" --mission "MAL" --owner "owner1"
    [ "$status" -eq 0 ]
    id="$(printf '%s\n' "$output" | sed -nE 's/.*id=(wake-[0-9]+).*/\1/p' | head -n1)"
    [ -n "$id" ]
    job="$HOME/.config/cockpit-wake/jobs/$id.sh"
    [ -f "$job" ]

    # Ensure the malicious label did NOT execute when running the generated job
    [ ! -e "$marker" ]
    run "$job"
    [ "$status" -eq 0 ]
    [ ! -e "$marker" ]
}
