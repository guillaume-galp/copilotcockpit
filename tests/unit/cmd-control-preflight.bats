#!/usr/bin/env bats
# tests/unit/cmd-control-preflight.bats — TH3.E1.US7 control-plane preflight and
# guarded repair diagnostics.
#
# The contract under test has three halves:
#
#   * preflight reports every control-plane dimension and is *strictly*
#     read-only, which is proven by comparing a byte, inode, mode, and
#     nanosecond-mtime snapshot of the whole store before and after each run;
#   * the read-only diagnosis separates authoritative corruption from derived
#     corruption and from safe non-authoritative debris, and names the exact
#     explicit command an operator runs for each finding;
#   * the explicit guarded repair only ever renames debris into retained
#     evidence, is idempotent, and refuses ambiguous or future-versioned state
#     without claiming anything.
#
# Concurrency and crash coverage is deterministic: every coordinated process
# parks at a named fault-hook boundary and is killed there; nothing waits on a
# timing guess.

load helper

setup() {
	cc_setup_fake_home
	export CONTROL_BIN="$BATS_TEST_DIRNAME/../../bin/cockpit-control"
	export MODULE_DIR="$BATS_TEST_DIRNAME/../../bin"
	unset COCKPIT_CONTROL_ROOT COCKPIT_QUEUE_ROOT TMUX TMUX_SESSION COCKPIT_SESSION_ID COCKPIT_ID
}

# cc_fresh_store <root> [committed-event-count] — initialize one store and commit
# the requested number of real events through the normal publication protocol.
cc_fresh_store() {
	local root="$1" count="${2:-0}" index=0
	export COCKPIT_CONTROL_ROOT="$root"
	"$CONTROL_BIN" init >/dev/null
	while [ "$index" -lt "$count" ]; do
		"$CONTROL_BIN" publish-event --type control-preflight-fixture >/dev/null
		index=$((index + 1))
	done
}

# cc_assert_preflight_readonly <root> <label> — run preflight and prove it changed
# nothing at all, including that it never created the transition guard.
cc_assert_preflight_readonly() {
	local root="$1" label="$2" before after guard_existed=0
	[ -e "$root/locks/control.guard" ] && guard_existed=1
	before="$(cc_control_snapshot "$root")"
	COCKPIT_CONTROL_ROOT="$root" "$CONTROL_BIN" preflight >/dev/null 2>&1 || true
	after="$(cc_control_snapshot "$root")"
	if [ "$before" != "$after" ]; then
		printf 'preflight mutated the store for case: %s\n' "$label" >&2
		diff <(printf '%s\n' "$before") <(printf '%s\n' "$after") >&2 || true
		return 1
	fi
	if [ "$guard_existed" -eq 0 ] && [ -e "$root/locks/control.guard" ]; then
		printf 'preflight created locks/control.guard for case: %s\n' "$label" >&2
		return 1
	fi
	return 0
}

@test "preflight reports every control-plane dimension ready without changing one byte" {
	local root="$BATS_TEST_TMPDIR/preflight-ready"
	cc_fresh_store "$root" 2

	local before
	before="$(cc_control_snapshot "$root")"

	run "$CONTROL_BIN" preflight
	[ "$status" -eq 0 ]
	echo "$output" | grep -Fq "preflight ready in $root (source: shell); 11 dimensions checked, 0 finding(s)"
	[ "$(echo "$output" | grep -c ' ready ok: ')" -eq 11 ]
	echo "$output" | grep -Fq "committed-revisions ready ok: 2 contiguous committed revision(s)"
	echo "$output" | grep -Fq "ledger ready ok: ledger.json and events.jsonl equal the committed rebuild at revision 2"
	! echo "$output" | grep -q '\[repair: '

	[ "$(cc_control_snapshot "$root")" = "$before" ]
	[ ! -e "$root/quarantine" ]
	[ ! -e "$root/ledger.json.tmp" ]
	[ ! -e "$root/events.jsonl.tmp" ]
	[ ! -e "$root/locks/control.lock" ]
}

@test "preflight names every readiness dimension the control plane declares" {
	local root="$BATS_TEST_TMPDIR/preflight-dimensions"
	cc_fresh_store "$root" 1

	run "$CONTROL_BIN" preflight
	[ "$status" -eq 0 ]

	local dimension
	for dimension in root schema transition-guard authoritative-lock quarantines \
		pending-events committed-revisions ledger queue worker-capability declared-paths; do
		if ! echo "$output" | grep -q "^$dimension "; then
			printf 'preflight never reported dimension: %s\n' "$dimension" >&2
			return 1
		fi
	done

	# The transition guard is the one dimension that cannot be fully answered
	# read-only, and it says so instead of taking the advisory lock.
	echo "$output" | grep -Fq "its current holder is not probed"
}

@test "preflight never creates a lock, a guard, or a temporary in any damaged state" {
	local root

	root="$BATS_TEST_TMPDIR/readonly-fresh"
	cc_fresh_store "$root" 0
	cc_assert_preflight_readonly "$root" "freshly initialized store"

	root="$BATS_TEST_TMPDIR/readonly-corrupt-ledger"
	cc_fresh_store "$root" 2
	printf 'not json at all\n' >"$root/ledger.json"
	cc_assert_preflight_readonly "$root" "corrupt derived ledger"

	root="$BATS_TEST_TMPDIR/readonly-corrupt-view"
	cc_fresh_store "$root" 2
	printf 'torn{\n' >"$root/events.jsonl"
	cc_assert_preflight_readonly "$root" "corrupt derived compatibility view"

	root="$BATS_TEST_TMPDIR/readonly-event-gap"
	cc_fresh_store "$root" 2
	rm "$root"/events/000000000001-*.json
	cc_assert_preflight_readonly "$root" "gap in committed authority"

	root="$BATS_TEST_TMPDIR/readonly-removed-tip"
	cc_fresh_store "$root" 2
	rm "$root"/events/000000000002-*.json
	cc_assert_preflight_readonly "$root" "removed committed tip"

	root="$BATS_TEST_TMPDIR/readonly-debris"
	cc_fresh_store "$root" 1
	touch "$root/pending/000000000009-$(python3 -c 'import uuid; print(uuid.uuid4())').json"
	printf 'interrupted\n' >"$root/ledger.json.tmp"
	mkdir "$root/locks/control.lock.released-$(python3 -c 'import uuid; print(uuid.uuid4())')"
	mkdir "$root/locks/.control.lock.candidate-$(python3 -c 'import uuid; print(uuid.uuid4())')"
	cc_assert_preflight_readonly "$root" "non-authoritative debris"

	root="$BATS_TEST_TMPDIR/readonly-future-schema"
	cc_fresh_store "$root" 1
	python3 -c '
import json
import sys

path = sys.argv[1] + "/control.json"
with open(path) as handle:
    record = json.load(handle)
record["schema_version"] = 99
with open(path, "w") as handle:
    handle.write(json.dumps(record, indent=2, sort_keys=True) + "\n")
' "$root"
	cc_assert_preflight_readonly "$root" "future-versioned root metadata"

	root="$BATS_TEST_TMPDIR/readonly-missing-directory"
	cc_fresh_store "$root" 0
	rmdir "$root/escalations"
	cc_assert_preflight_readonly "$root" "missing required directory"

	root="$BATS_TEST_TMPDIR/readonly-stale-lock"
	cc_fresh_store "$root" 0
	python3 -c '
import json
import os
import socket
import sys
import uuid
from datetime import datetime, timezone

root = sys.argv[1]
lock = os.path.join(root, "locks", "control.lock")
os.mkdir(lock, 0o700)
owner = {
    "schema_version": 1,
    "record_type": "control-lock",
    "lock_id": str(uuid.uuid4()),
    "pid": 999999,
    "host": socket.gethostname(),
    "command": "abandoned-writer",
    "acquired_at": datetime.now(timezone.utc)
    .isoformat(timespec="microseconds")
    .replace("+00:00", "Z"),
}
with open(os.path.join(lock, "owner.json"), "w") as handle:
    handle.write(json.dumps(owner, indent=2, sort_keys=True) + "\n")
' "$root"
	cc_assert_preflight_readonly "$root" "stale authoritative lock"
}

@test "preflight separates derived corruption from committed authority" {
	local root="$BATS_TEST_TMPDIR/derived-versus-authority"
	cc_fresh_store "$root" 2
	printf 'not json at all\n' >"$root/ledger.json"

	run "$CONTROL_BIN" preflight
	[ "$status" -eq 0 ]
	echo "$output" | grep -Fq "preflight degraded in $root"
	echo "$output" | grep -Fq "ledger advisory derived: ledger.json is derived state"
	echo "$output" | grep -Fq "[repair: cockpit-control replay-ledger]"
	echo "$output" | grep -Fq "committed-revisions ready ok: 2 contiguous committed revision(s)"

	run "$CONTROL_BIN" replay-ledger
	[ "$status" -eq 0 ]

	run "$CONTROL_BIN" preflight
	[ "$status" -eq 0 ]
	echo "$output" | grep -Fq "preflight ready in $root"
}

@test "preflight reports a corrupt compatibility view as rebuildable derived state" {
	local root="$BATS_TEST_TMPDIR/derived-view"
	cc_fresh_store "$root" 2
	printf 'torn{\n' >"$root/events.jsonl"

	run "$CONTROL_BIN" preflight
	[ "$status" -eq 0 ]
	echo "$output" | grep -Fq "ledger advisory derived: events.jsonl is a rebuildable compatibility view"
	echo "$output" | grep -Fq "[repair: cockpit-control replay-ledger]"
	echo "$output" | grep -Fq "committed-revisions ready ok:"

	# The fail-closed validator is unchanged: it still refuses a torn record.
	run "$CONTROL_BIN" validate
	[ "$status" -ne 0 ]
	echo "$output" | grep -Fq "malformed events.jsonl"

	run "$CONTROL_BIN" replay-ledger
	[ "$status" -eq 0 ]

	run "$CONTROL_BIN" preflight
	[ "$status" -eq 0 ]
	echo "$output" | grep -Fq "preflight ready in $root"
	run "$CONTROL_BIN" validate
	[ "$status" -eq 0 ]
}

@test "preflight blocks when committed authority is missing or discontinuous" {
	local root="$BATS_TEST_TMPDIR/authority-gap"
	cc_fresh_store "$root" 2
	rm "$root"/events/000000000001-*.json

	run "$CONTROL_BIN" preflight
	[ "$status" -eq 1 ]
	echo "$output" | grep -Fq "preflight blocked in $root"
	echo "$output" | grep -Fq "committed-revisions blocked authoritative: events is missing revision 1"
	echo "$output" | grep -Fq "cockpit-control never rewrites, reorders, or removes committed authority"

	local tip="$BATS_TEST_TMPDIR/authority-tip"
	cc_fresh_store "$tip" 2
	rm "$tip"/events/000000000002-*.json

	run "$CONTROL_BIN" preflight
	[ "$status" -eq 1 ]
	echo "$output" | grep -Fq "committed events appear to have been removed and replay would rewind derived state"
	echo "$output" | grep -Fq "cockpit-control replay-ledger\` to rewind ledger.json to revision 1"
	# The clean observation is withheld: continuity was not proven.
	! echo "$output" | grep -q "^committed-revisions ready "
}

@test "preflight refuses future-versioned root metadata without repairing it" {
	local root="$BATS_TEST_TMPDIR/future-schema"
	cc_fresh_store "$root" 0
	python3 -c '
import json
import sys

path = sys.argv[1] + "/control.json"
with open(path) as handle:
    record = json.load(handle)
record["schema_version"] = 99
with open(path, "w") as handle:
    handle.write(json.dumps(record, indent=2, sort_keys=True) + "\n")
' "$root"
	local before
	before="$(cc_control_snapshot "$root")"

	run "$CONTROL_BIN" preflight
	[ "$status" -eq 1 ]
	echo "$output" | grep -Fq "schema blocked authoritative: control.json declares future schema_version 99"
	echo "$output" | grep -Fq "upgrade the installed cockpit tools with \`bootstrap.sh global\`"

	run "$CONTROL_BIN" repair-store
	[ "$status" -eq 1 ]
	echo "$output" | grep -Fq "unsupported future schema_version 99"

	run "$CONTROL_BIN" repair-store --dry-run
	[ "$status" -eq 1 ]

	[ "$(cc_control_snapshot "$root")" = "$before" ]
	[ ! -e "$root/quarantine" ]
	[ ! -e "$root/locks/control.guard" ]
}

@test "guarded repair refuses a discontinuous committed sequence without claiming debris" {
	local root="$BATS_TEST_TMPDIR/repair-refuses-gap"
	cc_fresh_store "$root" 2
	touch "$root/pending/000000000009-$(python3 -c 'import uuid; print(uuid.uuid4())').json"
	rm "$root"/events/000000000001-*.json

	local before_files
	before_files="$(find "$root" -type f -exec cksum '{}' ';' | LC_ALL=C sort)"
	local before_names
	before_names="$(find "$root" | LC_ALL=C sort)"

	run "$CONTROL_BIN" repair-store
	[ "$status" -eq 1 ]
	echo "$output" | grep -Fq "is missing revision 1"

	[ "$(find "$root" -type f -exec cksum '{}' ';' | LC_ALL=C sort)" = "$before_files" ]
	[ "$(find "$root" | LC_ALL=C sort)" = "$before_names" ]
	[ ! -e "$root/quarantine" ]
}

@test "guarded repair refuses debris while committed-event continuity is unproven" {
	local root="$BATS_TEST_TMPDIR/repair-refuses-rewind"
	cc_fresh_store "$root" 2
	touch "$root/pending/000000000009-$(python3 -c 'import uuid; print(uuid.uuid4())').json"
	rm "$root"/events/000000000002-*.json

	local before_files
	before_files="$(find "$root" -type f -exec cksum '{}' ';' | LC_ALL=C sort)"

	run "$CONTROL_BIN" repair-store
	[ "$status" -eq 1 ]
	echo "$output" | grep -Fq "committed-event continuity cannot be proven, so nothing is claimed"

	[ "$(find "$root" -type f -exec cksum '{}' ';' | LC_ALL=C sort)" = "$before_files" ]
	[ ! -e "$root/quarantine" ]
	[ "$(find "$root/pending" -type f | grep -c .)" -eq 1 ]
}

# cc_assert_repair_preview_refuses <root> <needle> — prove the lock-free preview
# applies exactly the fail-closed refusals the guarded repair applies. The
# preview must exit 1 with the same diagnosis, must advertise no repair at all,
# and must leave every byte, inode, mode, and nanosecond mtime untouched without
# ever creating quarantine/ or the transition guard. The guarded repair is then
# run to prove both halves agree on the same refusal.
cc_assert_repair_preview_refuses() {
	local root="$1" needle="$2" before files
	# A transition guard that does not exist must not be created by a preview
	# that is specified to take neither control.lock nor the guard.
	rm -f "$root/locks/control.guard"
	before="$(cc_control_snapshot "$root")"

	run "$CONTROL_BIN" repair-store --dry-run
	if [ "$status" -ne 1 ]; then
		printf 'repair-store --dry-run did not refuse (status %s):\n%s\n' "$status" "$output" >&2
		return 1
	fi
	echo "$output" | grep -Fq "$needle"
	! echo "$output" | grep -q "would repair"
	! echo "$output" | grep -q "would create"
	! echo "$output" | grep -q "would-quarantine"
	! echo "$output" | grep -q "would-create"

	if [ "$(cc_control_snapshot "$root")" != "$before" ]; then
		printf 'repair-store --dry-run mutated the store:\n' >&2
		diff <(printf '%s\n' "$before") <(printf '%s\n' "$(cc_control_snapshot "$root")") >&2 || true
		return 1
	fi
	[ ! -e "$root/quarantine" ]
	[ ! -e "$root/locks/control.guard" ]

	# The guarded repair refuses the same store with the same diagnosis. It is
	# allowed to take control.lock before it refuses, so locks/ is the only part
	# of the store exempt from the byte-for-byte comparison.
	files="$(find "$root" -type f ! -path "$root/locks/*" -exec cksum '{}' ';' | LC_ALL=C sort)"
	run "$CONTROL_BIN" repair-store
	[ "$status" -eq 1 ]
	echo "$output" | grep -Fq "$needle"
	[ "$(find "$root" -type f ! -path "$root/locks/*" -exec cksum '{}' ';' | LC_ALL=C sort)" = "$files" ]
	[ ! -e "$root/quarantine" ]
	return 0
}

@test "guarded repair preview refuses a future-versioned committed event without claiming debris" {
	local root="$BATS_TEST_TMPDIR/preview-refuses-future-event"
	cc_fresh_store "$root" 2
	python3 -c '
import glob
import json
import sys

path = sorted(glob.glob(sys.argv[1] + "/events/*.json"))[-1]
with open(path) as handle:
    record = json.load(handle)
record["schema_version"] = 99
with open(path, "w") as handle:
    handle.write(json.dumps(record, indent=2, sort_keys=True) + "\n")
' "$root"
	touch "$root/pending/000000000009-$(python3 -c 'import uuid; print(uuid.uuid4())').json"

	cc_assert_repair_preview_refuses "$root" "uses unsupported future schema_version 99"

	# The non-authoritative candidate the preview refused to claim is still there.
	[ "$(find "$root/pending" -type f | grep -c .)" -eq 1 ]
}

@test "guarded repair preview refuses unproven committed continuity without claiming debris" {
	local root="$BATS_TEST_TMPDIR/preview-refuses-rewind"
	cc_fresh_store "$root" 2
	touch "$root/pending/000000000009-$(python3 -c 'import uuid; print(uuid.uuid4())').json"
	rm "$root"/events/000000000002-*.json

	cc_assert_repair_preview_refuses "$root" \
		"committed-event continuity cannot be proven, so nothing is claimed"

	[ "$(find "$root/pending" -type f | grep -c .)" -eq 1 ]
}

@test "an interrupted cleanup quarantine is reported as debris and cleaned by explicit repair" {
	local root="$BATS_TEST_TMPDIR/interrupted-cleanup-quarantine"
	export COCKPIT_CONTROL_ROOT="$root"
	"$CONTROL_BIN" init >/dev/null

	run python3 -c '
import subprocess
import sys
from pathlib import Path

root = Path(sys.argv[1])
module_dir = sys.argv[2]
sys.path.insert(0, module_dir)
import cockpit_control

locks = root / cockpit_control.LOCKS_DIR_NAME
authoritative = locks / cockpit_control.CONTROL_LOCK_NAME

releaser_code = r"""
import sys
from pathlib import Path
sys.path.insert(0, sys.argv[2])
import cockpit_control
lock = cockpit_control.PortableControlLock(
    Path(sys.argv[1]),
    "releaser-interrupted-before-cleanup",
    timeout_seconds=5,
    poll_seconds=0.01,
).acquire()
print("LOCK_HELD " + lock.owner["lock_id"], flush=True)
if sys.stdin.readline().strip() != "release":
    raise RuntimeError("the releaser barrier was not received")
def park(boundary, transition):
    if boundary == "release-quarantined":
        print("RELEASE_QUARANTINED " + transition._quarantine_path.name, flush=True)
        sys.stdin.readline()
        raise AssertionError("the quarantine barrier was released instead of killed")
cockpit_control._lock_transition_fault = park
lock.release()
"""

releaser = subprocess.Popen(
    [sys.executable, "-c", releaser_code, str(root), module_dir],
    stdin=subprocess.PIPE,
    stdout=subprocess.PIPE,
    stderr=subprocess.PIPE,
    text=True,
    bufsize=1,
)
try:
    held = releaser.stdout.readline().split()
    assert held and held[0] == "LOCK_HELD", releaser.stderr.read()
    owner_bytes = (authoritative / cockpit_control.LOCK_OWNER_NAME).read_bytes()
    releaser.stdin.write("release\n")
    releaser.stdin.flush()
    parked = releaser.stdout.readline().split()
    assert parked and parked[0] == "RELEASE_QUARANTINED", releaser.stderr.read()
finally:
    releaser.kill()
    releaser.wait(timeout=5)
assert releaser.returncode != 0

quarantine = locks / parked[1]
assert quarantine.is_dir(), "the interrupted cleanup left a valid quarantine"
assert not authoritative.exists(), "the quarantine is not authoritative"
assert (quarantine / cockpit_control.LOCK_OWNER_NAME).read_bytes() == owner_bytes
print("QUARANTINE " + quarantine.name)
' "$root" "$MODULE_DIR"
	[ "$status" -eq 0 ]
	local quarantine_name
	quarantine_name="$(echo "$output" | awk '$1 == "QUARANTINE" { print $2 }')"
	[ -n "$quarantine_name" ]

	local before
	before="$(cc_control_snapshot "$root")"

	run "$CONTROL_BIN" preflight
	[ "$status" -eq 0 ]
	echo "$output" | grep -Fq "preflight degraded in $root"
	echo "$output" | grep -Fq "quarantines advisory debris: locks/$quarantine_name is a non-authoritative released lock quarantine left by an interrupted cleanup"
	echo "$output" | grep -Fq "[repair: cockpit-control repair-store]"
	echo "$output" | grep -Fq "authoritative-lock ready ok: no locks/control.lock is published"
	[ "$(cc_control_snapshot "$root")" = "$before" ]

	run "$CONTROL_BIN" repair-store --dry-run
	[ "$status" -eq 0 ]
	echo "$output" | grep -Fq "would-quarantine locks/$quarantine_name"
	[ "$(cc_control_snapshot "$root")" = "$before" ]

	run "$CONTROL_BIN" repair-store
	[ "$status" -eq 0 ]
	echo "$output" | grep -Fq "quarantined locks/$quarantine_name -> quarantine/"
	[ ! -e "$root/locks/$quarantine_name" ]
	[ "$(find "$root/quarantine" -mindepth 1 -maxdepth 1 | grep -c .)" -eq 1 ]
	find "$root/quarantine" -mindepth 1 -maxdepth 1 -name "*$quarantine_name" | grep -q .

	run "$CONTROL_BIN" preflight
	[ "$status" -eq 0 ]
	echo "$output" | grep -Fq "preflight ready in $root"
	echo "$output" | grep -Fq "quarantine/ retains 1 archived item(s)"

	local after_repair
	after_repair="$(cc_control_contents "$root")"
	run "$CONTROL_BIN" repair-store
	[ "$status" -eq 0 ]
	echo "$output" | grep -Fq "no repairable control-store debris in $root; no state changed"
	[ "$(cc_control_contents "$root")" = "$after_repair" ]

	run "$CONTROL_BIN" publish-event --type liveness-after-quarantine-cleanup
	[ "$status" -eq 0 ]
	echo "$output" | grep -Fq "at revision 1"
}

@test "guarded repair never claims a private lock candidate without explicit authorization" {
	local root="$BATS_TEST_TMPDIR/candidate-authorization"
	cc_fresh_store "$root" 1
	local lock_id
	lock_id="$(python3 -c 'import uuid; print(uuid.uuid4())')"
	mkdir "$root/locks/.control.lock.candidate-$lock_id"

	local before
	before="$(cc_control_snapshot "$root")"

	run "$CONTROL_BIN" repair-store --dry-run
	[ "$status" -eq 0 ]
	echo "$output" | grep -Fq "claimed nothing in $root"
	echo "$output" | grep -Fq "run: cockpit-control repair-store --authorize $lock_id"
	[ "$(cc_control_snapshot "$root")" = "$before" ]

	run "$CONTROL_BIN" preflight
	[ "$status" -eq 0 ]
	echo "$output" | grep -Fq "quarantines advisory debris: locks/.control.lock.candidate-$lock_id is an abandoned private lock candidate"
	echo "$output" | grep -Fq "[repair: cockpit-control repair-store --authorize $lock_id]"

	run "$CONTROL_BIN" repair-store
	[ "$status" -eq 0 ]
	echo "$output" | grep -Fq "claimed nothing in $root"
	echo "$output" | grep -Fq "run: cockpit-control repair-store --authorize $lock_id"
	[ -d "$root/locks/.control.lock.candidate-$lock_id" ]
	[ ! -e "$root/quarantine" ]

	run "$CONTROL_BIN" repair-store --authorize "$(python3 -c 'import uuid; print(uuid.uuid4())')"
	[ "$status" -eq 1 ]
	echo "$output" | grep -Fq "no private lock candidate under locks/ carries that owner UUID; no state changed"
	[ -d "$root/locks/.control.lock.candidate-$lock_id" ]
	[ ! -e "$root/quarantine" ]

	run "$CONTROL_BIN" repair-store --authorize not-a-uuid
	[ "$status" -eq 1 ]
	echo "$output" | grep -Fq "requires UUID authorized_lock_id"

	before="$(cc_control_snapshot "$root")"
	run "$CONTROL_BIN" repair-store --authorize "$lock_id" --dry-run
	[ "$status" -eq 0 ]
	echo "$output" | grep -Fq "would-quarantine locks/.control.lock.candidate-$lock_id"
	[ "$(cc_control_snapshot "$root")" = "$before" ]

	run "$CONTROL_BIN" repair-store --authorize "$lock_id"
	[ "$status" -eq 0 ]
	echo "$output" | grep -Fq "quarantined locks/.control.lock.candidate-$lock_id -> quarantine/"
	[ ! -e "$root/locks/.control.lock.candidate-$lock_id" ]
	[ "$(find "$root/quarantine" -mindepth 1 -maxdepth 1 | grep -c .)" -eq 1 ]

	run "$CONTROL_BIN" preflight
	[ "$status" -eq 0 ]
	echo "$output" | grep -Fq "preflight ready in $root"
}

@test "guarded repair restores every required directory idempotently" {
	local root="$BATS_TEST_TMPDIR/restore-directories"
	cc_fresh_store "$root" 1
	rmdir "$root/escalations" "$root/commands"

	run "$CONTROL_BIN" preflight
	[ "$status" -eq 1 ]
	echo "$output" | grep -Fq "root blocked configuration: commands/ is missing"
	echo "$output" | grep -Fq "root blocked configuration: escalations/ is missing"
	echo "$output" | grep -Fq "[repair: cockpit-control repair-store]"

	run "$CONTROL_BIN" repair-store --dry-run
	[ "$status" -eq 0 ]
	echo "$output" | grep -Fq "would-create commands/"
	echo "$output" | grep -Fq "would-create escalations/"
	[ ! -e "$root/commands" ]
	[ ! -e "$root/escalations" ]

	run "$CONTROL_BIN" repair-store
	[ "$status" -eq 0 ]
	echo "$output" | grep -Fq "created commands/"
	echo "$output" | grep -Fq "created escalations/"
	[ -d "$root/commands" ]
	[ -d "$root/escalations" ]

	local after
	after="$(cc_control_contents "$root")"
	run "$CONTROL_BIN" repair-store
	[ "$status" -eq 0 ]
	echo "$output" | grep -Fq "no repairable control-store debris in $root; no state changed"
	[ "$(cc_control_contents "$root")" = "$after" ]

	run "$CONTROL_BIN" preflight
	[ "$status" -eq 0 ]
	echo "$output" | grep -Fq "preflight ready in $root"
}

@test "guarded repair killed after one quarantine rename loses nothing and completes on re-run" {
	local root="$BATS_TEST_TMPDIR/repair-crash"
	cc_fresh_store "$root" 1

	run python3 -c '
import hashlib
import subprocess
import sys
from pathlib import Path
from uuid import uuid4

root = Path(sys.argv[1])
module_dir = sys.argv[2]
sys.path.insert(0, module_dir)
import cockpit_control

pending = root / cockpit_control.PENDING_DIR_NAME
quarantine = root / cockpit_control.QUARANTINE_DIR_NAME

debris = {}
for index in range(2):
    name = "%012d-%s.json" % (900 + index, uuid4())
    path = pending / name
    path.write_bytes(("abandoned-candidate-%d\n" % index).encode("utf-8"))
    debris[name] = hashlib.sha256(path.read_bytes()).hexdigest()

repair_code = r"""
import sys
from pathlib import Path
sys.path.insert(0, sys.argv[2])
import cockpit_control
def park(boundary, repair):
    if boundary == "item-quarantined":
        print("QUARANTINED " + repair.quarantined[-1][0], flush=True)
        sys.stdin.readline()
        raise AssertionError("the repair barrier was released instead of killed")
cockpit_control._store_repair_fault = park
cockpit_control.repair_control_store(
    Path(sys.argv[1]), timeout_seconds=5, poll_seconds=0.01
)
"""

repair = subprocess.Popen(
    [sys.executable, "-c", repair_code, str(root), module_dir],
    stdin=subprocess.PIPE,
    stdout=subprocess.PIPE,
    stderr=subprocess.PIPE,
    text=True,
    bufsize=1,
)
try:
    parked = repair.stdout.readline().split()
    assert parked and parked[0] == "QUARANTINED", repair.stderr.read()
finally:
    repair.kill()
    repair.wait(timeout=5)
assert repair.returncode != 0

claimed = parked[1].split("/")[-1]
archived = sorted(entry.name for entry in quarantine.iterdir())
assert len(archived) == 1, archived
assert archived[0].endswith(claimed), archived
assert (
    hashlib.sha256((quarantine / archived[0]).read_bytes()).hexdigest()
    == debris[claimed]
), "the rename preserved the exact debris bytes"
remaining = sorted(entry.name for entry in pending.iterdir())
assert remaining == sorted(name for name in debris if name != claimed), remaining

# The killed repair held the control lock, so preflight must now name the exact
# stale-lock repair before the store repair can be completed.
report = cockpit_control.run_control_preflight(root, "test")
assert report.blocked, report.status
lock_findings = [
    finding
    for finding in report.actionable
    if finding.dimension == "authoritative-lock"
]
assert len(lock_findings) == 1, lock_findings
assert lock_findings[0].repair == cockpit_control.REPAIR_ACTION_REPAIR_LOCK

repaired = cockpit_control.repair_stale_control_lock(root, timeout_seconds=5)
assert repaired.repaired

completed = cockpit_control.repair_control_store(
    root, timeout_seconds=5, poll_seconds=0.01
)
assert completed.repaired, completed
archived = sorted(entry.name for entry in quarantine.iterdir())
assert len(archived) == 3, archived
assert list(pending.iterdir()) == [], "every abandoned candidate was claimed exactly once"
for name, digest in debris.items():
    matches = [entry for entry in archived if entry.endswith(name)]
    assert len(matches) == 1, (name, archived)
    assert (
        hashlib.sha256((quarantine / matches[0]).read_bytes()).hexdigest() == digest
    ), name

published = cockpit_control.publish_control_event(
    root,
    "liveness-after-repair-crash",
    command="writer-after-repair-crash",
    timeout_seconds=5,
    poll_seconds=0.01,
)
assert published.committed
assert published.revision == 2, published.revision
print("the interrupted repair claimed each item exactly once and lost nothing")
' "$root" "$MODULE_DIR"
	[ "$status" -eq 0 ]
	echo "$output" | grep -Fq "the interrupted repair claimed each item exactly once and lost nothing"

	run "$CONTROL_BIN" preflight
	[ "$status" -eq 0 ]
	echo "$output" | grep -Fq "preflight ready in $root"
}

@test "guarded repair waits boundedly for a live owner and claims nothing" {
	local root="$BATS_TEST_TMPDIR/repair-live-owner"
	cc_fresh_store "$root" 1
	touch "$root/pending/000000000009-$(python3 -c 'import uuid; print(uuid.uuid4())').json"

	run python3 -c '
import subprocess
import sys
from pathlib import Path

root = Path(sys.argv[1])
module_dir = sys.argv[2]
sys.path.insert(0, module_dir)
import cockpit_control

pending = root / cockpit_control.PENDING_DIR_NAME
before = sorted(entry.name for entry in pending.iterdir())

holder_code = r"""
import sys
from pathlib import Path
sys.path.insert(0, sys.argv[2])
import cockpit_control
lock = cockpit_control.PortableControlLock(
    Path(sys.argv[1]), "live-owner", timeout_seconds=5, poll_seconds=0.01
).acquire()
print("LOCK_HELD " + lock.owner["lock_id"], flush=True)
sys.stdin.readline()
"""

holder = subprocess.Popen(
    [sys.executable, "-c", holder_code, str(root), module_dir],
    stdin=subprocess.PIPE,
    stdout=subprocess.PIPE,
    stderr=subprocess.PIPE,
    text=True,
    bufsize=1,
)
try:
    held = holder.stdout.readline().split()
    assert held and held[0] == "LOCK_HELD", holder.stderr.read()

    report = cockpit_control.run_control_preflight(root, "test")
    findings = [
        finding
        for finding in report.actionable
        if finding.dimension == "authoritative-lock"
    ]
    assert len(findings) == 1, findings
    assert findings[0].state == cockpit_control.PREFLIGHT_ADVISORY, findings[0]
    assert "mutation waits for its release" in findings[0].detail
    assert not report.blocked, report.status

    try:
        cockpit_control.repair_control_store(
            root, timeout_seconds=0.2, poll_seconds=0.01
        )
    except cockpit_control.ControlStoreError as error:
        assert "timed out" in str(error), error
    else:
        raise AssertionError("repair claimed debris while a live owner held the lock")
finally:
    holder.kill()
    holder.wait(timeout=5)

assert sorted(entry.name for entry in pending.iterdir()) == before
assert not (root / cockpit_control.QUARANTINE_DIR_NAME).exists()
print("the bounded repair refused to claim debris under a live owner")
' "$root" "$MODULE_DIR"
	[ "$status" -eq 0 ]
	echo "$output" | grep -Fq "the bounded repair refused to claim debris under a live owner"
}

@test "preflight reports declared roots, queue agreement, and legacy capability" {
	local root="$BATS_TEST_TMPDIR/declared-roots"
	local queue="$BATS_TEST_TMPDIR/declared-queue"
	local planning="$BATS_TEST_TMPDIR/declared-planning"
	local implementation="$BATS_TEST_TMPDIR/declared-implementation"
	mkdir -p "$queue" "$planning" "$implementation"
	cc_fresh_store "$root" 1

	python3 -c '
import json
import sys

path = sys.argv[1] + "/control.json"
with open(path) as handle:
    record = json.load(handle)
roots = record["canonical_roots"]
roots["queue_root"] = sys.argv[2]
roots["planning_root"] = sys.argv[3]
roots["implementation_roots"] = [sys.argv[4]]
record["queue_root"] = sys.argv[2]
record["planning_root"] = sys.argv[3]
record["implementation_roots"] = [sys.argv[4]]
record["capabilities"]["control_store"] = 0
with open(path, "w") as handle:
    handle.write(json.dumps(record, indent=2, sort_keys=True) + "\n")
' "$root" "$queue" "$planning" "$implementation"

	local before
	before="$(cc_control_snapshot "$root")"

	export COCKPIT_QUEUE_ROOT="$queue"
	run "$CONTROL_BIN" preflight
	[ "$status" -eq 0 ]
	echo "$output" | grep -Fq "queue ready ok: the declared queue root $queue exists"
	echo "$output" | grep -Fq "declared-paths ready ok: 2 declared root(s) exist and are writable"
	echo "$output" | grep -Fq "worker-capability advisory configuration: control.json declares legacy capabilities.control_store 0"
	echo "$output" | grep -Fq "legacy-observed"

	export COCKPIT_QUEUE_ROOT="$BATS_TEST_TMPDIR/other-queue"
	run "$CONTROL_BIN" preflight
	[ "$status" -eq 1 ]
	echo "$output" | grep -Fq "disagrees with the declared queue root $queue"
	echo "$output" | grep -Fq "[repair: export COCKPIT_QUEUE_ROOT=$queue]"

	rmdir "$planning"
	export COCKPIT_QUEUE_ROOT="$queue"
	run "$CONTROL_BIN" preflight
	[ "$status" -eq 1 ]
	echo "$output" | grep -Fq "declared-paths blocked configuration: the declared planning root $planning does not exist"
	echo "$output" | grep -Fq "[repair: mkdir -p $planning]"
	mkdir -p "$planning"

	[ "$(cc_control_snapshot "$root")" = "$before" ]
}

@test "preflight blocks on a missing control root and names the exact bootstrap command" {
	local root="$BATS_TEST_TMPDIR/absent-root"
	export COCKPIT_CONTROL_ROOT="$root"

	run "$CONTROL_BIN" preflight
	[ "$status" -eq 1 ]
	echo "$output" | grep -Fq "root blocked configuration: $root does not exist"
	echo "$output" | grep -Fq "[repair: cockpit-control init]"
	[ "$(echo "$output" | grep -c '^[a-z-]* undeterminable ')" -ge 1 ]
	[ ! -e "$root" ]

	local dimension
	for dimension in root schema transition-guard authoritative-lock quarantines \
		pending-events committed-revisions ledger queue worker-capability declared-paths; do
		if ! echo "$output" | grep -q "^$dimension "; then
			printf 'preflight never reported dimension: %s\n' "$dimension" >&2
			return 1
		fi
	done
}

# --- cross-story recovery: one crashed writer, both findings, ordered repairs --
#
# A real writer that dies mid-publication is the operator's actual failure mode,
# and it damages two dimensions at once: it leaves the authoritative lock held by
# a dead owner (US2/US3) and a torn private publication candidate in pending/
# (US4).  The per-story suites prove each half in isolation; this test proves the
# whole chain composes through the installed CLI: one preflight report names both
# exact repairs, the store repair fails closed against the stale lock so the lock
# repair must run first, and only then does the store return to ready with a new
# writer publishing at the next contiguous revision and every byte of evidence
# retained in quarantine/.
@test "a crashed writer's stale lock and pending debris recover through the preflight-named repairs" {
	local root="$BATS_TEST_TMPDIR/crashed-writer-recovery"
	cc_fresh_store "$root" 1

	# Crash one real writer at the named `revision-allocated` fault boundary: the
	# candidate is flushed, the process is SIGKILLed there, and no timing guess
	# is involved.
	run python3 -c '
import json
import subprocess
import sys
from pathlib import Path

root = Path(sys.argv[1])
module_dir = sys.argv[2]
sys.path.insert(0, module_dir)
import cockpit_control

writer_code = r"""
import os
import sys
from pathlib import Path
sys.path.insert(0, sys.argv[2])
import cockpit_control

TORN_PREFIX_BYTES = 24

def torn_write(path, record):
    partial = cockpit_control._serialized_record(record).encode("utf-8")[:TORN_PREFIX_BYTES]
    descriptor = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    os.write(descriptor, partial)
    os.fsync(descriptor)
    os.close(descriptor)
    print("TORN " + path.name + " " + partial.hex(), flush=True)
    sys.stdin.readline()
    raise AssertionError("the torn candidate barrier was released instead of killed")

def fault(boundary, publication):
    if boundary == "revision-allocated":
        cockpit_control._write_json = torn_write

cockpit_control._event_publication_fault = fault
cockpit_control.publish_control_event(
    Path(sys.argv[1]),
    "crashed-writer",
    actor="crashed-writer",
    command="crashed-writer",
    timeout_seconds=5,
    poll_seconds=0.01,
)
"""

writer = subprocess.Popen(
    [sys.executable, "-c", writer_code, str(root), module_dir],
    stdin=subprocess.PIPE,
    stdout=subprocess.PIPE,
    stderr=subprocess.PIPE,
    text=True,
    bufsize=1,
)
try:
    torn = writer.stdout.readline().split()
    assert torn and torn[0] == "TORN", writer.stderr.read()
finally:
    writer.kill()
    writer.wait(timeout=5)
assert writer.returncode != 0

locks = root / cockpit_control.LOCKS_DIR_NAME
owner_path = locks / cockpit_control.CONTROL_LOCK_NAME / cockpit_control.LOCK_OWNER_NAME
owner = json.loads(owner_path.read_text())
debris = root / cockpit_control.PENDING_DIR_NAME / torn[1]
assert debris.read_bytes() == bytes.fromhex(torn[2])
print("DEBRIS " + torn[1])
print("TORNHEX " + torn[2])
print("LOCK " + owner["lock_id"])
' "$root" "$MODULE_DIR"
	[ "$status" -eq 0 ]

	local debris torn_hex lock_id
	debris="$(echo "$output" | awk '$1 == "DEBRIS" { print $2 }')"
	torn_hex="$(echo "$output" | awk '$1 == "TORNHEX" { print $2 }')"
	lock_id="$(echo "$output" | awk '$1 == "LOCK" { print $2 }')"
	[ -n "$debris" ]
	[ -n "$torn_hex" ]
	[ -n "$lock_id" ]

	# One read-only report diagnoses both dimensions and names the exact command
	# for each, without touching a byte.
	local before
	before="$(cc_control_snapshot "$root")"

	run "$CONTROL_BIN" preflight
	[ "$status" -eq 1 ]
	echo "$output" | grep -Fq "preflight blocked in $root"
	echo "$output" | grep -Fq "authoritative-lock blocked authoritative: locks/control.lock is owned by lock $lock_id"
	echo "$output" | grep -Fq "every writer blocks until it is repaired [repair: cockpit-control repair-lock]"
	echo "$output" | grep -Fq "pending-events advisory debris: pending/$debris"
	echo "$output" | grep -Fq "replay never treats it as a committed event [repair: cockpit-control repair-store]"
	# The committed sequence is untouched: the torn candidate never became one.
	echo "$output" | grep -Fq "committed-revisions ready ok: 1 contiguous committed revision(s)"
	[ "$(cc_control_snapshot "$root")" = "$before" ]

	# The store repair needs the control lock the dead owner still holds, so it
	# waits boundedly and claims nothing: the lock repair has to run first.
	local contents
	contents="$(cc_control_contents "$root")"
	COCKPIT_CONTROL_LOCK_TIMEOUT_SECONDS=0.2 run "$CONTROL_BIN" repair-store
	[ "$status" -eq 1 ]
	echo "$output" | grep -Fq "timed out after 0.2s waiting for locks/control.lock"
	[ "$(cc_control_contents "$root")" = "$contents" ]
	[ ! -e "$root/quarantine" ]
	[ -f "$root/pending/$debris" ]

	# The named lock repair quarantines the provably dead owner by rename only.
	run "$CONTROL_BIN" repair-lock
	[ "$status" -eq 0 ]
	echo "$output" | grep -Fq "quarantined control lock $lock_id as locks/control.lock.repaired-$lock_id-"
	echo "$output" | grep -Fq "proven same-host owner death"
	[ ! -e "$root/locks/control.lock" ]

	# Only now does the store repair claim both non-authoritative items.
	run "$CONTROL_BIN" repair-store
	[ "$status" -eq 0 ]
	echo "$output" | grep -Fq "quarantined pending/$debris -> quarantine/"
	echo "$output" | grep -Fq "quarantined locks/control.lock.repaired-$lock_id-"
	[ ! -e "$root/pending/$debris" ]

	# Nothing was deleted: the torn candidate is retained byte for byte.
	run python3 -c '
import sys
from pathlib import Path

quarantine = Path(sys.argv[1]) / "quarantine"
expected = bytes.fromhex(sys.argv[3])
retained = [path for path in quarantine.iterdir() if path.name.endswith(sys.argv[2])]
assert len(retained) == 1, retained
assert retained[0].read_bytes() == expected, "the quarantined candidate lost bytes"
print("RETAINED")
' "$root" "$debris" "$torn_hex"
	[ "$status" -eq 0 ]
	echo "$output" | grep -Fq "RETAINED"

	# The repaired control plane is ready again and re-running the repair is a
	# no-op.
	run "$CONTROL_BIN" preflight
	[ "$status" -eq 0 ]
	echo "$output" | grep -Fq "preflight ready in $root"
	echo "$output" | grep -Fq "quarantine/ retains 2 archived item(s)"

	local after_repair
	after_repair="$(cc_control_contents "$root")"
	run "$CONTROL_BIN" repair-store
	[ "$status" -eq 0 ]
	echo "$output" | grep -Fq "no repairable control-store debris in $root; no state changed"
	[ "$(cc_control_contents "$root")" = "$after_repair" ]

	# Liveness: a new writer publishes at the next contiguous revision, so the
	# crashed candidate never consumed one, and the derived ledger agrees.
	run "$CONTROL_BIN" publish-event --type liveness-after-crash-recovery
	[ "$status" -eq 0 ]
	echo "$output" | grep -Fq "at revision 2"

	run "$CONTROL_BIN" replay-ledger
	[ "$status" -eq 0 ]
	echo "$output" | grep -Fq "ledger.json projection is current at revision 2"

	run "$CONTROL_BIN" validate
	[ "$status" -eq 0 ]
}
