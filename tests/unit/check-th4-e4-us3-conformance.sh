#!/usr/bin/env bash
# Focused conformance gate for TH4.E4.US3.
#
# Runs the final modularity and behavior-preservation checks required by
# ADR-020/TH4 boundaries before the full repository gate.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd -P)"

echo "== TH4.E4.US3 focused conformance =="
echo "-- python conformance + seam tests"
python3 -m unittest \
	tests.unit.test_typed_contracts \
	tests.unit.test_control_root_schema_seam \
	tests.unit.test_control_lock_seam \
	tests.unit.test_control_journal_seam \
	tests.unit.test_control_projection_seam \
	tests.unit.test_control_lifecycle_seam \
	tests.unit.test_control_command_seam \
	tests.unit.test_control_mission_control_seam \
	tests.unit.test_control_controller_seam \
	tests.unit.test_control_wake_seam \
	tests.unit.test_control_cli_adapter_seam \
	tests.unit.test_th4_e4_us3_conformance

if command -v bats >/dev/null 2>&1; then
	echo "-- lock/journal/projection/controller/wake + resilience invariants"
	bats \
		"$ROOT/tests/unit/cmd-control-interruption.bats" \
		"$ROOT/tests/unit/cmd-overseer-recovery.bats" \
		"$ROOT/tests/unit/cmd-overseer-tick.bats" \
		"$ROOT/tests/integration/resilience.bats"
else
	echo "-- bats not available; skipping bats-focused conformance block"
fi

echo "TH4.E4.US3 focused conformance: PASS"
