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

@test "AC3 undeclared deployment image need blocks dispatch and emits architecture-boundary escalation" {
    cc_cockpit happy
    local item
    item="$(cc_active_item implementing)"

    # Create a worker lifecycle that records a blocked need for an image the
    # mission did not declare: this should make the controller block and
    # emit an architecture-boundary escalation file.
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

    run "$CONTROL_BIN" record-lifecycle --state blocked --worker worker-dev \
        --mission "$mission_id" --queue-item "$item" --sequence 1 \
        --trace "$trace_id" --blocker-category image --blocker-detail "image:unapproved"
    [ "$status" -eq 0 ]

    cc_tick --window worker-dev --as-of 2026-09-04T10:00:00.000000Z
    # blocked ticks exit non-zero
    [ "$status" -ne 0 ]
    echo "$output" | grep -Fq "dispatch is blocked (root-undeclared)"

    # An architecture-boundary escalation file must be present describing the
    # undeclared blocker.
    matches=$(ls "$COCKPIT_CONTROL_ROOT/esc"/*.json 2>/dev/null || true)
    [ -n "$matches" ]
    grep -q '"record_type": "architecture-boundary"' "$COCKPIT_CONTROL_ROOT/esc"/*.json
}
