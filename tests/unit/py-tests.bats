#!/usr/bin/env bats

load helper

setup() {
  cc_setup_fake_home
}

@test "python unit tests via unittest" {
  cd "$CC_REPO_ROOT"
  run python3 -m unittest discover -s tests/unit -p 'test_*.py'
  printf '%s\n' "$output"
  [ "$status" -eq 0 ]
}
