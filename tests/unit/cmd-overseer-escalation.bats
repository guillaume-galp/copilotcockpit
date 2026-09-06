#!/usr/bin/env bats
# tests/unit/cmd-overseer-escalation.bats — TH3.E3.US3 minimal escalation bookkeeping

load helper

setup() {
    cc_setup_fake_home
    export PATH="$BATS_TEST_TMPDIR/bin:$PATH"
    mkdir -p "$BATS_TEST_TMPDIR/bin"
    export OVERSEER_BIN="$BATS_TEST_DIRNAME/../../bin/cockpit-overseer"
    export CONTROL_BIN="$BATS_TEST_DIRNAME/../../bin/cockpit-control"
    export QUEUE_BIN="$BATS_TEST_DIRNAME/../../bin/cockpit-queue"
    unset COCKPIT_CONTROL_ROOT COCKPIT_QUEUE_ROOT TMUX TMUX_SESSION COCKPIT_SESSION_ID COCKPIT_ID
    cc_setup_tmux_stub
}

@test "AC2 repeated blocked ticks create and advance escalation records" {
    cc_cockpit esc
    # Two active items make the queue ambiguous and block dispatch.
    item1="$(cc_active_item implementing)"
    item2="$(cc_active_item implementing)"

    # First tick: observation persisted and escalation record created.
    cc_tick --as-of 2026-09-04T10:00:00.000000Z
    [ "$status" -eq 0 ]
    echo "$output" | grep -Fq "reason queue-item-ambiguous"
    # One escalation file should exist and have status 'raised'.
    files=("$COCKPIT_CONTROL_ROOT/escalations"/*.json)
    [ -f "${files[0]}" ]
    python3 - <<'PY'
import json,sys
f=sys.argv[1]
doc=json.load(open(f))
print(doc.get('status'))
PY "${files[0]}" | grep -Fq raised

    # Second tick: escalation advances to 'escalated'.
    cc_tick --as-of 2026-09-04T10:01:00.000000Z
    [ "$status" -eq 0 ]
    python3 - <<'PY'
import json,sys
f=sys.argv[1]
doc=json.load(open(f))
print(doc.get('status'))
print(len(doc.get('attempted_recovery',[])))
PY "${files[0]}" | grep -E "escalated|1"

    # Third tick: escalation requests human decision.
    cc_tick --as-of 2026-09-04T10:02:00.000000Z
    [ "$status" -eq 0 ]
    python3 - <<'PY'
import json,sys
f=sys.argv[1]
doc=json.load(open(f))
print(doc.get('status'))
print(doc.get('count'))
PY "${files[0]}" | grep -E "decision-requested|3"
}
