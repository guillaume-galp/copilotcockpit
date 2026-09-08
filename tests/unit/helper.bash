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
CC_UNINSTALL="$CC_REPO_ROOT/uninstall.sh"

# cc_setup_fake_home — point HOME at an isolated, writable dir inside the test's
# private tmp dir, and assert the override actually took effect (defence in depth).
cc_setup_fake_home() {
	export PATH="$CC_REPO_ROOT/tests/transport:$PATH"
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

# Accept the stored controller envelope at an explicit test-clock instant.
cc_accept_dispatch() {
	python3 - "$CC_REPO_ROOT/bin" "$COCKPIT_CONTROL_ROOT" "$@" <<'PY'
import sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
import cockpit_control as cc
root, mission, at, until = Path(sys.argv[2]), *sys.argv[3:]
envelope = next(entry["envelope"] for entry in cc.read_command_slots(root).values()
                if entry["envelope"]["mission_id"] == mission
                and entry["envelope"]["command_type"] == "mission-dispatch")
seconds = (cc._parsed_timestamp(until, "until", "fixture") -
           cc._parsed_timestamp(at, "at", "fixture")).total_seconds()
result = cc.accept_dispatch(
    root, envelope["command_id"], mission, envelope["target"]["id"],
    envelope["queue_item_id"], envelope["trace_id"], envelope["payload_digest"],
    fresh_for=str(seconds), as_of=at,
)
assert result.start_work, result
PY
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

# cc_control_snapshot <root> — print one stable, sorted description of every path
# under <root>: relative name, device, inode, mode, size, nanosecond mtime, and
# the SHA-256 of every regular file. Two snapshots compare equal only when the
# store is byte-identical *and* nothing was created, replaced, or re-timestamped,
# which is how the read-only control preflight contract is proven.
cc_control_snapshot() {
	python3 -c '
import hashlib
import os
import stat
import sys

root = sys.argv[1]
paths = set()
for directory, directories, files in os.walk(root):
    paths.add(directory)
    for name in directories + files:
        paths.add(os.path.join(directory, name))

rows = []
for path in sorted(paths):
    info = os.lstat(path)
    digest = "-"
    if stat.S_ISREG(info.st_mode):
        with open(path, "rb") as handle:
            digest = hashlib.sha256(handle.read()).hexdigest()
    rows.append(
        "%s dev=%d ino=%d mode=%o size=%d mtime=%d sha=%s"
        % (
            os.path.relpath(path, root),
            info.st_dev,
            info.st_ino,
            info.st_mode,
            info.st_size,
            info.st_mtime_ns,
            digest,
        )
    )
print("\n".join(rows))
' "$1"
}

# cc_control_contents <root> — print one stable description of every path under
# <root> that ignores directory timestamps: acquiring and releasing the control
# lock legitimately re-timestamps locks/, so an idempotency proof compares the
# set of paths plus the inode, size, and SHA-256 of every regular file instead.
cc_control_contents() {
	python3 -c '
import hashlib
import os
import stat
import sys

root = sys.argv[1]
paths = set()
for directory, directories, files in os.walk(root):
    paths.add(directory)
    for name in directories + files:
        paths.add(os.path.join(directory, name))

rows = []
for path in sorted(paths):
    info = os.lstat(path)
    if stat.S_ISREG(info.st_mode):
        with open(path, "rb") as handle:
            digest = hashlib.sha256(handle.read()).hexdigest()
        rows.append(
            "%s file ino=%d size=%d sha=%s"
            % (os.path.relpath(path, root), info.st_ino, info.st_size, digest)
        )
    else:
        rows.append(
            "%s other mode=%o" % (os.path.relpath(path, root), info.st_mode)
        )
print("\n".join(rows))
' "$1"
}
