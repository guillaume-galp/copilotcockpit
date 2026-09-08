#!/usr/bin/env bats
# Real CLI regressions with per-test Python scheduler fakes and safe tmux transport.
load helper

setup() {
    cc_setup_fake_home
    export XDG_CONFIG_HOME="$HOME/.config"
    export XDG_CACHE_HOME="$HOME/.cache"
}

@test "wake BUG-001: stored identity, real tick, scheduler safety, cancellation and dry-run" {
    run env PYTHONPATH="$CC_REPO_ROOT/tests/unit" \
        python3 -m unittest -v test_wake_runtime.WakeRuntimeTests
    printf '%s\n' "$output"
    [ "$status" -eq 0 ]
}

@test "wake typed seam and existing lease ownership primitives" {
    run env PYTHONPATH="$CC_REPO_ROOT/tests/unit" \
        python3 -m unittest -v test_control_wake_seam
    printf '%s\n' "$output"
    [ "$status" -eq 0 ]
}
