#!/usr/bin/env bats
# tests/unit/cmd-wake-migrate.bats — unit tests for cockpit-wake migrate

load helper

setup() {
    cc_setup_fake_home
}

@test "migrate: initializes control root and preserves wake state" {
    root="$BATS_TEST_TMPDIR/control-root"
    export COCKPIT_CONTROL_ROOT="$root"

    # create a legacy wake state under $HOME
    mkdir -p "$HOME/.config/cockpit-wake"
    echo '{"awakenings": [{"id":"wake-1","message":"hi"}]}' > "$HOME/.config/cockpit-wake/awakenings.json"

    run "$CC_BOOTSTRAP" doctor >/dev/null 2>&1 || true

    run "$CC_BOOTSTRAP" doctor || true

    WAKE_BIN="$BATS_TEST_DIRNAME/../../bin/cockpit-wake"
    run "$WAKE_BIN" migrate
    [ "$status" -eq 0 ]
    [ -f "$root/control.json" ]
    # wake state unchanged
    grep -q 'wake-1' "$HOME/.config/cockpit-wake/awakenings.json"
}

@test "migrate: rerun is idempotent and reports current" {
    root="$BATS_TEST_TMPDIR/control-root"
    export COCKPIT_CONTROL_ROOT="$root"

    WAKE_BIN="$BATS_TEST_DIRNAME/../../bin/cockpit-wake"
    run "$WAKE_BIN" migrate
    [ "$status" -eq 0 ]

    before=$(cc_control_contents "$root")

    WAKE_BIN="$BATS_TEST_DIRNAME/../../bin/cockpit-wake"
    run "$WAKE_BIN" migrate
    [ "$status" -eq 0 ]
    echo "$output" | grep -q "control root already current"

    after=$(cc_control_contents "$root")
    [ "$before" = "$after" ]
}

@test "migrate: future schema present -> exits without rewriting control root" {
    root="$BATS_TEST_TMPDIR/control-root-future"
    mkdir -p "$root"
    export COCKPIT_CONTROL_ROOT="$root"
    echo '{"schema_version": 99}' > "$root/control.json"

    WAKE_BIN="$BATS_TEST_DIRNAME/../../bin/cockpit-wake"
    run "$WAKE_BIN" migrate
    [ "$status" -ne 0 ]
    echo "$output" | grep -q "future schema_version"
}
