#!/usr/bin/env bash
# tests/integration/helper.bash — shared setup for Category-4 integration smoke.
#
# SAFETY INVARIANT (AC3): the bootstrap commands install into $HOME/.copilot/skills
# and $HOME/.local/bin. The smoke suite MUST therefore run with a *fake* HOME
# inside BATS_TEST_TMPDIR so a bug can never clobber the developer's real
# ~/.copilot/skills or ~/.local/bin. This mirrors tests/unit/helper.bash.

# Repo root from this helper (tests/integration/ -> repo root).
CC_REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
CC_BOOTSTRAP="$CC_REPO_ROOT/bootstrap.sh"

# cc_setup_fake_home — point HOME at an isolated, writable dir inside the test's
# private tmp dir, and HARD-GUARD that the override actually took effect.
cc_setup_fake_home() {
	export HOME="$BATS_TEST_TMPDIR/home"
	mkdir -p "$HOME"
	case "$HOME" in
	"$BATS_TEST_TMPDIR"/*) : ;;
	*)
		printf 'FATAL: fake HOME not isolated: %s\n' "$HOME" >&2
		return 1
		;;
	esac
}

# cc_make_project_dir — create and echo a fresh, empty target dir for `e2e`.
cc_make_project_dir() {
	local d="$BATS_TEST_TMPDIR/proj-$$-$RANDOM"
	mkdir -p "$d"
	printf '%s' "$d"
}
