#!/usr/bin/env bats

@test "python unit tests via unittest" {
  run python3 -m unittest tests.unit.test_typed_contracts
  [ "$status" -eq 0 ]
}
