#!/usr/bin/env bats

load ../unit/helper

setup() {
    cc_setup_fake_home
}

@test "BUG-001 public CLI durable FIFO, cooperative lifecycle, persisted wakes and legacy diagnosis" {
    cd "$CC_REPO_ROOT"
    run python3 -m unittest -v tests.integration.test_bug001_public_cli
    printf '%s\n' "$output"
    [ "$status" -eq 0 ]
}
