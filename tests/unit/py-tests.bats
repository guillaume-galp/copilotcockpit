#!/usr/bin/env bats

@test "python unit tests via unittest" {
  run python3 -m unittest \
    tests.unit.test_typed_contracts \
    tests.unit.test_control_command_seam \
    tests.unit.test_control_root_schema_seam \
    tests.unit.test_control_lock_seam \
    tests.unit.test_control_journal_seam \
    tests.unit.test_control_projection_seam \
    tests.unit.test_control_lifecycle_seam \
    tests.unit.test_control_mission_control_seam \
    tests.unit.test_control_controller_seam \
    tests.unit.test_control_wake_seam
  [ "$status" -eq 0 ]
}
