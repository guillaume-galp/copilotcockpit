#!/usr/bin/env bats
# tests/unit/cmd-doctor.bats — Category-1 unit tests for lib/cmd-doctor.sh.
#
# Proves (AC3), all against a fake HOME (AC4):
#   * idempotency  — doctor is read-only: two consecutive runs produce identical
#                    output and write nothing to $HOME.
#   * no side-effects — doctor (which has no --dry-run because it never mutates)
#                    leaves $HOME untouched.
#   * error path   — when a HARD prerequisite is missing, doctor exits non-zero
#                    and prints "MISSING  (required)".

load helper

setup() {
	cc_setup_fake_home
}

# --- happy path: all hard prereqs present here -> exit 0 ----------------------
@test "doctor: exits 0 when hard prerequisites are present" {
	run "$CC_BOOTSTRAP" doctor
	[ "$status" -eq 0 ]
	echo "$output" | grep -q "Prerequisites"
}

# --- idempotency: read-only, two runs identical, no HOME mutation ------------
@test "doctor is idempotent and writes nothing to HOME" {
	run "$CC_BOOTSTRAP" doctor
	[ "$status" -eq 0 ]
	first="$output"

	run "$CC_BOOTSTRAP" doctor
	[ "$status" -eq 0 ]
	# Output is stable across runs (the report path is the only volatile bit and
	# it is constant here). Compare the Prerequisites + Result verdicts.
	[ "$output" = "$first" ]
	echo "$output" | grep -q "cockpit-overseer"
	echo "$output" | grep -q "cockpit-trace"

	# doctor must never create anything under HOME.
	[ ! -e "$HOME/.copilot" ]
	[ ! -e "$HOME/.local" ]
}

# --- error path: a missing HARD prerequisite -> non-zero + message -----------
# We build a deterministic minimal PATH (a sandbox of symlinks to the real tools
# the script needs) that deliberately OMITS `node`, so doctor reports node as a
# missing hard prerequisite regardless of where node lives on this machine.
@test "doctor: missing hard prerequisite exits non-zero with MISSING message" {
	if ! command -v python3 >/dev/null 2>&1; then
		skip "python3 not available to build the sandbox PATH"
	fi
	minbin="$BATS_TEST_TMPDIR/minbin"
	mkdir -p "$minbin"
	# Resolve the REAL python3 binary (avoid wrapper shims that need their parent
	# dir on PATH) so the sandbox python3 works standalone.
	realpy="$(python3 -c 'import sys; print(sys.executable)')"
	ln -s "$realpy" "$minbin/python3"
	# Symlink the coreutils + bash/git the doctor needs — but NOT node/npm.
	for t in bash sh git head awk cat uname grep sed cmp dirname basename env \
		mktemp cp mv rm mkdir ls find chmod tr sort wc; do
		p="$(command -v "$t" 2>/dev/null)" && ln -s "$p" "$minbin/$t" 2>/dev/null || true
	done

	# Run with the restricted PATH via `env` so bats' own PATH stays intact.
	run env PATH="$minbin" "$CC_BOOTSTRAP" doctor
	[ "$status" -ne 0 ]
	echo "$output" | grep -q "MISSING  (required)"
	echo "$output" | grep -q "FAIL"
}
