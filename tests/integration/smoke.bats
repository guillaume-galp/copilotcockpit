#!/usr/bin/env bats
# tests/integration/smoke.bats — Category 4: dry-run integration smoke (ADR-008).
#
# Drives the real bootstrap.sh sub-commands in --dry-run / read-only mode under a
# FAKE HOME so nothing touches the developer's real ~/.copilot/skills or
# ~/.local/bin (AC3 safety invariant — enforced by cc_setup_fake_home).
#
# Proves (AC3):
#   * `global --dry-run` enumerates all 8 managed skills + cockpit-wake;
#   * `e2e <tmp> --dry-run` prints the expected scaffold file list;
#   * `doctor` exits 0 and reports each prerequisite found/missing.

load helper

setup() {
	cc_setup_fake_home
}

# --- global --dry-run lists the 8 managed skills + cockpit-wake --------------
# Reality since TH1-E5-US3: copilotcockpit-dev's source is now vendored, so the
# dry-run enumerates ALL 8 names as "would copy ..." lines (no pending warning),
# plus cockpit-wake. We assert exactly that REAL output.
@test "global --dry-run lists all 8 managed skills + cockpit-wake" {
	run "$CC_BOOTSTRAP" global --dry-run
	[ "$status" -eq 0 ]

	# The 7 harness skills are each named on a "would copy" line.
	echo "$output" | grep -q "e2e-cockpit"
	echo "$output" | grep -q "e2e-operator"
	echo "$output" | grep -q "setup-e2e-cockpit"
	echo "$output" | grep -q "setup-e2e-runbook"
	echo "$output" | grep -q "worker-dev"
	echo "$output" | grep -q "worker-fix"
	echo "$output" | grep -q "worker-test"

	# The 8th skill is now vendored: it appears AND is no longer flagged pending.
	echo "$output" | grep -q "copilotcockpit-dev"
	! echo "$output" | grep -q "not yet vendored, skipping: copilotcockpit-dev"

	# cockpit-wake is part of the same managed install pass.
	echo "$output" | grep -q "cockpit-wake"

	# Dry-run is side-effect-free: nothing written under the fake HOME.
	[ ! -e "$HOME/.copilot" ]
	[ ! -e "$HOME/.local/bin/cockpit-wake" ]
}

# --- e2e <tmp> --dry-run prints the expected scaffold file list --------------
@test "e2e <tmp> --dry-run prints the scaffold file list and writes nothing" {
	local proj
	proj="$(cc_make_project_dir)"

	CC_SKIP_NPM=1 run "$CC_BOOTSTRAP" e2e "$proj" --dry-run --yes
	[ "$status" -eq 0 ]

	echo "$output" | grep -q "e2e dry-run — would scaffold into:"
	# Representative entries across every class (seed/framework/project).
	echo "$output" | grep -q "e2e/package.json"
	echo "$output" | grep -q "e2e/run-audit.sh"
	echo "$output" | grep -q "e2e/run-playwright.sh"
	echo "$output" | grep -q "e2e/playwright.config.ts"
	echo "$output" | grep -q "e2e/tests/smoke.spec.ts"
	echo "$output" | grep -q "e2e/.github/copilot-instructions.md"
	echo "$output" | grep -q "e2e/MANIFEST.toml"
	# Resolved tokens are echoed.
	echo "$output" | grep -q "@@APP_NAME@@"
	echo "$output" | grep -q "nothing written"

	# Dry-run wrote nothing: no e2e/ subtree created.
	[ ! -e "$proj/e2e" ]
}

# --- doctor exits 0 and reports each prerequisite found/missing --------------
@test "doctor exits 0 and reports each prerequisite found/missing" {
	run "$CC_BOOTSTRAP" doctor
	[ "$status" -eq 0 ]

	echo "$output" | grep -q "== Prerequisites =="
	# Every HARD prerequisite is reported by name with a found/missing verdict.
	echo "$output" | grep -Eq "bash[[:space:]]+(found|missing)"
	echo "$output" | grep -Eq "git[[:space:]]+(found|missing)"
	echo "$output" | grep -Eq "node[[:space:]]+(found|missing)"
	echo "$output" | grep -Eq "python3[[:space:]]+(found|missing)"
	# Result section is present.
	echo "$output" | grep -q "== Result =="
}
