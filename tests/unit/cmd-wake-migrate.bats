#!/usr/bin/env bats
load helper

setup() {
    cc_setup_fake_home
    export XDG_CONFIG_HOME="$HOME/.config"
    export XDG_CACHE_HOME="$HOME/.cache"
}

@test "wake ADR-017 migration: explicit read-only diagnosis preserves legacy state and authority" {
    run env PYTHONPATH="$CC_REPO_ROOT/tests/unit" \
        python3 -m unittest -v test_wake_runtime.WakeMigrationTests
    printf '%s\n' "$output"
    [ "$status" -eq 0 ]
}
