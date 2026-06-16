#!/usr/bin/env bats
# tests/unit/cmd-e2e.bats — Category-1 unit tests for lib/cmd-e2e.sh.
#
# Proves (AC3), all against temp dirs + a fake HOME (AC4):
#   * idempotency  — scaffold once, then `--update` twice → identical state, no
#                    new backup (framework files reported "already current").
#   * dry-run      — `e2e <dir> --dry-run` writes no e2e/ tree.
#   * error paths  — missing <dir> arg (exit 2), non-directory target (exit 1),
#                    and refuse-to-clobber WITHOUT --update (exit 1 + --update hint).
#
# CC_SKIP_NPM=1 is exported in setup() so no test ever touches the network/npm.

load helper

setup() {
	cc_setup_fake_home
	# Never run a real `npm install` (offline + deterministic).
	export CC_SKIP_NPM=1
}

# --- happy path scaffold (precondition for the idempotency tests) ------------
@test "e2e: scaffolds an e2e/ tree into a fresh dir" {
	proj="$(cc_make_project_dir)"
	run "$CC_BOOTSTRAP" e2e "$proj" --no-git --yes
	[ "$status" -eq 0 ]
	[ -f "$proj/e2e/playwright.config.ts" ]
	[ -f "$proj/e2e/MANIFEST.toml" ]
}

# --- idempotency: --update twice yields the same state, no new backup --------
@test "e2e --update is idempotent (no changes, no backup on the second run)" {
	proj="$(cc_make_project_dir)"
	run "$CC_BOOTSTRAP" e2e "$proj" --no-git --yes
	[ "$status" -eq 0 ]

	run "$CC_BOOTSTRAP" e2e "$proj" --no-git --yes --update
	[ "$status" -eq 0 ]
	echo "$output" | grep -q "0 refreshed"

	run "$CC_BOOTSTRAP" e2e "$proj" --no-git --yes --update
	[ "$status" -eq 0 ]
	echo "$output" | grep -q "0 refreshed"
	# Idempotent: framework files were identical, so nothing was backed up.
	[ "$(cc_count_backups "$proj/e2e")" -eq 0 ]
}

# --- dry-run = no side-effects -----------------------------------------------
@test "e2e --dry-run writes no e2e/ tree" {
	proj="$(cc_make_project_dir)"
	run "$CC_BOOTSTRAP" e2e "$proj" --no-git --yes --dry-run
	[ "$status" -eq 0 ]
	[ ! -e "$proj/e2e" ]
}

# --- error path: missing <dir> argument --------------------------------------
@test "e2e: missing <dir> arg exits 2 with a message" {
	run "$CC_BOOTSTRAP" e2e
	[ "$status" -eq 2 ]
	echo "$output" | grep -q "missing <dir>"
}

# --- error path: target is not a directory -----------------------------------
@test "e2e: non-directory target exits 1" {
	run "$CC_BOOTSTRAP" e2e "$BATS_TEST_TMPDIR/does-not-exist"
	[ "$status" -eq 1 ]
	echo "$output" | grep -q "not a directory"
}

# --- error path: refuse to clobber WITHOUT --update --------------------------
# (BDD: "cmd-e2e refuses to clobber")
@test "e2e: refusing to clobber exits non-zero and points at --update" {
	proj="$(cc_make_project_dir)"
	run "$CC_BOOTSTRAP" e2e "$proj" --no-git --yes
	[ "$status" -eq 0 ]

	run "$CC_BOOTSTRAP" e2e "$proj" --no-git --yes
	[ "$status" -ne 0 ]
	echo "$output" | grep -q "refusing to clobber"
	echo "$output" | grep -q -- "--update"
}
