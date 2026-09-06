#!/usr/bin/env bats
# tests/unit/cmd-evidence-boundary.bats — TH3.E4.US3 evidence boundary blocking

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

# cc_setup_tmux_stub — deterministic pane capture stub used by overseer tests.
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

cc_cockpit() {
    export COCKPIT_CONTROL_ROOT="$BATS_TEST_TMPDIR/$1-control"
    export COCKPIT_QUEUE_ROOT="$BATS_TEST_TMPDIR/$1-queue"
    mkdir -p "$COCKPIT_QUEUE_ROOT"
    "$CONTROL_BIN" init >/dev/null
    cc_declare_queue_root "$COCKPIT_CONTROL_ROOT" "$COCKPIT_QUEUE_ROOT"
    run "$CONTROL_BIN" replay-ledger
    [ "$status" -eq 0 ]
}

cc_active_item() {
    local item
    item="$("$QUEUE_BIN" enqueue --text "/the-copilot-build-method deliver a change" --title change)"
    "$QUEUE_BIN" start-next >/dev/null
    "$QUEUE_BIN" transition "$item" "$1" --reason "ready for $1" >/dev/null
    printf '%s' "$item"
}

cc_tick() {
    run "$OVERSEER_BIN" tick "$@"
}

@test "AC3 undeclared deployment image need blocks dispatch and emits architecture-boundary escalation" {
    cc_cockpit happy
    local item
    item="$(cc_active_item implementing)"
    mission_id="$(python3 - <<'PY'
import sys, uuid
print(uuid.uuid4())
PY
)"
    trace_id="$(python3 - <<'PY'
import sys, uuid
print(uuid.uuid4())
PY
)"

    # Establish a materialized mission and then report an undeclared image need.
    run "$CONTROL_BIN" record-lifecycle --state accepted --worker worker-dev \
    	--mission "$mission_id" --queue-item "$item" --sequence 1 \
    	--trace "$trace_id" --fresh-for 3600
    [ "$status" -eq 0 ]
    run "$CONTROL_BIN" record-lifecycle --state running --worker worker-dev \
    	--mission "$mission_id" --queue-item "$item" --sequence 2 \
    	--trace "$trace_id" --fresh-for 3600
    [ "$status" -eq 0 ]
    run "$CONTROL_BIN" record-lifecycle --state blocked --worker worker-dev \
    	--mission "$mission_id" --queue-item "$item" --sequence 3 \
    	--trace "$trace_id" --fresh-for 3600 --reason "needs unapproved image" \
    	--blocker-category image --blocker-detail "image:unapproved"
    [ "$status" -eq 0 ]

    cc_tick --window worker-dev --as-of 2026-09-04T10:00:00.000000Z
    # blocked ticks exit non-zero
    [ "$status" -ne 0 ]
    echo "$output" | grep -Fq "dispatch is blocked (queue-root-undeclared)"
    echo "$output" | grep -Fq "tick recorded action record-observation outcome blocked reason queue-root-undeclared"

    # The architecture-boundary observation is committed in the authoritative
    # journal, not as a sidecar-only artifact.
    run "$CONTROL_BIN" list-events
    [ "$status" -eq 0 ]
    echo "$output" | grep -q "type controller-observation-blocked actor cockpit-overseer$"

    run python3 -c '
import json
import sys
from pathlib import Path

sys.path.insert(0, sys.argv[2])
import cockpit_control

root = Path(sys.argv[1])
control_id = json.loads((root / cockpit_control.CONTROL_METADATA_NAME).read_text())["control_id"]
history = cockpit_control.read_committed_events(root, control_id)
obs = None
for event in history.events:
    candidate = cockpit_control.event_controller_observation(
        event.record, f"events/{event.path.name}"
    )
    if candidate is not None:
        obs = candidate
assert obs is not None
assert obs["outcome"] == "blocked", obs
assert obs["reason"] == "queue-root-undeclared", obs
assert obs["mission_id"] == sys.argv[3], obs
assert obs["queue_item_id"] == sys.argv[4], obs
refs = set(obs["evidence_refs"])
assert f"mission:{sys.argv[3]}" in refs, refs
assert f"queue:{sys.argv[4]}" in refs, refs
assert f"trace:{sys.argv[5]}" in refs, refs
assert "boundary:image" in refs, refs
print("journal-backed architecture boundary observation committed with causal refs")
' "$COCKPIT_CONTROL_ROOT" "$MODULE_DIR" "$mission_id" "$item" "$trace_id"
    [ "$status" -eq 0 ]
    echo "$output" | grep -Fq "journal-backed architecture boundary observation committed with causal refs"
}
