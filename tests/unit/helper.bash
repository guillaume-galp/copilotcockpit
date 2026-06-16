#!/usr/bin/env bash
# tests/unit/helper.bash — shared setup for Category-1 bats unit tests (ADR-008).
#
# CRITICAL SAFETY INVARIANT (AC4): every command under test installs into
# $HOME/.copilot/skills and $HOME/.local/bin. These tests MUST therefore run with
# a *fake* HOME pointing inside the per-test BATS_TEST_TMPDIR, so a bug here can
# never clobber the developer's real ~/.copilot/skills or ~/.local/bin. The
# `cc_setup_fake_home` helper below is called from each suite's setup() and is the
# single choke-point that guarantees this isolation.

# Resolve the repo root from this helper's location (tests/unit/ -> repo root).
CC_REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
CC_BOOTSTRAP="$CC_REPO_ROOT/bootstrap.sh"

# cc_setup_fake_home — point HOME at an isolated, writable dir inside the test's
# private tmp dir, and assert the override actually took effect (defence in depth).
cc_setup_fake_home() {
	export HOME="$BATS_TEST_TMPDIR/home"
	mkdir -p "$HOME"
	# Hard guard: refuse to proceed if HOME is anything but the sandbox. This
	# makes an accidental real-HOME mutation impossible even if a future edit
	# forgets the export.
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

# cc_count_backups <dir> — print how many backup files (*.bak-*) exist under <dir>.
cc_count_backups() {
	find "$1" -name '*.bak-*' 2>/dev/null | grep -c . || true
}
