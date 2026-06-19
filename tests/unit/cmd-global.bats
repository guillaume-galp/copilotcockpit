#!/usr/bin/env bats
# tests/unit/cmd-global.bats — Category-1 unit tests for lib/cmd-global.sh.
#
# Proves (AC3), all against a fake HOME (AC4):
#   * idempotency  — a second `global` run reports "already current" and writes
#                    no new backup.
#   * dry-run      — `global --dry-run` touches nothing under $HOME.
#   * error path   — an unknown option exits 2 with a clear message.

load helper

setup() {
	cc_setup_fake_home
}

# --- idempotency (BDD: "cmd-global idempotency is asserted") ------------------
@test "global: first run installs the managed skills + cockpit tools" {
	run "$CC_BOOTSTRAP" global
	[ "$status" -eq 0 ]
	[ -f "$HOME/.copilot/skills/worker-dev/SKILL.md" ]
	[ -f "$HOME/.local/bin/cockpit-wake" ]
	[ -f "$HOME/.local/bin/cockpit-overseer" ]
	[ -f "$HOME/.local/bin/cockpit-trace" ]
}

@test "global: second run reports already-current and writes no backup" {
	run "$CC_BOOTSTRAP" global
	[ "$status" -eq 0 ]

	run "$CC_BOOTSTRAP" global
	[ "$status" -eq 0 ]
	# Every managed artefact (7 harness skills + cockpit tools) is already current.
	echo "$output" | grep -q "already current"
	# Idempotent: no backups were created on the second pass.
	[ "$(cc_count_backups "$HOME")" -eq 0 ]
}

# --- dry-run = no side-effects -----------------------------------------------
@test "global --dry-run writes nothing under HOME" {
	run "$CC_BOOTSTRAP" global --dry-run
	[ "$status" -eq 0 ]
	[ ! -e "$HOME/.copilot" ]
	[ ! -e "$HOME/.local" ]
}

# --- error path: unknown option ----------------------------------------------
@test "global: unknown option exits 2 with a message" {
	run "$CC_BOOTSTRAP" global --bogus
	[ "$status" -eq 2 ]
	echo "$output" | grep -q "unknown option"
}

# --- error path: --from-release without a ref --------------------------------
@test "global: --from-release without a ref exits 2" {
	run "$CC_BOOTSTRAP" global --from-release
	[ "$status" -eq 2 ]
	echo "$output" | grep -q "from-release"
}
