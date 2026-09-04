#!/usr/bin/env bats
# tests/unit/cmd-control.bats — versioned VP3 control-store contract tests.

load helper

setup() {
	cc_setup_fake_home
	export CONTROL_BIN="$BATS_TEST_DIRNAME/../../bin/cockpit-control"
	export OVERSEER_BIN="$BATS_TEST_DIRNAME/../../bin/cockpit-overseer"
	unset COCKPIT_CONTROL_ROOT TMUX TMUX_SESSION COCKPIT_SESSION_ID COCKPIT_ID
}

@test "overseer start atomically creates a complete versioned control root" {
	local root="$BATS_TEST_TMPDIR/control-root"
	export COCKPIT_CONTROL_ROOT="$root"

	run "$OVERSEER_BIN" start
	[ "$status" -eq 0 ]
	echo "$output" | grep -q "control store initialized"
	[ -f "$root/control.json" ]
	[ -f "$root/ledger.json" ]
	[ -f "$root/events.jsonl" ]
	[ -d "$root/commands" ]
	[ -d "$root/escalations" ]
	[ -d "$root/locks" ]
	! find "$BATS_TEST_TMPDIR" -maxdepth 1 -name '.cockpit-control-*' | grep -q .

	run python3 -c '
import json
import sys
from uuid import UUID

root = sys.argv[1]
metadata = json.load(open(root + "/control.json"))
ledger = json.load(open(root + "/ledger.json"))
assert metadata["schema_version"] == 1
assert metadata["record_type"] == "control-root"
assert metadata["control_root"] == root
assert metadata["canonical_roots"]["control_root"] == root
assert metadata["canonical_roots"]["queue_root"] is None
assert metadata["canonical_roots"]["planning_root"] is None
assert metadata["canonical_roots"]["implementation_roots"] == []
UUID(metadata["control_id"])
assert metadata["control_id"] == ledger["control_id"]
assert ledger["schema_version"] == 1
assert ledger["record_type"] == "ledger"
assert ledger["revision"] == 0
UUID(ledger["ledger_id"])
' "$root"
	[ "$status" -eq 0 ]

	run "$CONTROL_BIN" validate
	[ "$status" -eq 0 ]
	echo "$output" | grep -Fq "$root"
}

@test "control root falls back only to the exact active tmux session value" {
	local root="$BATS_TEST_TMPDIR/tmux root"
	local cwd="$BATS_TEST_TMPDIR/unrelated-cwd"
	mkdir -p "$BATS_TEST_TMPDIR/bin" "$cwd"
	cat > "$BATS_TEST_TMPDIR/bin/tmux" <<EOF
#!/usr/bin/env bash
if [ "\$1" = "show-environment" ] && [ "\$2" = "COCKPIT_CONTROL_ROOT" ]; then
	printf 'COCKPIT_CONTROL_ROOT=%s\n' "$root"
	exit 0
fi
if [ "\$1" = "display-message" ]; then
	printf 'tmux-cockpit\n'
	exit 0
fi
exit 1
EOF
	chmod +x "$BATS_TEST_TMPDIR/bin/tmux"
	export PATH="$BATS_TEST_TMPDIR/bin:$PATH"
	export TMUX="$BATS_TEST_TMPDIR/tmux-socket,123,0"

	run bash -c 'cd "$1" && "$2" init' -- "$cwd" "$CONTROL_BIN"
	[ "$status" -eq 0 ]
	echo "$output" | grep -q "source: tmux"
	[ -f "$root/control.json" ]
	[ ! -e "$cwd/control.json" ]

	run python3 -c '
import json
import sys
metadata = json.load(open(sys.argv[1] + "/control.json"))
assert metadata["control_root"] == sys.argv[1]
assert metadata["session_id"] == "tmux-cockpit"
' "$root"
	[ "$status" -eq 0 ]
}

@test "missing or relative control roots fail without creating state" {
	local missing_root="$BATS_TEST_TMPDIR/missing-root"
	local cwd="$BATS_TEST_TMPDIR/cwd"
	mkdir -p "$cwd"

	run bash -c 'cd "$1" && "$2" init' -- "$cwd" "$CONTROL_BIN"
	[ "$status" -ne 0 ]
	echo "$output" | grep -q "COCKPIT_CONTROL_ROOT is required"
	[ ! -e "$missing_root" ]
	[ ! -e "$cwd/control.json" ]

	export COCKPIT_CONTROL_ROOT="relative-control-root"
	run "$CONTROL_BIN" init
	[ "$status" -ne 0 ]
	echo "$output" | grep -q "must be an absolute path"
	[ ! -e "$BATS_TEST_TMPDIR/relative-control-root" ]
}

@test "valid event command and escalation records validate their required correlations" {
	local root="$BATS_TEST_TMPDIR/records"
	export COCKPIT_CONTROL_ROOT="$root"
	"$CONTROL_BIN" init >/dev/null

	run python3 -c '
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

root = Path(sys.argv[1])
control_id = json.loads((root / "control.json").read_text())["control_id"]
timestamp = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
mission_id = str(uuid4())
event = {
    "schema_version": 1, "record_type": "event", "event_id": str(uuid4()),
    "control_id": control_id, "timestamp": timestamp, "revision": 1,
    "event_type": "control-initialized", "actor": "overseer",
}
command = {
    "schema_version": 1, "record_type": "command", "command_id": str(uuid4()),
    "control_id": control_id, "mission_id": mission_id, "queue_item_id": "QI-1",
    "worker_id": "worker-dev", "command_type": "dispatch", "created_at": timestamp,
}
escalation = {
    "schema_version": 1, "record_type": "escalation", "escalation_id": str(uuid4()),
    "control_id": control_id, "mission_id": mission_id, "queue_item_id": "QI-1",
    "status": "open", "created_at": timestamp,
}
(root / "events.jsonl").write_text(json.dumps(event) + "\n")
(root / "commands" / (command["command_id"] + ".json")).write_text(json.dumps(command))
(root / "escalations" / (escalation["escalation_id"] + ".json")).write_text(json.dumps(escalation))
' "$root"
	[ "$status" -eq 0 ]

	run "$CONTROL_BIN" validate
	[ "$status" -eq 0 ]

	run python3 -c '
import sys
from pathlib import Path
root = Path(sys.argv[1])
path = next((root / "commands").glob("*.json"))
path.write_text(path.read_text().replace("\"mission_id\"", "\"missing_mission_id\""))
' "$root"
	[ "$status" -eq 0 ]
	local before
	before="$(cksum "$root/commands/"*.json)"

	run "$CONTROL_BIN" init
	[ "$status" -ne 0 ]
	echo "$output" | grep -q "requires non-empty mission_id"
	[ "$(cksum "$root/commands/"*.json)" = "$before" ]
}

@test "event and record filenames cannot reuse required identifiers" {
	local root="$BATS_TEST_TMPDIR/duplicate-identifiers"
	export COCKPIT_CONTROL_ROOT="$root"
	"$CONTROL_BIN" init >/dev/null

	run python3 -c '
import json
import sys
from pathlib import Path
from uuid import uuid4

root = Path(sys.argv[1])
control_id = json.loads((root / "control.json").read_text())["control_id"]
event_id = str(uuid4())
event = {
    "schema_version": 1, "record_type": "event", "event_id": event_id,
    "control_id": control_id, "timestamp": "2026-09-03T15:35:15Z",
    "revision": 1, "event_type": "started", "actor": "overseer",
}
(root / "events.jsonl").write_text(json.dumps(event) + "\n" + json.dumps(event) + "\n")
' "$root"
	[ "$status" -eq 0 ]
	local before
	before="$(cksum "$root/events.jsonl")"

	run "$CONTROL_BIN" init
	[ "$status" -ne 0 ]
	echo "$output" | grep -q "duplicates event_id"
	[ "$(cksum "$root/events.jsonl")" = "$before" ]
}

@test "all record schemas reject future versions and missing identifier fields" {
	run python3 -c '
import sys
from pathlib import Path
from uuid import uuid4

sys.path.insert(0, sys.argv[1])
from cockpit_control import (
    CONTROL_SCHEMA_VERSION,
    ControlStoreError,
    validate_command,
    validate_escalation,
    validate_event,
    validate_ledger,
    validate_root_metadata,
)

control_id = str(uuid4())
timestamp = "2026-09-03T15:35:15Z"
validators = (
    (
        validate_root_metadata,
        (Path("/control"),),
        {
            "schema_version": 1, "record_type": "control-root", "control_id": control_id,
            "cockpit_id": "cockpit", "session_id": "session", "control_root": "/control",
            "canonical_roots": {
                "control_root": "/control", "queue_root": None, "planning_root": None,
                "implementation_roots": [],
            }, "implementation_roots": [],
            "queue_root": None, "planning_root": None, "capabilities": {},
            "tool_capability_versions": {}, "created_at": timestamp, "last_migration_at": timestamp,
        },
        "control_id",
    ),
    (
        validate_ledger,
        (control_id,),
        {
            "schema_version": 1, "record_type": "ledger", "ledger_id": str(uuid4()),
            "control_id": control_id, "revision": 0, "created_at": timestamp,
            "updated_at": timestamp, "active_queue_item_id": None, "active_mission_id": None,
            "canonical_roots": {
                "control_root": "/control", "queue_root": None, "planning_root": None,
                "implementation_roots": [],
            },
        },
        "ledger_id",
    ),
    (
        validate_event,
        (control_id,),
        {
            "schema_version": 1, "record_type": "event", "event_id": str(uuid4()),
            "control_id": control_id, "timestamp": timestamp, "revision": 1,
            "event_type": "started", "actor": "overseer",
        },
        "event_id",
    ),
    (
        validate_command,
        (control_id,),
        {
            "schema_version": 1, "record_type": "command", "command_id": str(uuid4()),
            "control_id": control_id, "mission_id": str(uuid4()), "queue_item_id": "QI-1",
            "worker_id": "worker-dev", "command_type": "dispatch", "created_at": timestamp,
        },
        "command_id",
    ),
    (
        validate_escalation,
        (control_id,),
        {
            "schema_version": 1, "record_type": "escalation", "escalation_id": str(uuid4()),
            "control_id": control_id, "mission_id": str(uuid4()), "queue_item_id": "QI-1",
            "status": "open", "created_at": timestamp,
        },
        "escalation_id",
    ),
)
for validator, arguments, record, identifier in validators:
    try:
        validator({"schema_version": CONTROL_SCHEMA_VERSION + 1}, *arguments)
    except ControlStoreError as error:
        assert "unsupported future schema_version" in str(error)
    else:
        raise AssertionError("future schema version was accepted")
    record.pop(identifier)
    try:
        validator(record, *arguments)
    except ControlStoreError as error:
        assert identifier in str(error)
    else:
        raise AssertionError("missing identifier was accepted")
print("all schema versions and identifiers rejected")
' "$BATS_TEST_DIRNAME/../../bin"
	[ "$status" -eq 0 ]
	echo "$output" | grep -q "all schema versions and identifiers rejected"
}

@test "future root metadata is rejected by a mutating command without state changes" {
	local root="$BATS_TEST_TMPDIR/future-root"
	export COCKPIT_CONTROL_ROOT="$root"
	"$CONTROL_BIN" init >/dev/null

	run python3 -c '
import json
import sys
from pathlib import Path
path = Path(sys.argv[1])
record = json.loads(path.read_text())
record["schema_version"] = 2
path.write_text(json.dumps(record, sort_keys=True) + "\n")
' "$root/control.json"
	[ "$status" -eq 0 ]
	local before
	before="$(find "$root" -type f -exec cksum '{}' ';' | LC_ALL=C sort)"

	run "$CONTROL_BIN" init
	[ "$status" -ne 0 ]
	echo "$output" | grep -q "unsupported future schema_version 2"
	[ "$(find "$root" -type f -exec cksum '{}' ';' | LC_ALL=C sort)" = "$before" ]
}

@test "canonical root declarations and active mission IDs fail closed without mutation" {
	local root="$BATS_TEST_TMPDIR/canonical-root-validation"
	export COCKPIT_CONTROL_ROOT="$root"
	"$CONTROL_BIN" init >/dev/null

	run python3 -c '
import json
import sys
from pathlib import Path
path = Path(sys.argv[1])
record = json.loads(path.read_text())
del record["canonical_roots"]["queue_root"]
path.write_text(json.dumps(record, sort_keys=True) + "\n")
' "$root/control.json"
	[ "$status" -eq 0 ]
	local before
	before="$(find "$root" -type f -exec cksum '{}' ';' | LC_ALL=C sort)"

	run "$CONTROL_BIN" init
	[ "$status" -ne 0 ]
	echo "$output" | grep -Fq "control.json canonical_roots requires queue_root"
	[ "$(find "$root" -type f -exec cksum '{}' ';' | LC_ALL=C sort)" = "$before" ]

	run python3 -c '
import json
import sys
from pathlib import Path
path = Path(sys.argv[1])
record = json.loads(path.read_text())
record["canonical_roots"]["queue_root"] = None
path.write_text(json.dumps(record, sort_keys=True) + "\n")
' "$root/control.json"
	[ "$status" -eq 0 ]

	for field in queue_root planning_root; do
		run python3 -c '
import json
import sys
from pathlib import Path
path = Path(sys.argv[1])
record = json.loads(path.read_text())
record["canonical_roots"][sys.argv[2]] = "relative-root"
path.write_text(json.dumps(record, sort_keys=True) + "\n")
' "$root/control.json" "$field"
		[ "$status" -eq 0 ]
		local before
		before="$(find "$root" -type f -exec cksum '{}' ';' | LC_ALL=C sort)"

		run "$CONTROL_BIN" init
		[ "$status" -ne 0 ]
		echo "$output" | grep -Fq "canonical_roots $field COCKPIT_CONTROL_ROOT must be an absolute path"
		[ "$(find "$root" -type f -exec cksum '{}' ';' | LC_ALL=C sort)" = "$before" ]

		run python3 -c '
import json
import sys
from pathlib import Path
path = Path(sys.argv[1])
record = json.loads(path.read_text())
record["canonical_roots"][sys.argv[2]] = None
path.write_text(json.dumps(record, sort_keys=True) + "\n")
' "$root/control.json" "$field"
		[ "$status" -eq 0 ]
	done

	run python3 -c '
import json
import sys
from pathlib import Path
path = Path(sys.argv[1])
record = json.loads(path.read_text())
record["canonical_roots"]["implementation_roots"] = ["relative-root"]
path.write_text(json.dumps(record, sort_keys=True) + "\n")
' "$root/control.json"
	[ "$status" -eq 0 ]
	local before
	before="$(find "$root" -type f -exec cksum '{}' ';' | LC_ALL=C sort)"

	run "$CONTROL_BIN" init
	[ "$status" -ne 0 ]
	echo "$output" | grep -Fq "canonical_roots.implementation_roots[0] COCKPIT_CONTROL_ROOT must be an absolute path"
	[ "$(find "$root" -type f -exec cksum '{}' ';' | LC_ALL=C sort)" = "$before" ]

	run python3 -c '
import json
import sys
from pathlib import Path
path = Path(sys.argv[1])
record = json.loads(path.read_text())
record["canonical_roots"]["implementation_roots"] = []
path.write_text(json.dumps(record, sort_keys=True) + "\n")
' "$root/control.json"
	[ "$status" -eq 0 ]

	run python3 -c '
import json
import sys
from pathlib import Path
path = Path(sys.argv[1])
record = json.loads(path.read_text())
record["canonical_roots"]["planning_root"] = "relative-root"
path.write_text(json.dumps(record, sort_keys=True) + "\n")
' "$root/ledger.json"
	[ "$status" -eq 0 ]
	before="$(find "$root" -type f -exec cksum '{}' ';' | LC_ALL=C sort)"

	run "$CONTROL_BIN" init
	[ "$status" -ne 0 ]
	echo "$output" | grep -Fq "ledger.json canonical_roots planning_root COCKPIT_CONTROL_ROOT must be an absolute path"
	[ "$(find "$root" -type f -exec cksum '{}' ';' | LC_ALL=C sort)" = "$before" ]

	run python3 -c '
import json
import sys
from pathlib import Path
path = Path(sys.argv[1])
record = json.loads(path.read_text())
record["canonical_roots"]["planning_root"] = None
del record["active_mission_id"]
path.write_text(json.dumps(record, sort_keys=True) + "\n")
' "$root/ledger.json"
	[ "$status" -eq 0 ]
	before="$(find "$root" -type f -exec cksum '{}' ';' | LC_ALL=C sort)"

	run "$CONTROL_BIN" init
	[ "$status" -ne 0 ]
	echo "$output" | grep -Fq "ledger.json requires active_mission_id"
	[ "$(find "$root" -type f -exec cksum '{}' ';' | LC_ALL=C sort)" = "$before" ]

	run python3 -c '
import json
import sys
from pathlib import Path
path = Path(sys.argv[1])
record = json.loads(path.read_text())
record["active_mission_id"] = "not-a-uuid"
record["active_queue_item_id"] = "QI-1"
path.write_text(json.dumps(record, sort_keys=True) + "\n")
' "$root/ledger.json"
	[ "$status" -eq 0 ]
	before="$(find "$root" -type f -exec cksum '{}' ';' | LC_ALL=C sort)"

	run "$CONTROL_BIN" init
	[ "$status" -ne 0 ]
	echo "$output" | grep -Fq "ledger.json requires UUID active_mission_id"
	[ "$(find "$root" -type f -exec cksum '{}' ';' | LC_ALL=C sort)" = "$before" ]
}

@test "malformed root metadata is rejected by a mutating command without state changes" {
	local root="$BATS_TEST_TMPDIR/malformed-root"
	export COCKPIT_CONTROL_ROOT="$root"
	"$CONTROL_BIN" init >/dev/null

	printf '{not-json\n' > "$root/control.json"
	local before
	before="$(find "$root" -type f -exec cksum '{}' ';' | LC_ALL=C sort)"

	run "$CONTROL_BIN" init
	[ "$status" -ne 0 ]
	echo "$output" | grep -q "malformed control.json"
	[ "$(find "$root" -type f -exec cksum '{}' ';' | LC_ALL=C sort)" = "$before" ]
}

@test "a complete private owner is atomically published and release is quarantined first" {
	local root="$BATS_TEST_TMPDIR/atomic-lock"
	export COCKPIT_CONTROL_ROOT="$root"
	"$CONTROL_BIN" init >/dev/null

	run python3 -c '
import json
import stat
import sys
from pathlib import Path

sys.path.insert(0, sys.argv[2])
import cockpit_control

root = Path(sys.argv[1])
locks = root / cockpit_control.LOCKS_DIR_NAME
authoritative = locks / cockpit_control.CONTROL_LOCK_NAME
candidate_identity = {}
published_identity = {}
boundaries = []

def fault(boundary, lock):
    boundaries.append(boundary)
    if boundary == "candidate-prepared":
        candidates = list(locks.glob(cockpit_control.LOCK_CANDIDATE_PREFIX + "*"))
        assert candidates == [lock._candidate_path]
        candidate = candidates[0]
        assert not authoritative.exists()
        assert {entry.name for entry in candidate.iterdir()} == {
            cockpit_control.LOCK_OWNER_NAME
        }
        owner = json.loads((candidate / cockpit_control.LOCK_OWNER_NAME).read_text())
        cockpit_control._validate_lock_owner(owner)
        assert owner["command"] == "atomic-publication-test"
        candidate_identity["value"] = candidate.lstat()
    elif boundary == "lock-published":
        assert authoritative.is_dir()
        assert not list(locks.glob(cockpit_control.LOCK_CANDIDATE_PREFIX + "*"))
        owner = json.loads(
            (authoritative / cockpit_control.LOCK_OWNER_NAME).read_text()
        )
        assert owner == lock.owner
        assert cockpit_control._same_filesystem_identity(
            authoritative.lstat(), candidate_identity["value"]
        )
        published_identity["value"] = authoritative.lstat()
    elif boundary == "release-quarantined":
        assert not authoritative.exists()
        quarantines = list(locks.glob(cockpit_control.LOCK_RELEASED_PREFIX + "*"))
        assert quarantines == [lock._quarantine_path]
        owner = json.loads(
            (quarantines[0] / cockpit_control.LOCK_OWNER_NAME).read_text()
        )
        cockpit_control._validate_lock_owner(owner)
        assert cockpit_control._same_filesystem_identity(
            quarantines[0].lstat(), published_identity["value"]
        )

cockpit_control._lock_transition_fault = fault
lock = cockpit_control.PortableControlLock(
    root, "atomic-publication-test", timeout_seconds=0.5, poll_seconds=0.01
)
lock.acquire()
assert authoritative.is_dir()
lock.release()

assert not authoritative.exists()
assert not list(locks.glob(cockpit_control.LOCK_CANDIDATE_PREFIX + "*"))
assert not list(locks.glob(cockpit_control.LOCK_RELEASED_PREFIX + "*"))
guard = locks / cockpit_control.CONTROL_GUARD_NAME
assert guard.is_file() and not guard.is_symlink()
assert stat.S_IMODE(guard.stat().st_mode) & 0o077 == 0
assert boundaries == [
    "candidate-directory-created",
    "candidate-prepared",
    "acquire-guard-held",
    "lock-published-before-observed",
    "lock-published",
    "release-guard-held",
    "release-validated",
    "release-quarantined",
]
print("complete owner publication and quarantine-first release verified")
' "$root" "$BATS_TEST_DIRNAME/../../bin"
	[ "$status" -eq 0 ]
	echo "$output" | grep -Fq "complete owner publication and quarantine-first release verified"
}

@test "a real contending process times out boundedly and removes only its candidate" {
	local root="$BATS_TEST_TMPDIR/two-process-contention"
	export COCKPIT_CONTROL_ROOT="$root"
	"$CONTROL_BIN" init >/dev/null

	run python3 -c '
import json
import subprocess
import sys
import time
from pathlib import Path

root = Path(sys.argv[1])
module_dir = sys.argv[2]
sys.path.insert(0, module_dir)
import cockpit_control

holder_code = r"""
import sys
from pathlib import Path
sys.path.insert(0, sys.argv[2])
import cockpit_control
root = Path(sys.argv[1])
with cockpit_control.PortableControlLock(
    root, "holder-process", timeout_seconds=1, poll_seconds=0.01
) as lock:
    print("HOLDER_READY " + lock.owner["lock_id"], flush=True)
    if sys.stdin.readline().strip() != "release":
        raise RuntimeError("holder release barrier was not received")
print("HOLDER_RELEASED", flush=True)
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
    ready = holder.stdout.readline().strip()
    assert ready.startswith("HOLDER_READY "), holder.stderr.read()
    holder_id = ready.split(" ", 1)[1]
    authoritative = root / "locks" / cockpit_control.CONTROL_LOCK_NAME
    holder_identity = authoritative.lstat()
    holder_owner = json.loads(
        (authoritative / cockpit_control.LOCK_OWNER_NAME).read_text()
    )
    assert holder_owner["lock_id"] == holder_id

    candidate_observed = []
    def fault(boundary, lock):
        if boundary == "candidate-prepared":
            candidate = lock._candidate_path
            assert candidate.is_dir()
            owner = json.loads(
                (candidate / cockpit_control.LOCK_OWNER_NAME).read_text()
            )
            cockpit_control._validate_lock_owner(owner)
            assert owner["command"] == "contending-process"
            candidate_observed.append(candidate)

    cockpit_control._lock_transition_fault = fault
    contender = cockpit_control.PortableControlLock(
        root, "contending-process", timeout_seconds=0.15, poll_seconds=0.01
    )
    started = time.monotonic()
    try:
        contender.acquire()
    except cockpit_control.ControlStoreError as error:
        elapsed = time.monotonic() - started
        assert "timed out after 0.15s waiting for locks/control.lock" in str(error)
        assert elapsed < 2.0
    else:
        raise AssertionError("contender unexpectedly acquired the held lock")

    assert len(candidate_observed) == 1
    assert not candidate_observed[0].exists()
    assert not list(
        (root / "locks").glob(cockpit_control.LOCK_CANDIDATE_PREFIX + "*")
    )
    assert cockpit_control._same_filesystem_identity(
        authoritative.lstat(), holder_identity
    )
    assert json.loads(
        (authoritative / cockpit_control.LOCK_OWNER_NAME).read_text()
    ) == holder_owner
finally:
    if holder.poll() is None:
        holder.stdin.write("release\n")
        holder.stdin.flush()
        released = holder.stdout.readline().strip()
        assert released == "HOLDER_RELEASED", holder.stderr.read()
    holder.wait(timeout=5)
    assert holder.returncode == 0, holder.stderr.read()

assert not (root / "locks" / cockpit_control.CONTROL_LOCK_NAME).exists()
print("two-process timeout cleaned only the contender candidate")
' "$root" "$BATS_TEST_DIRNAME/../../bin"
	[ "$status" -eq 0 ]
	echo "$output" | grep -Fq "two-process timeout cleaned only the contender candidate"

	# TH3.E1.US6 liveness: a later writer completes a real mutation after the bounded acquisition timeout.
	run "$CONTROL_BIN" publish-event --type liveness-after-interruption
	[ "$status" -eq 0 ]
	echo "$output" | grep -Fq "at revision 1"
}

@test "the kernel releases the transition guard when its process dies" {
	local root="$BATS_TEST_TMPDIR/guard-process-death"
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

publisher_code = r"""
import json
import sys
from pathlib import Path
sys.path.insert(0, sys.argv[2])
import cockpit_control
def stop_after_publication(boundary, lock):
    if boundary == "lock-published":
        owner_path = lock.path / cockpit_control.LOCK_OWNER_NAME
        owner = json.loads(owner_path.read_text())
        assert owner == lock.owner
        print("LOCK_PUBLISHED_UNDER_GUARD", flush=True)
        sys.stdin.readline()
cockpit_control._lock_transition_fault = stop_after_publication
cockpit_control.PortableControlLock(
    Path(sys.argv[1]),
    "publisher-killed-under-guard",
    timeout_seconds=1,
    poll_seconds=0.01,
).acquire()
"""

child = subprocess.Popen(
    [sys.executable, "-c", publisher_code, str(root), module_dir],
    stdin=subprocess.PIPE,
    stdout=subprocess.PIPE,
    stderr=subprocess.PIPE,
    text=True,
    bufsize=1,
)
try:
    assert (
        child.stdout.readline().strip() == "LOCK_PUBLISHED_UNDER_GUARD"
    ), child.stderr.read()
    authoritative = root / "locks" / cockpit_control.CONTROL_LOCK_NAME
    observed = authoritative.lstat()
    owner = cockpit_control._validate_lock_owner(
        cockpit_control._load_json(
            authoritative / cockpit_control.LOCK_OWNER_NAME,
            "published owner",
        )
    )
    assert owner["pid"] == child.pid
    try:
        cockpit_control.ControlTransitionGuard(
            root / cockpit_control.LOCKS_DIR_NAME,
            timeout_seconds=0,
            poll_seconds=0.01,
        ).acquire()
    except cockpit_control.ControlStoreError as error:
        assert "timed out after 0s waiting for locks/control.guard" in str(error)
    else:
        raise AssertionError("a second process bypassed the held transition guard")

    child.kill()
    child.wait(timeout=5)
    assert child.returncode != 0
    with cockpit_control.ControlTransitionGuard(
        root / cockpit_control.LOCKS_DIR_NAME,
        timeout_seconds=0.5,
        poll_seconds=0.01,
    ):
        assert cockpit_control._same_filesystem_identity(
            authoritative.lstat(), observed
        )
        assert cockpit_control._load_json(
            authoritative / cockpit_control.LOCK_OWNER_NAME,
            "published owner after process death",
        ) == owner
finally:
    if child.poll() is None:
        child.kill()
        child.wait(timeout=5)

cockpit_control._cleanup_owned_directory(
    authoritative,
    owner,
    observed,
    "test interrupted published lock",
)
print("process death released the kernel transition guard")
' "$root" "$BATS_TEST_DIRNAME/../../bin"
	[ "$status" -eq 0 ]
	echo "$output" | grep -Fq "process death released the kernel transition guard"

	# TH3.E1.US6 liveness: a later writer completes a real mutation after the kernel-released transition guard.
	run "$CONTROL_BIN" publish-event --type liveness-after-interruption
	[ "$status" -eq 0 ]
	echo "$output" | grep -Fq "at revision 1"
}

@test "exact release fails closed when the authoritative path has a replacement owner" {
	local root="$BATS_TEST_TMPDIR/replacement-release"
	export COCKPIT_CONTROL_ROOT="$root"
	"$CONTROL_BIN" init >/dev/null

	run python3 -c '
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, sys.argv[2])
import cockpit_control

root = Path(sys.argv[1])
locks = root / cockpit_control.LOCKS_DIR_NAME
authoritative = locks / cockpit_control.CONTROL_LOCK_NAME
lock = cockpit_control.PortableControlLock(
    root, "original-owner", timeout_seconds=0.5, poll_seconds=0.01
).acquire()
original_owner = dict(lock.owner)
original_identity = lock.observed
displaced = locks / "displaced-original-lock"
os.rename(str(authoritative), str(displaced))

replacement_owner = cockpit_control._new_lock_owner("replacement-owner")
authoritative.mkdir(mode=0o700)
cockpit_control._write_json(
    authoritative / cockpit_control.LOCK_OWNER_NAME, replacement_owner
)
cockpit_control._fsync_directory(authoritative)
replacement_identity = authoritative.lstat()

try:
    lock.release()
except cockpit_control.ControlStoreError as error:
    assert "changed filesystem identity; replacement retained" in str(error)
else:
    raise AssertionError("exact release removed a replacement owner")

assert cockpit_control._same_filesystem_identity(
    authoritative.lstat(), replacement_identity
)
assert json.loads(
    (authoritative / cockpit_control.LOCK_OWNER_NAME).read_text()
)["lock_id"] == replacement_owner["lock_id"]
assert displaced.is_dir()
assert not list(locks.glob(cockpit_control.LOCK_RELEASED_PREFIX + "*"))

cockpit_control._cleanup_owned_directory(
    authoritative,
    replacement_owner,
    replacement_identity,
    "test replacement lock",
)
cockpit_control._cleanup_owned_directory(
    displaced,
    original_owner,
    original_identity,
    "test displaced original lock",
)

uuid_lock = cockpit_control.PortableControlLock(
    root, "uuid-checked-owner", timeout_seconds=0.5, poll_seconds=0.01
).acquire()
uuid_identity = uuid_lock.observed
uuid_replacement = cockpit_control._new_lock_owner("same-directory-replacement")
(authoritative / cockpit_control.LOCK_OWNER_NAME).unlink()
cockpit_control._write_json(
    authoritative / cockpit_control.LOCK_OWNER_NAME,
    uuid_replacement,
)
try:
    uuid_lock.release()
except cockpit_control.ControlStoreError as error:
    assert "has another owner UUID; replacement retained" in str(error)
else:
    raise AssertionError("release ignored replacement owner UUID")
assert cockpit_control._same_filesystem_identity(
    authoritative.lstat(), uuid_identity
)
assert json.loads(
    (authoritative / cockpit_control.LOCK_OWNER_NAME).read_text()
)["lock_id"] == uuid_replacement["lock_id"]
cockpit_control._cleanup_owned_directory(
    authoritative,
    uuid_replacement,
    uuid_identity,
    "test same-directory replacement lock",
)
print("exact release retained the replacement owner")
' "$root" "$BATS_TEST_DIRNAME/../../bin"
	[ "$status" -eq 0 ]
	echo "$output" | grep -Fq "exact release retained the replacement owner"
}

@test "acquisition error unwind removes exact ownership and preserves a replacement" {
	local cleanup_root="$BATS_TEST_TMPDIR/error-cleanup"
	local replacement_root="$BATS_TEST_TMPDIR/error-replacement"
	COCKPIT_CONTROL_ROOT="$cleanup_root" "$CONTROL_BIN" init >/dev/null
	COCKPIT_CONTROL_ROOT="$replacement_root" "$CONTROL_BIN" init >/dev/null

	run python3 -c '
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, sys.argv[3])
import cockpit_control

cleanup_root = Path(sys.argv[1])
replacement_root = Path(sys.argv[2])

class InjectedPublicationError(RuntimeError):
    pass

def fail_after_publish(boundary, lock):
    if boundary == "lock-published":
        raise InjectedPublicationError("injected after atomic lock publication")

cockpit_control._lock_transition_fault = fail_after_publish
try:
    cockpit_control.PortableControlLock(
        cleanup_root,
        "publication-error-owner",
        timeout_seconds=0.5,
        poll_seconds=0.01,
    ).acquire()
except InjectedPublicationError as error:
    assert "injected after atomic lock publication" in str(error)
else:
    raise AssertionError("publication fault was not surfaced")

cleanup_locks = cleanup_root / cockpit_control.LOCKS_DIR_NAME
assert not (cleanup_locks / cockpit_control.CONTROL_LOCK_NAME).exists()
assert not list(cleanup_locks.glob(cockpit_control.LOCK_CANDIDATE_PREFIX + "*"))
assert not list(cleanup_locks.glob(cockpit_control.LOCK_RELEASED_PREFIX + "*"))

evidence = {}
def replace_then_fail(boundary, lock):
    if boundary != "lock-published":
        return
    locks = replacement_root / cockpit_control.LOCKS_DIR_NAME
    authoritative = locks / cockpit_control.CONTROL_LOCK_NAME
    displaced = locks / "failed-publisher-original"
    evidence["original_owner"] = dict(lock.owner)
    evidence["original_identity"] = lock.observed
    evidence["displaced"] = displaced
    os.rename(str(authoritative), str(displaced))

    replacement_owner = cockpit_control._new_lock_owner(
        "replacement-during-error-unwind"
    )
    authoritative.mkdir(mode=0o700)
    cockpit_control._write_json(
        authoritative / cockpit_control.LOCK_OWNER_NAME,
        replacement_owner,
    )
    cockpit_control._fsync_directory(authoritative)
    evidence["replacement_owner"] = replacement_owner
    evidence["replacement_identity"] = authoritative.lstat()
    raise InjectedPublicationError("injected after replacement publication")

cockpit_control._lock_transition_fault = replace_then_fail
try:
    cockpit_control.PortableControlLock(
        replacement_root,
        "failing-original-owner",
        timeout_seconds=0.5,
        poll_seconds=0.01,
    ).acquire()
except cockpit_control.ControlStoreError as error:
    assert "safe unwind was incomplete" in str(error)
    assert "changed filesystem identity; replacement retained" in str(error)
else:
    raise AssertionError("replacement interleaving was not surfaced")

locks = replacement_root / cockpit_control.LOCKS_DIR_NAME
authoritative = locks / cockpit_control.CONTROL_LOCK_NAME
assert cockpit_control._same_filesystem_identity(
    authoritative.lstat(), evidence["replacement_identity"]
)
assert json.loads(
    (authoritative / cockpit_control.LOCK_OWNER_NAME).read_text()
)["lock_id"] == evidence["replacement_owner"]["lock_id"]
assert not list(locks.glob(cockpit_control.LOCK_CANDIDATE_PREFIX + "*"))
assert not list(locks.glob(cockpit_control.LOCK_RELEASED_PREFIX + "*"))

cockpit_control._cleanup_owned_directory(
    authoritative,
    evidence["replacement_owner"],
    evidence["replacement_identity"],
    "test replacement after unwind",
)
cockpit_control._cleanup_owned_directory(
    evidence["displaced"],
    evidence["original_owner"],
    evidence["original_identity"],
    "test displaced failed publisher",
)
print("error unwind cleaned exact ownership and retained replacement ownership")
' "$cleanup_root" "$replacement_root" "$BATS_TEST_DIRNAME/../../bin"
	[ "$status" -eq 0 ]
	echo "$output" | grep -Fq "error unwind cleaned exact ownership and retained replacement ownership"
}

@test "acquisition unwind preserves a same-UUID new-inode replacement before observation" {
	local root="$BATS_TEST_TMPDIR/same-uuid-replacement"
	export COCKPIT_CONTROL_ROOT="$root"
	"$CONTROL_BIN" init >/dev/null

	run python3 -c '
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, sys.argv[2])
import cockpit_control

root = Path(sys.argv[1])
locks = root / cockpit_control.LOCKS_DIR_NAME
authoritative = locks / cockpit_control.CONTROL_LOCK_NAME
evidence = {}

class InjectedPreObservationError(RuntimeError):
    pass

def replace_before_observation(boundary, lock):
    if boundary != "lock-published-before-observed":
        return
    assert lock.owner is None
    assert lock.observed is None

    original_owner = json.loads(
        (authoritative / cockpit_control.LOCK_OWNER_NAME).read_text()
    )
    original_identity = authoritative.lstat()
    displaced = locks / "displaced-original-before-observation"
    os.rename(str(authoritative), str(displaced))

    authoritative.mkdir(mode=0o700)
    cockpit_control._write_json(
        authoritative / cockpit_control.LOCK_OWNER_NAME,
        original_owner,
    )
    cockpit_control._fsync_directory(authoritative)
    replacement_identity = authoritative.lstat()
    assert not cockpit_control._same_filesystem_identity(
        replacement_identity,
        original_identity,
    )
    evidence.update(
        original_owner=original_owner,
        original_identity=original_identity,
        displaced=displaced,
        replacement_identity=replacement_identity,
    )
    raise InjectedPreObservationError(
        "injected after same-UUID replacement before observation"
    )

cockpit_control._lock_transition_fault = replace_before_observation
lock = cockpit_control.PortableControlLock(
    root,
    "same-uuid-original-publisher",
    timeout_seconds=0.5,
    poll_seconds=0.01,
)
try:
    lock.acquire()
except cockpit_control.ControlStoreError as error:
    assert "safe unwind was incomplete" in str(error)
    assert "changed filesystem identity; replacement retained" in str(error)
    assert "injected after same-UUID replacement before observation" in str(error)
else:
    raise AssertionError("same-UUID replacement interleaving was not surfaced")

assert lock.owner is None
assert lock.observed is None
assert cockpit_control._same_filesystem_identity(
    authoritative.lstat(),
    evidence["replacement_identity"],
)
replacement_owner = json.loads(
    (authoritative / cockpit_control.LOCK_OWNER_NAME).read_text()
)
assert replacement_owner["lock_id"] == evidence["original_owner"]["lock_id"]
assert cockpit_control._same_filesystem_identity(
    evidence["displaced"].lstat(),
    evidence["original_identity"],
)
assert not list(locks.glob(cockpit_control.LOCK_CANDIDATE_PREFIX + "*"))
assert not list(locks.glob(cockpit_control.LOCK_RELEASED_PREFIX + "*"))

cockpit_control._cleanup_owned_directory(
    authoritative,
    evidence["original_owner"],
    evidence["replacement_identity"],
    "test same-UUID replacement",
)
cockpit_control._cleanup_owned_directory(
    evidence["displaced"],
    evidence["original_owner"],
    evidence["original_identity"],
    "test displaced original publisher",
)
print("same-UUID new-inode replacement was retained before observation")
' "$root" "$BATS_TEST_DIRNAME/../../bin"
	[ "$status" -eq 0 ]
	echo "$output" | grep -Fq "same-UUID new-inode replacement was retained before observation"
}

@test "post-publication guard release failure reacquires the guard and unwinds ownership" {
	local root="$BATS_TEST_TMPDIR/guard-release-unwind"
	export COCKPIT_CONTROL_ROOT="$root"
	"$CONTROL_BIN" init >/dev/null

	run python3 -c '
import sys
from pathlib import Path

sys.path.insert(0, sys.argv[2])
import cockpit_control

root = Path(sys.argv[1])
locks = root / cockpit_control.LOCKS_DIR_NAME
authoritative = locks / cockpit_control.CONTROL_LOCK_NAME
real_release = cockpit_control.ControlTransitionGuard.release
release_calls = []

class InjectedGuardReleaseError(RuntimeError):
    pass

def fail_first_release_after_close(guard):
    real_release(guard)
    assert guard._descriptor is None
    release_calls.append(guard.path)
    if len(release_calls) == 1:
        raise InjectedGuardReleaseError(
            "injected transition guard release failure after close"
        )

cockpit_control.ControlTransitionGuard.release = fail_first_release_after_close
lock = cockpit_control.PortableControlLock(
    root,
    "guard-release-failure-owner",
    timeout_seconds=0.5,
    poll_seconds=0.01,
)
try:
    try:
        lock.acquire()
    except InjectedGuardReleaseError as error:
        assert "failure after close" in str(error)
    else:
        raise AssertionError("guard release failure was not surfaced")
finally:
    cockpit_control.ControlTransitionGuard.release = real_release

assert len(release_calls) == 2
assert lock.owner is None
assert lock.observed is None
assert not authoritative.exists()
assert not list(locks.glob(cockpit_control.LOCK_CANDIDATE_PREFIX + "*"))
assert not list(locks.glob(cockpit_control.LOCK_RELEASED_PREFIX + "*"))

probe = cockpit_control.PortableControlLock(
    root,
    "post-unwind-probe",
    timeout_seconds=0.5,
    poll_seconds=0.01,
).acquire()
probe.release()
assert not authoritative.exists()
print("post-publication guard release failure safely unwound exact ownership")
' "$root" "$BATS_TEST_DIRNAME/../../bin"
	[ "$status" -eq 0 ]
	echo "$output" | grep -Fq "post-publication guard release failure safely unwound exact ownership"
}

@test "repeated EINTR observes the guard deadline and returns the visible timeout" {
	local root="$BATS_TEST_TMPDIR/guard-eintr-timeout"
	export COCKPIT_CONTROL_ROOT="$root"
	"$CONTROL_BIN" init >/dev/null

	run python3 -c '
import errno
import sys
from pathlib import Path

sys.path.insert(0, sys.argv[2])
import cockpit_control

root = Path(sys.argv[1])
locks = root / cockpit_control.LOCKS_DIR_NAME
real_flock = cockpit_control.fcntl.flock
real_monotonic = cockpit_control.time.monotonic
clock = iter((100.0, 100.05, 100.19, 100.21))
attempts = []

def interrupted_flock(descriptor, operation):
    assert operation == cockpit_control.fcntl.LOCK_EX | cockpit_control.fcntl.LOCK_NB
    attempts.append(descriptor)
    raise OSError(errno.EINTR, "injected repeated interrupt")

cockpit_control.fcntl.flock = interrupted_flock
cockpit_control.time.monotonic = lambda: next(clock)
guard = cockpit_control.ControlTransitionGuard(
    locks,
    timeout_seconds=0.2,
    poll_seconds=0.01,
)
try:
    try:
        guard.acquire()
    except cockpit_control.ControlStoreError as error:
        assert str(error) == (
            "timed out after 0.2s waiting for locks/control.guard"
        )
    else:
        raise AssertionError("repeated EINTR bypassed the guard deadline")
finally:
    cockpit_control.fcntl.flock = real_flock
    cockpit_control.time.monotonic = real_monotonic

assert len(attempts) == 3
assert guard._descriptor is None
with cockpit_control.ControlTransitionGuard(
    locks,
    timeout_seconds=0,
    poll_seconds=0.01,
):
    pass
print("repeated EINTR returned the bounded visible guard timeout")
' "$root" "$BATS_TEST_DIRNAME/../../bin"
	[ "$status" -eq 0 ]
	echo "$output" | grep -Fq "repeated EINTR returned the bounded visible guard timeout"
}

@test "guarded repair quarantines a proven-dead same-host owner and frees acquisition" {
	local root="$BATS_TEST_TMPDIR/repair-dead-owner"
	export COCKPIT_CONTROL_ROOT="$root"
	"$CONTROL_BIN" init >/dev/null

	run python3 -c '
import json
import subprocess
import sys
from pathlib import Path

root = Path(sys.argv[1])
module_dir = sys.argv[2]
sys.path.insert(0, module_dir)
import cockpit_control

holder_code = r"""
import json
import sys
from pathlib import Path
sys.path.insert(0, sys.argv[2])
import cockpit_control
def park(boundary, lock):
    if boundary == "lock-published":
        print("HOLDER_PUBLISHED " + json.dumps(lock.owner), flush=True)
        sys.stdin.readline()
cockpit_control._lock_transition_fault = park
cockpit_control.PortableControlLock(
    Path(sys.argv[1]), "crashed-owner", timeout_seconds=2, poll_seconds=0.01
).acquire()
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
    published = holder.stdout.readline().strip()
    assert published.startswith("HOLDER_PUBLISHED "), holder.stderr.read()
    dead_owner = json.loads(published.split(" ", 1)[1])
finally:
    holder.kill()
    holder.wait(timeout=5)
assert dead_owner["pid"] == holder.pid

locks = root / cockpit_control.LOCKS_DIR_NAME
authoritative = locks / cockpit_control.CONTROL_LOCK_NAME
stale_identity = authoritative.lstat()
assert json.loads(
    (authoritative / cockpit_control.LOCK_OWNER_NAME).read_text()
) == dead_owner
state, reason = cockpit_control._prove_lock_owner_death(dead_owner)
assert state == cockpit_control.LOCK_OWNER_DEAD, reason

boundaries = []
def observe(boundary, repair):
    boundaries.append(boundary)
    if boundary == "repair-validated":
        assert authoritative.is_dir()
        assert repair.owner == dead_owner
        assert not list(locks.glob(cockpit_control.LOCK_REPAIRED_PREFIX + "*"))
    elif boundary == "repair-quarantined":
        assert not authoritative.exists()
        quarantines = list(locks.glob(cockpit_control.LOCK_REPAIRED_PREFIX + "*"))
        assert quarantines == [repair._quarantine_path]

cockpit_control._lock_transition_fault = observe

preview = cockpit_control.repair_stale_control_lock(
    root, timeout_seconds=0.5, poll_seconds=0.01, dry_run=True
)
assert preview.outcome == cockpit_control.LOCK_REPAIR_WOULD_QUARANTINE
assert preview.repaired is False
assert preview.lock_id == dead_owner["lock_id"]
assert preview.quarantine_path is None
assert cockpit_control._same_filesystem_identity(
    authoritative.lstat(), stale_identity
)
assert not list(locks.glob(cockpit_control.LOCK_REPAIRED_PREFIX + "*"))

result = cockpit_control.repair_stale_control_lock(
    root, timeout_seconds=0.5, poll_seconds=0.01
)
assert result.repaired is True
assert result.outcome == cockpit_control.LOCK_REPAIR_QUARANTINED
assert result.lock_id == dead_owner["lock_id"]
assert "proven same-host owner death" in result.reason
assert boundaries == [
    "repair-guard-held",
    "repair-validated",
    "repair-guard-held",
    "repair-validated",
    "repair-quarantined",
]

quarantine = result.quarantine_path
assert quarantine.name.startswith(
    cockpit_control.LOCK_REPAIRED_PREFIX + dead_owner["lock_id"] + "-"
)
assert not authoritative.exists()
assert cockpit_control._same_filesystem_identity(quarantine.lstat(), stale_identity)
assert json.loads(
    (quarantine / cockpit_control.LOCK_OWNER_NAME).read_text()
) == dead_owner
assert not list(locks.glob(cockpit_control.LOCK_CANDIDATE_PREFIX + "*"))
assert not list(locks.glob(cockpit_control.LOCK_RELEASED_PREFIX + "*"))

def silent(boundary, lock):
    return None

cockpit_control._lock_transition_fault = silent
with cockpit_control.PortableControlLock(
    root, "post-repair-writer", timeout_seconds=0.5, poll_seconds=0.01
) as new_lock:
    assert authoritative.is_dir()
    assert new_lock.owner["lock_id"] != dead_owner["lock_id"]
assert not authoritative.exists()

again = cockpit_control.repair_stale_control_lock(
    root, timeout_seconds=0.5, poll_seconds=0.01
)
assert again.outcome == cockpit_control.LOCK_REPAIR_ABSENT
assert again.repaired is False
assert again.quarantine_path is None
assert list(locks.glob(cockpit_control.LOCK_REPAIRED_PREFIX + "*")) == [quarantine]
print("guarded repair quarantined the proven-dead owner and freed acquisition")
' "$root" "$BATS_TEST_DIRNAME/../../bin"
	[ "$status" -eq 0 ]
	echo "$output" | grep -Fq "guarded repair quarantined the proven-dead owner and freed acquisition"
}

@test "repair killed after the quarantine rename leaves evidence and permits acquisition" {
	local root="$BATS_TEST_TMPDIR/repair-crash-after-rename"
	export COCKPIT_CONTROL_ROOT="$root"
	"$CONTROL_BIN" init >/dev/null

	run python3 -c '
import json
import subprocess
import sys
from pathlib import Path

root = Path(sys.argv[1])
module_dir = sys.argv[2]
sys.path.insert(0, module_dir)
import cockpit_control

locks = root / cockpit_control.LOCKS_DIR_NAME
authoritative = locks / cockpit_control.CONTROL_LOCK_NAME

probe = subprocess.Popen([sys.executable, "-c", "pass"])
probe.wait(timeout=5)
stale_owner = cockpit_control._new_lock_owner("owner-that-already-exited")
stale_owner["pid"] = probe.pid
stale_owner = cockpit_control._validate_lock_owner(stale_owner)
authoritative.mkdir(mode=0o700)
cockpit_control._write_json(
    authoritative / cockpit_control.LOCK_OWNER_NAME, stale_owner
)
cockpit_control._fsync_directory(authoritative)
stale_identity = authoritative.lstat()
assert cockpit_control._prove_lock_owner_death(stale_owner)[0] == (
    cockpit_control.LOCK_OWNER_DEAD
)

repair_code = r"""
import sys
from pathlib import Path
sys.path.insert(0, sys.argv[2])
import cockpit_control
def park(boundary, repair):
    if boundary == "repair-quarantined":
        print("REPAIR_QUARANTINED " + repair._quarantine_path.name, flush=True)
        sys.stdin.readline()
        raise AssertionError("the repair barrier was released instead of killed")
cockpit_control._lock_transition_fault = park
cockpit_control.repair_stale_control_lock(
    Path(sys.argv[1]), timeout_seconds=5, poll_seconds=0.01
)
"""

repairer = subprocess.Popen(
    [sys.executable, "-c", repair_code, str(root), module_dir],
    stdin=subprocess.PIPE,
    stdout=subprocess.PIPE,
    stderr=subprocess.PIPE,
    text=True,
    bufsize=1,
)
try:
    parked = repairer.stdout.readline().strip()
    assert parked.startswith("REPAIR_QUARANTINED "), repairer.stderr.read()
    quarantine_name = parked.split(" ", 1)[1]
finally:
    repairer.kill()
    repairer.wait(timeout=5)
assert repairer.returncode != 0

quarantine = locks / quarantine_name
assert not authoritative.exists()
assert quarantine.is_dir()
assert list(locks.glob(cockpit_control.LOCK_REPAIRED_PREFIX + "*")) == [quarantine]
assert cockpit_control._same_filesystem_identity(quarantine.lstat(), stale_identity)
assert json.loads(
    (quarantine / cockpit_control.LOCK_OWNER_NAME).read_text()
) == stale_owner
assert not list(locks.glob(cockpit_control.LOCK_CANDIDATE_PREFIX + "*"))
assert not list(locks.glob(cockpit_control.LOCK_RELEASED_PREFIX + "*"))

writer_code = r"""
import sys
from pathlib import Path
sys.path.insert(0, sys.argv[2])
import cockpit_control
with cockpit_control.PortableControlLock(
    Path(sys.argv[1]), "writer-after-killed-repair", timeout_seconds=2, poll_seconds=0.01
) as lock:
    print("WRITER_ACQUIRED " + lock.owner["lock_id"], flush=True)
print("WRITER_RELEASED", flush=True)
"""

writer = subprocess.run(
    [sys.executable, "-c", writer_code, str(root), module_dir],
    stdout=subprocess.PIPE,
    stderr=subprocess.PIPE,
    text=True,
    timeout=30,
)
assert writer.returncode == 0, writer.stderr
lines = writer.stdout.split()
assert lines[0] == "WRITER_ACQUIRED", writer.stdout
assert lines[2] == "WRITER_RELEASED", writer.stdout
assert lines[1] != stale_owner["lock_id"]

assert not authoritative.exists()
assert list(locks.glob(cockpit_control.LOCK_REPAIRED_PREFIX + "*")) == [quarantine]
assert json.loads(
    (quarantine / cockpit_control.LOCK_OWNER_NAME).read_text()
) == stale_owner
print("killed repair retained quarantine evidence and permitted a new writer")
' "$root" "$BATS_TEST_DIRNAME/../../bin"
	[ "$status" -eq 0 ]
	echo "$output" | grep -Fq "killed repair retained quarantine evidence and permitted a new writer"

	# TH3.E1.US6 liveness: a later writer completes a real mutation after the abandoned repair quarantine.
	run "$CONTROL_BIN" publish-event --type liveness-after-interruption
	[ "$status" -eq 0 ]
	echo "$output" | grep -Fq "at revision 1"
}

@test "repair killed before the quarantine rename leaves the exact lock unchanged" {
	local root="$BATS_TEST_TMPDIR/repair-crash-before-rename"
	export COCKPIT_CONTROL_ROOT="$root"
	"$CONTROL_BIN" init >/dev/null

	run python3 -c '
import json
import subprocess
import sys
from pathlib import Path

root = Path(sys.argv[1])
module_dir = sys.argv[2]
sys.path.insert(0, module_dir)
import cockpit_control

locks = root / cockpit_control.LOCKS_DIR_NAME
authoritative = locks / cockpit_control.CONTROL_LOCK_NAME

probe = subprocess.Popen([sys.executable, "-c", "pass"])
probe.wait(timeout=5)
stale_owner = cockpit_control._new_lock_owner("owner-that-already-exited")
stale_owner["pid"] = probe.pid
stale_owner = cockpit_control._validate_lock_owner(stale_owner)
authoritative.mkdir(mode=0o700)
cockpit_control._write_json(
    authoritative / cockpit_control.LOCK_OWNER_NAME, stale_owner
)
cockpit_control._fsync_directory(authoritative)
stale_identity = authoritative.lstat()

repair_code = r"""
import sys
from pathlib import Path
sys.path.insert(0, sys.argv[2])
import cockpit_control
def park(boundary, repair):
    if boundary == "repair-validated":
        print("REPAIR_VALIDATED " + repair.owner["lock_id"], flush=True)
        sys.stdin.readline()
        raise AssertionError("the repair barrier was released instead of killed")
cockpit_control._lock_transition_fault = park
cockpit_control.repair_stale_control_lock(
    Path(sys.argv[1]), timeout_seconds=5, poll_seconds=0.01
)
"""

repairer = subprocess.Popen(
    [sys.executable, "-c", repair_code, str(root), module_dir],
    stdin=subprocess.PIPE,
    stdout=subprocess.PIPE,
    stderr=subprocess.PIPE,
    text=True,
    bufsize=1,
)
try:
    parked = repairer.stdout.readline().strip()
    assert parked == "REPAIR_VALIDATED " + stale_owner["lock_id"], (
        repairer.stderr.read()
    )
finally:
    repairer.kill()
    repairer.wait(timeout=5)
assert repairer.returncode != 0

assert authoritative.is_dir()
assert cockpit_control._same_filesystem_identity(
    authoritative.lstat(), stale_identity
)
assert json.loads(
    (authoritative / cockpit_control.LOCK_OWNER_NAME).read_text()
) == stale_owner
assert not list(locks.glob(cockpit_control.LOCK_REPAIRED_PREFIX + "*"))
assert not list(locks.glob(cockpit_control.LOCK_RELEASED_PREFIX + "*"))
assert not list(locks.glob(cockpit_control.LOCK_CANDIDATE_PREFIX + "*"))

resumed = cockpit_control.repair_stale_control_lock(
    root, timeout_seconds=1, poll_seconds=0.01
)
assert resumed.repaired is True
assert resumed.lock_id == stale_owner["lock_id"]
assert not authoritative.exists()
assert cockpit_control._same_filesystem_identity(
    resumed.quarantine_path.lstat(), stale_identity
)
assert json.loads(
    (resumed.quarantine_path / cockpit_control.LOCK_OWNER_NAME).read_text()
) == stale_owner
print("repair killed before the rename left the exact lock recoverable")
' "$root" "$BATS_TEST_DIRNAME/../../bin"
	[ "$status" -eq 0 ]
	echo "$output" | grep -Fq "repair killed before the rename left the exact lock recoverable"

	# TH3.E1.US6 liveness: a later writer completes a real mutation after the interrupted repair validation.
	run "$CONTROL_BIN" publish-event --type liveness-after-interruption
	[ "$status" -eq 0 ]
	echo "$output" | grep -Fq "at revision 1"
}

@test "repair and acquisition cannot interleave under the shared transition guard" {
	local root="$BATS_TEST_TMPDIR/repair-acquire-interleaving"
	export COCKPIT_CONTROL_ROOT="$root"
	"$CONTROL_BIN" init >/dev/null

	run python3 -c '
import json
import subprocess
import sys
from pathlib import Path

root = Path(sys.argv[1])
module_dir = sys.argv[2]
sys.path.insert(0, module_dir)
import cockpit_control

locks = root / cockpit_control.LOCKS_DIR_NAME
authoritative = locks / cockpit_control.CONTROL_LOCK_NAME

probe = subprocess.Popen([sys.executable, "-c", "pass"])
probe.wait(timeout=5)
stale_owner = cockpit_control._new_lock_owner("owner-that-already-exited")
stale_owner["pid"] = probe.pid
stale_owner = cockpit_control._validate_lock_owner(stale_owner)
authoritative.mkdir(mode=0o700)
cockpit_control._write_json(
    authoritative / cockpit_control.LOCK_OWNER_NAME, stale_owner
)
cockpit_control._fsync_directory(authoritative)
stale_identity = authoritative.lstat()

repair_code = r"""
import sys
from pathlib import Path
sys.path.insert(0, sys.argv[2])
import cockpit_control
def park(boundary, repair):
    if boundary == "repair-validated":
        print("REPAIR_VALIDATED " + repair.owner["lock_id"], flush=True)
        if sys.stdin.readline().strip() != "go":
            raise RuntimeError("the repair barrier was not received")
cockpit_control._lock_transition_fault = park
result = cockpit_control.repair_stale_control_lock(
    Path(sys.argv[1]), timeout_seconds=10, poll_seconds=0.01
)
print("REPAIR_DONE " + result.quarantine_path.name, flush=True)
"""

repairer = subprocess.Popen(
    [sys.executable, "-c", repair_code, str(root), module_dir],
    stdin=subprocess.PIPE,
    stdout=subprocess.PIPE,
    stderr=subprocess.PIPE,
    text=True,
    bufsize=1,
)
try:
    parked = repairer.stdout.readline().strip()
    assert parked == "REPAIR_VALIDATED " + stale_owner["lock_id"], (
        repairer.stderr.read()
    )

    try:
        cockpit_control.PortableControlLock(
            root, "writer-blocked-by-repair", timeout_seconds=0.2, poll_seconds=0.01
        ).acquire()
    except cockpit_control.ControlStoreError as error:
        message = str(error)
        assert message.startswith("timed out after "), message
        assert message.endswith("waiting for locks/control.guard"), message
    else:
        raise AssertionError("acquisition bypassed the repair transition guard")

    try:
        cockpit_control.repair_stale_control_lock(
            root, timeout_seconds=0.2, poll_seconds=0.01
        )
    except cockpit_control.ControlStoreError as error:
        assert "timed out after 0.2s waiting for locks/control.guard" in str(error)
    else:
        raise AssertionError("a second repair bypassed the repair transition guard")

    assert cockpit_control._same_filesystem_identity(
        authoritative.lstat(), stale_identity
    )
    assert not list(locks.glob(cockpit_control.LOCK_CANDIDATE_PREFIX + "*"))
    assert not list(locks.glob(cockpit_control.LOCK_REPAIRED_PREFIX + "*"))

    repairer.stdin.write("go\n")
    repairer.stdin.flush()
    finished = repairer.stdout.readline().strip()
    assert finished.startswith("REPAIR_DONE "), repairer.stderr.read()
    quarantine = locks / finished.split(" ", 1)[1]
finally:
    repairer.stdin.close()
    try:
        repairer.wait(timeout=10)
    except subprocess.TimeoutExpired:
        repairer.kill()
        repairer.wait(timeout=5)
assert repairer.returncode == 0, repairer.stderr.read()

assert not authoritative.exists()
assert cockpit_control._same_filesystem_identity(quarantine.lstat(), stale_identity)
assert json.loads(
    (quarantine / cockpit_control.LOCK_OWNER_NAME).read_text()
) == stale_owner

with cockpit_control.PortableControlLock(
    root, "writer-after-repair", timeout_seconds=1, poll_seconds=0.01
) as lock:
    assert authoritative.is_dir()
    assert lock.owner["lock_id"] != stale_owner["lock_id"]
print("the shared transition guard serialized repair against acquisition")
' "$root" "$BATS_TEST_DIRNAME/../../bin"
	[ "$status" -eq 0 ]
	echo "$output" | grep -Fq "the shared transition guard serialized repair against acquisition"

	# TH3.E1.US6 liveness: a later writer completes a real mutation after the guarded repair/acquisition interleaving.
	run "$CONTROL_BIN" publish-event --type liveness-after-interruption
	[ "$status" -eq 0 ]
	echo "$output" | grep -Fq "at revision 1"
}

@test "repair refuses a live same-host owner even with explicit authorization" {
	local root="$BATS_TEST_TMPDIR/repair-live-owner"
	export COCKPIT_CONTROL_ROOT="$root"
	"$CONTROL_BIN" init >/dev/null

	run python3 -c '
import json
import subprocess
import sys
from pathlib import Path

root = Path(sys.argv[1])
module_dir = sys.argv[2]
sys.path.insert(0, module_dir)
import cockpit_control

holder_code = r"""
import json
import sys
from pathlib import Path
sys.path.insert(0, sys.argv[2])
import cockpit_control
with cockpit_control.PortableControlLock(
    Path(sys.argv[1]), "live-owner", timeout_seconds=2, poll_seconds=0.01
) as lock:
    print("HOLDER_READY " + json.dumps(lock.owner), flush=True)
    if sys.stdin.readline().strip() != "release":
        raise RuntimeError("the holder release barrier was not received")
print("HOLDER_RELEASED", flush=True)
"""

locks = root / cockpit_control.LOCKS_DIR_NAME
authoritative = locks / cockpit_control.CONTROL_LOCK_NAME
holder = subprocess.Popen(
    [sys.executable, "-c", holder_code, str(root), module_dir],
    stdin=subprocess.PIPE,
    stdout=subprocess.PIPE,
    stderr=subprocess.PIPE,
    text=True,
    bufsize=1,
)
try:
    ready = holder.stdout.readline().strip()
    assert ready.startswith("HOLDER_READY "), holder.stderr.read()
    live_owner = json.loads(ready.split(" ", 1)[1])
    live_identity = authoritative.lstat()
    assert cockpit_control._prove_lock_owner_death(live_owner)[0] == (
        cockpit_control.LOCK_OWNER_ALIVE
    )

    for authorization in (None, live_owner["lock_id"]):
        try:
            cockpit_control.repair_stale_control_lock(
                root,
                authorized_lock_id=authorization,
                timeout_seconds=0.5,
                poll_seconds=0.01,
            )
        except cockpit_control.ControlStoreError as error:
            assert "refusing to repair a live locks/control.lock" in str(error)
            assert "is alive" in str(error)
            assert "lock retained" in str(error)
        else:
            raise AssertionError("repair moved a live same-host owner")

        preview = None
        try:
            preview = cockpit_control.repair_stale_control_lock(
                root,
                authorized_lock_id=authorization,
                timeout_seconds=0.5,
                poll_seconds=0.01,
                dry_run=True,
            )
        except cockpit_control.ControlStoreError as error:
            assert "refusing to repair a live locks/control.lock" in str(error)
        else:
            raise AssertionError("a dry run approved a live same-host owner")
        assert preview is None

    assert cockpit_control._same_filesystem_identity(
        authoritative.lstat(), live_identity
    )
    assert json.loads(
        (authoritative / cockpit_control.LOCK_OWNER_NAME).read_text()
    ) == live_owner
    assert not list(locks.glob(cockpit_control.LOCK_REPAIRED_PREFIX + "*"))

    assert cockpit_control._prove_lock_owner_death(
        cockpit_control._new_lock_owner("this-very-process")
    )[0] == cockpit_control.LOCK_OWNER_ALIVE
finally:
    if holder.poll() is None:
        holder.stdin.write("release\n")
        holder.stdin.flush()
        assert holder.stdout.readline().strip() == "HOLDER_RELEASED", (
            holder.stderr.read()
        )
    holder.wait(timeout=5)
assert holder.returncode == 0, holder.stderr.read()
assert not authoritative.exists()
print("repair refused the live same-host owner and retained the lock")
' "$root" "$BATS_TEST_DIRNAME/../../bin"
	[ "$status" -eq 0 ]
	echo "$output" | grep -Fq "repair refused the live same-host owner and retained the lock"
}

@test "repair fails closed for remote malformed and mis-authorized owners" {
	local root="$BATS_TEST_TMPDIR/repair-fails-closed"
	export COCKPIT_CONTROL_ROOT="$root"
	"$CONTROL_BIN" init >/dev/null

	run python3 -c '
import json
import os
import shutil
import sys
from pathlib import Path
from uuid import uuid4

root = Path(sys.argv[1])
sys.path.insert(0, sys.argv[2])
import cockpit_control

locks = root / cockpit_control.LOCKS_DIR_NAME
authoritative = locks / cockpit_control.CONTROL_LOCK_NAME

def publish(command, host=None, pid=None):
    owner = cockpit_control._new_lock_owner(command)
    if host is not None:
        owner["host"] = host
    if pid is not None:
        owner["pid"] = pid
    owner = cockpit_control._validate_lock_owner(owner)
    authoritative.mkdir(mode=0o700)
    cockpit_control._write_json(
        authoritative / cockpit_control.LOCK_OWNER_NAME, owner
    )
    cockpit_control._fsync_directory(authoritative)
    return owner, authoritative.lstat()

def refuses(expected, **kwargs):
    identity = authoritative.lstat()
    before = (authoritative / cockpit_control.LOCK_OWNER_NAME).read_bytes()
    try:
        cockpit_control.repair_stale_control_lock(
            root, timeout_seconds=0.5, poll_seconds=0.01, **kwargs
        )
    except cockpit_control.ControlStoreError as error:
        assert expected in str(error), str(error)
    else:
        raise AssertionError("repair did not fail closed for: " + expected)
    assert cockpit_control._same_filesystem_identity(
        authoritative.lstat(), identity
    )
    assert (authoritative / cockpit_control.LOCK_OWNER_NAME).read_bytes() == before
    assert not list(locks.glob(cockpit_control.LOCK_REPAIRED_PREFIX + "*"))

# A remote owner is never proven dead, even when its PID matches a live local one.
remote_owner, remote_identity = publish(
    "remote-owner", host="another-host.example", pid=os.getpid()
)
assert cockpit_control._prove_lock_owner_death(remote_owner)[0] == (
    cockpit_control.LOCK_OWNER_UNPROVEN
)
refuses("refusing to repair locks/control.lock without proof of owner death")
refuses("same-host process death cannot be proven")
refuses("re-run with explicit authorization for lock " + remote_owner["lock_id"])
refuses("repair authorization names lock", authorized_lock_id=str(uuid4()))
refuses("control lock repair requires UUID authorized_lock_id", authorized_lock_id="not-a-uuid")
refuses("control lock repair requires non-empty authorized_lock_id", authorized_lock_id="")

# Explicit guarded authorization for the exact owner UUID repairs the same lock.
authorized = cockpit_control.repair_stale_control_lock(
    root,
    authorized_lock_id=remote_owner["lock_id"],
    timeout_seconds=0.5,
    poll_seconds=0.01,
)
assert authorized.repaired is True
assert "explicit guarded authorization" in authorized.reason
assert cockpit_control._same_filesystem_identity(
    authorized.quarantine_path.lstat(), remote_identity
)
assert json.loads(
    (authorized.quarantine_path / cockpit_control.LOCK_OWNER_NAME).read_text()
) == remote_owner
shutil.rmtree(str(authorized.quarantine_path))

# Malformed owner metadata is never repaired, with or without authorization.
malformed_owner = publish("malformed-owner")[0]
(authoritative / cockpit_control.LOCK_OWNER_NAME).write_text("{ not json")
refuses("malformed locks/control.lock/owner.json")
refuses(
    "malformed locks/control.lock/owner.json",
    authorized_lock_id=malformed_owner["lock_id"],
)
(authoritative / cockpit_control.LOCK_OWNER_NAME).write_text(
    json.dumps({"schema_version": 1, "record_type": "control-lock"})
)
refuses("locks/control.lock/owner.json requires non-empty lock_id")
(authoritative / cockpit_control.LOCK_OWNER_NAME).unlink()
identity = authoritative.lstat()
try:
    cockpit_control.repair_stale_control_lock(
        root, timeout_seconds=0.5, poll_seconds=0.01
    )
except cockpit_control.ControlStoreError as error:
    assert "malformed locks/control.lock: expected exactly owner.json" in str(error)
else:
    raise AssertionError("repair claimed an owner-less lock directory")
assert cockpit_control._same_filesystem_identity(authoritative.lstat(), identity)
assert not list(locks.glob(cockpit_control.LOCK_REPAIRED_PREFIX + "*"))
authoritative.rmdir()

# Repair of an absent lock is idempotent and never creates state.
absent = cockpit_control.repair_stale_control_lock(
    root, timeout_seconds=0.5, poll_seconds=0.01
)
assert absent.outcome == cockpit_control.LOCK_REPAIR_ABSENT
assert absent.repaired is False
assert sorted(entry.name for entry in locks.iterdir()) == [
    cockpit_control.CONTROL_GUARD_NAME
]
print("repair failed closed for every ambiguous owner")
' "$root" "$BATS_TEST_DIRNAME/../../bin"
	[ "$status" -eq 0 ]
	echo "$output" | grep -Fq "repair failed closed for every ambiguous owner"
}

@test "repair-lock reports guarded outcomes and fail-closed refusals on stderr" {
	local root="$BATS_TEST_TMPDIR/repair-lock-cli"
	export COCKPIT_CONTROL_ROOT="$root"
	"$CONTROL_BIN" init >/dev/null

	run "$CONTROL_BIN" repair-lock
	[ "$status" -eq 0 ]
	echo "$output" | grep -Fq "no locks/control.lock to repair in $root"

	run python3 -c '
import subprocess
import sys
from pathlib import Path

root = Path(sys.argv[1])
sys.path.insert(0, sys.argv[2])
import cockpit_control

authoritative = root / cockpit_control.LOCKS_DIR_NAME / cockpit_control.CONTROL_LOCK_NAME
probe = subprocess.Popen([sys.executable, "-c", "pass"])
probe.wait(timeout=5)
owner = cockpit_control._new_lock_owner("cli-stale-owner")
owner["pid"] = probe.pid
owner = cockpit_control._validate_lock_owner(owner)
authoritative.mkdir(mode=0o700)
cockpit_control._write_json(authoritative / cockpit_control.LOCK_OWNER_NAME, owner)
cockpit_control._fsync_directory(authoritative)
print(owner["lock_id"])
' "$root" "$BATS_TEST_DIRNAME/../../bin"
	[ "$status" -eq 0 ]
	local stale_id="$output"

	run "$CONTROL_BIN" repair-lock --dry-run
	[ "$status" -eq 0 ]
	echo "$output" | grep -Fq "would quarantine control lock $stale_id"
	echo "$output" | grep -Fq "no state changed"
	[ -d "$root/locks/control.lock" ]

	run "$CONTROL_BIN" repair-lock --authorize "00000000-0000-4000-8000-000000000000"
	[ "$status" -eq 1 ]
	echo "$output" | grep -Fq "cockpit-control: repair authorization names lock"
	[ -d "$root/locks/control.lock" ]

	run "$CONTROL_BIN" repair-lock
	[ "$status" -eq 0 ]
	echo "$output" | grep -Fq "quarantined control lock $stale_id as locks/control.lock.repaired-$stale_id-"
	[ ! -e "$root/locks/control.lock" ]
	[ -n "$(find "$root/locks" -maxdepth 1 -name "control.lock.repaired-$stale_id-*" -type d)" ]

	run "$CONTROL_BIN" repair-lock
	[ "$status" -eq 0 ]
	echo "$output" | grep -Fq "no locks/control.lock to repair in $root"

	export COCKPIT_CONTROL_ROOT=""
	run "$CONTROL_BIN" repair-lock
	[ "$status" -ne 0 ]
	echo "$output" | grep -Fq "COCKPIT_CONTROL_ROOT is empty"
}

@test "one atomic rename commits a validated private candidate and advances one revision" {
	local root="$BATS_TEST_TMPDIR/event-commit"
	export COCKPIT_CONTROL_ROOT="$root"
	"$CONTROL_BIN" init >/dev/null

	run python3 -c '
import json
import sys
from pathlib import Path

sys.path.insert(0, sys.argv[2])
import cockpit_control

root = Path(sys.argv[1])
events = root / cockpit_control.EVENTS_DIR_NAME
pending = root / cockpit_control.PENDING_DIR_NAME
locks = root / cockpit_control.LOCKS_DIR_NAME
authoritative = locks / cockpit_control.CONTROL_LOCK_NAME
control_id = json.loads((root / cockpit_control.CONTROL_METADATA_NAME).read_text())["control_id"]
ledger_before = (root / cockpit_control.LEDGER_NAME).read_bytes()
compatibility_view_before = (root / cockpit_control.EVENTS_NAME).read_bytes()

boundaries = []
candidate = {}

def fault(boundary, publication):
    boundaries.append(boundary)
    assert authoritative.is_dir(), "event publication must hold the control lock"
    owner = json.loads((authoritative / cockpit_control.LOCK_OWNER_NAME).read_text())
    assert owner["command"] == "commit-boundary-test"
    if boundary == "revision-allocated":
        assert publication.revision == 1
        assert list(events.iterdir()) == []
        assert list(pending.iterdir()) == []
    elif boundary == "candidate-written":
        private = publication.candidate_path
        assert list(pending.iterdir()) == [private]
        assert list(events.iterdir()) == [], "nothing is authoritative before the rename"
        expected = cockpit_control._serialized_record(publication.record).encode("utf-8")
        assert private.read_bytes() == expected
        candidate["identity"] = private.lstat()
        candidate["bytes"] = expected
        candidate["name"] = private.name
    elif boundary == "event-committed":
        committed = publication.committed_path
        assert publication.candidate_path is None
        assert list(pending.iterdir()) == []
        assert list(events.iterdir()) == [committed]
        assert committed.name == candidate["name"]
        assert committed.read_bytes() == candidate["bytes"]
        assert cockpit_control._same_filesystem_identity(
            committed.lstat(), candidate["identity"]
        ), "the exact validated candidate inode must be the committed event"
        assert (root / cockpit_control.LEDGER_NAME).read_bytes() == ledger_before, (
            "the rename alone commits the event; the projection follows it"
        )

cockpit_control._event_publication_fault = fault
first = cockpit_control.publish_control_event(
    root,
    "mission-dispatched",
    actor="overseer",
    payload={"queue_item_id": "QI-1"},
    command="commit-boundary-test",
    timeout_seconds=1,
    poll_seconds=0.01,
)
assert boundaries == [
    "revision-allocated",
    "candidate-written",
    "candidate-validated",
    "event-committed",
], boundaries
assert first.committed
assert first.revision == 1
assert first.pending_debris == ()
assert first.path.name == cockpit_control._event_filename(1, first.event_id)
assert not authoritative.exists(), "the control lock is released after publication"
assert sorted(entry.name for entry in locks.iterdir()) == [
    cockpit_control.CONTROL_GUARD_NAME
]

next_boundaries = []

def observe(boundary, publication):
    next_boundaries.append(boundary)
    assert publication.revision == 2, publication.revision

cockpit_control._event_publication_fault = observe
second = cockpit_control.publish_control_event(
    root,
    "mission-completed",
    actor="overseer",
    command="commit-boundary-test",
    timeout_seconds=1,
    poll_seconds=0.01,
)
assert next_boundaries == boundaries, next_boundaries
assert second.revision == 2, second.revision
assert second.event_id != first.event_id

history = cockpit_control.read_committed_events(root, control_id)
assert history.latest_revision == 2
assert [event.revision for event in history.events] == [1, 2]
assert history.pending == ()
assert [event.path.name for event in history.events] == sorted(
    entry.name for entry in events.iterdir()
), "committed names sort in revision order"
for event in history.events:
    named_revision, named_event_id = cockpit_control._parse_event_filename(
        event.path.name, event.path.name
    )
    assert named_revision == event.record["revision"] == event.revision
    assert named_event_id == event.record["event_id"] == event.event_id
    assert event.record["control_id"] == control_id
    assert event.record["schema_version"] == cockpit_control.CONTROL_SCHEMA_VERSION
assert history.events[0].record["payload"] == {"queue_item_id": "QI-1"}

assert second.projection is not None and second.projection.rebuilt
assert second.projection.revision == 2

# The projection is derived: it equals a fresh rebuild from committed events.
metadata = cockpit_control.validate_root_metadata(
    json.loads((root / cockpit_control.CONTROL_METADATA_NAME).read_text()), root
)
rebuilt = cockpit_control.build_ledger_projection(metadata, history.events)
ledger_after = (root / cockpit_control.LEDGER_NAME).read_bytes()
assert ledger_after != ledger_before, "the committed event advanced the projection"
assert ledger_after == cockpit_control._serialized_record(rebuilt).encode("utf-8")
assert json.loads(ledger_after.decode("utf-8"))["revision"] == 2
assert json.loads(ledger_after.decode("utf-8"))["updated_at"] == (
    history.events[-1].record["timestamp"]
)
view_after = (root / cockpit_control.EVENTS_NAME).read_bytes()
assert view_after != compatibility_view_before
assert view_after == cockpit_control.build_events_view(history.events).encode("utf-8")
assert not (root / cockpit_control.LEDGER_TEMPORARY_NAME).exists()
assert not (root / cockpit_control.EVENTS_VIEW_TEMPORARY_NAME).exists()
print("one atomic rename per revision verified")
' "$root" "$BATS_TEST_DIRNAME/../../bin"
	[ "$status" -eq 0 ]
	echo "$output" | grep -Fq "one atomic rename per revision verified"

	run "$CONTROL_BIN" validate
	[ "$status" -eq 0 ]

	run "$CONTROL_BIN" list-events
	[ "$status" -eq 0 ]
	echo "$output" | grep -Fq "2 committed events in $root (latest revision 2)"
	echo "$output" | grep -q "^events/000000000001-.*revision 1 type mission-dispatched actor overseer$"
	echo "$output" | grep -q "^events/000000000002-.*revision 2 type mission-completed actor overseer$"
}

@test "a writer killed during private publication leaves reported debris that never commits" {
	local root="$BATS_TEST_TMPDIR/event-torn-candidate"
	export COCKPIT_CONTROL_ROOT="$root"
	"$CONTROL_BIN" init >/dev/null

	run python3 -c '
import json
import subprocess
import sys
from pathlib import Path

root = Path(sys.argv[1])
module_dir = sys.argv[2]
sys.path.insert(0, module_dir)
import cockpit_control

events = root / cockpit_control.EVENTS_DIR_NAME
pending = root / cockpit_control.PENDING_DIR_NAME
authoritative = root / cockpit_control.LOCKS_DIR_NAME / cockpit_control.CONTROL_LOCK_NAME
control_id = json.loads((root / cockpit_control.CONTROL_METADATA_NAME).read_text())["control_id"]

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
    "interrupted-publication",
    actor="killed-writer",
    command="killed-writer",
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

debris = pending / torn[1]
assert debris.is_file()
assert debris.read_bytes() == bytes.fromhex(torn[2])
assert 0 < debris.stat().st_size < 64, "the killed writer left an incomplete candidate"
try:
    json.loads(debris.read_text())
except json.JSONDecodeError:
    pass
else:
    raise AssertionError("the torn candidate was unexpectedly complete JSON")

interrupted = cockpit_control.read_committed_events(root, control_id)
assert list(events.iterdir()) == []
assert interrupted.events == ()
assert interrupted.latest_revision == 0, "a private candidate never advances the revision"
assert interrupted.pending == (debris,)
assert authoritative.is_dir(), "the killed writer still holds the control lock"

repair = cockpit_control.repair_stale_control_lock(root, timeout_seconds=5, poll_seconds=0.01)
assert repair.repaired

published = cockpit_control.publish_control_event(
    root,
    "publication-after-debris",
    actor="overseer",
    command="writer-after-debris",
    timeout_seconds=5,
    poll_seconds=0.01,
)
assert published.committed
assert published.revision == 1, "the debris did not consume a revision"
assert published.pending_debris == (debris,), "publication reports retained debris"
assert published.path != debris
assert published.path.name != debris.name

assert debris.is_file(), "debris is retained as diagnosable evidence"
assert debris.read_bytes() == bytes.fromhex(torn[2])
after = cockpit_control.read_committed_events(root, control_id)
assert after.latest_revision == 1
assert after.pending == (debris,)
assert [event.path for event in after.events] == [published.path]
print("killed private publication left reported non-authoritative debris")
' "$root" "$BATS_TEST_DIRNAME/../../bin"
	[ "$status" -eq 0 ]
	echo "$output" | grep -Fq "killed private publication left reported non-authoritative debris"

	run "$CONTROL_BIN" list-events
	[ "$status" -eq 0 ]
	echo "$output" | grep -Fq "1 committed events in $root (latest revision 1)"
	echo "$output" | grep -q "^cockpit-control: pending/000000000001-.* is non-authoritative debris from an interrupted publication; it is retained for diagnosis$"

	run "$CONTROL_BIN" validate
	[ "$status" -eq 0 ]
	echo "$output" | grep -Fq "is non-authoritative debris"
	[ "$(find "$root/pending" -type f | wc -l)" -eq 1 ]
}

@test "a committed revision gap fails closed names the missing revision and blocks mutation" {
	local root="$BATS_TEST_TMPDIR/event-revision-gap"
	export COCKPIT_CONTROL_ROOT="$root"
	"$CONTROL_BIN" init >/dev/null

	run "$CONTROL_BIN" publish-event --type first --actor overseer
	[ "$status" -eq 0 ]
	run "$CONTROL_BIN" publish-event --type second --actor overseer
	[ "$status" -eq 0 ]
	run "$CONTROL_BIN" publish-event --type third --actor overseer
	[ "$status" -eq 0 ]
	echo "$output" | grep -Fq "at revision 3"

	local second
	second="$(find "$root/events" -maxdepth 1 -name '000000000002-*.json')"
	[ -n "$second" ]
	local removed="$BATS_TEST_TMPDIR/removed-revision-2.json"
	mv "$second" "$removed"
	[ "$(find "$root/events" -maxdepth 1 -type f | wc -l)" -eq 2 ]

	run "$CONTROL_BIN" list-events
	[ "$status" -eq 1 ]
	echo "$output" | grep -Fq "events is missing revision 2"
	echo "$output" | grep -Fq "cannot become authority until the gap is repaired"

	run "$CONTROL_BIN" publish-event --type blocked-by-gap
	[ "$status" -eq 1 ]
	echo "$output" | grep -Fq "events is missing revision 2"
	[ "$(find "$root/events" -maxdepth 1 -type f | wc -l)" -eq 2 ]
	[ "$(find "$root/pending" -type f | wc -l)" -eq 0 ]

	run "$CONTROL_BIN" publish-event --type blocked-by-gap --dry-run
	[ "$status" -eq 1 ]
	echo "$output" | grep -Fq "events is missing revision 2"

	run "$CONTROL_BIN" validate
	[ "$status" -eq 1 ]
	echo "$output" | grep -Fq "events is missing revision 2"

	run "$OVERSEER_BIN" start
	[ "$status" -ne 0 ]
	echo "$output" | grep -Fq "missing revision 2"

	[ ! -e "$root/locks/control.lock" ]
	[ -z "$(find "$root/locks" -maxdepth 1 -name 'control.lock.*')" ]

	mv "$removed" "$second"
	run "$CONTROL_BIN" list-events
	[ "$status" -eq 0 ]
	echo "$output" | grep -Fq "3 committed events in $root (latest revision 3)"

	run "$CONTROL_BIN" publish-event --type after-repair
	[ "$status" -eq 0 ]
	echo "$output" | grep -Fq "at revision 4"
}

@test "committed name revision and identifier disagreements fail closed without mutation" {
	local root="$BATS_TEST_TMPDIR/event-identity"
	export COCKPIT_CONTROL_ROOT="$root"
	"$CONTROL_BIN" init >/dev/null

	run python3 -c '
import json
import sys
from pathlib import Path
from uuid import uuid4

sys.path.insert(0, sys.argv[2])
import cockpit_control

root = Path(sys.argv[1])
events = root / cockpit_control.EVENTS_DIR_NAME
pending = root / cockpit_control.PENDING_DIR_NAME
locks = root / cockpit_control.LOCKS_DIR_NAME
control_id = json.loads((root / cockpit_control.CONTROL_METADATA_NAME).read_text())["control_id"]

def record(revision, event_id, **overrides):
    event = {
        "schema_version": cockpit_control.CONTROL_SCHEMA_VERSION,
        "record_type": "event",
        "event_id": event_id,
        "control_id": control_id,
        "timestamp": cockpit_control.utc_timestamp(),
        "revision": revision,
        "event_type": "crafted",
        "actor": "test",
        "payload": {},
    }
    event.update(overrides)
    return event

def place(name, event):
    (events / name).write_text(json.dumps(event, indent=2, sort_keys=True) + "\n")

def reset():
    for entry in list(events.iterdir()) + list(pending.iterdir()):
        if entry.is_dir() and not entry.is_symlink():
            entry.rmdir()
        else:
            entry.unlink()

def refuses(case, fragment):
    try:
        cockpit_control.read_committed_events(root, control_id)
    except cockpit_control.ControlStoreError as error:
        assert fragment in str(error), (case, str(error))
    else:
        raise AssertionError("discovery accepted " + case)
    try:
        cockpit_control.publish_control_event(
            root, "blocked-mutation", command="identity-test",
            timeout_seconds=1, poll_seconds=0.01,
        )
    except cockpit_control.ControlStoreError as error:
        assert fragment in str(error), (case, str(error))
    else:
        raise AssertionError("publication accepted " + case)
    assert list(pending.iterdir()) == [], case
    assert sorted(entry.name for entry in locks.iterdir()) == [
        cockpit_control.CONTROL_GUARD_NAME
    ], case
    reset()

first_id = str(uuid4())
second_id = str(uuid4())

place(cockpit_control._event_filename(1, first_id), record(2, first_id))
refuses("declared revision mismatch", "declares revision 2 but its filename commits revision 1")

place(cockpit_control._event_filename(1, first_id), record(1, second_id))
refuses("declared event_id mismatch", "declares event_id " + second_id)

place(cockpit_control._event_filename(1, first_id), record(1, first_id))
place(cockpit_control._event_filename(1, second_id), record(1, second_id))
refuses("duplicate revision", "duplicates revision 1")

place(cockpit_control._event_filename(1, first_id), record(1, first_id))
place(cockpit_control._event_filename(2, first_id), record(2, first_id))
refuses("duplicate event_id", "duplicates event_id " + first_id)

place(cockpit_control._event_filename(1, first_id), record(1, first_id))
place(cockpit_control._event_filename(3, second_id), record(3, second_id))
refuses("revision gap", "is missing revision 2")

place(cockpit_control._event_filename(2, first_id), record(2, first_id))
refuses("first revision missing", "is missing revision 1")

place("1-" + first_id + ".json", record(1, first_id))
refuses("unpadded revision", "is not a committed event")

place("00000000000x-" + first_id + ".json", record(1, first_id))
refuses("non-numeric revision", "does not name a zero-padded revision")

place("000000000000-" + first_id + ".json", record(1, first_id))
refuses("zero revision", "does not name a positive revision")

place("000000000001-not-a-uuid.json", record(1, first_id))
refuses("non-uuid name", "does not name a UUID event_id")

place("000000000001-" + first_id.upper() + ".json", record(1, first_id))
refuses("uppercase uuid name", "does not name a canonical lowercase UUID event_id")

place("." + cockpit_control._event_filename(1, first_id), record(1, first_id))
refuses("hidden entry", "is not a committed event")

(events / "notes.txt").write_text("not an event\n")
refuses("unsupported extension", "is not a committed event")

place(cockpit_control._event_filename(1, first_id), record(1, first_id, control_id=str(uuid4())))
refuses("foreign control_id", "control_id does not match")

place(
    cockpit_control._event_filename(1, first_id),
    record(1, first_id, schema_version=cockpit_control.CONTROL_SCHEMA_VERSION + 1),
)
refuses("future schema", "unsupported future schema_version")

(events / cockpit_control._event_filename(1, first_id)).symlink_to(root / cockpit_control.LEDGER_NAME)
refuses("symlinked event", "must be a regular file")

(events / cockpit_control._event_filename(1, first_id)).mkdir()
refuses("directory event", "must be a regular file")

assert list(events.iterdir()) == []
assert cockpit_control.read_committed_events(root, control_id).latest_revision == 0
print("committed identity disagreements failed closed")
' "$root" "$BATS_TEST_DIRNAME/../../bin"
	[ "$status" -eq 0 ]
	echo "$output" | grep -Fq "committed identity disagreements failed closed"
}

@test "event publication dry run and malformed payloads change no committed state" {
	local root="$BATS_TEST_TMPDIR/event-dry-run"
	export COCKPIT_CONTROL_ROOT="$root"
	"$CONTROL_BIN" init >/dev/null

	run "$CONTROL_BIN" publish-event --type would-commit --dry-run
	[ "$status" -eq 0 ]
	echo "$output" | grep -Fq "would commit events/000000000001-"
	echo "$output" | grep -Fq "at revision 1; no state changed"
	[ "$(find "$root/events" -type f | wc -l)" -eq 0 ]
	[ "$(find "$root/pending" -type f | wc -l)" -eq 0 ]
	[ ! -e "$root/locks/control.lock" ]

	run "$CONTROL_BIN" publish-event --type broken-payload --payload 'not json'
	[ "$status" -eq 1 ]
	echo "$output" | grep -Fq "event payload is not valid JSON"
	run "$CONTROL_BIN" publish-event --type broken-payload --payload '[1, 2]'
	[ "$status" -eq 1 ]
	echo "$output" | grep -Fq "event payload must be a JSON object of structured metadata"
	run "$CONTROL_BIN" publish-event --type "   " --actor overseer
	[ "$status" -eq 1 ]
	echo "$output" | grep -Fq "event requires non-empty event_type"

	# A JSON integer literal longer than CPython's 4300-digit int->str
	# conversion limit raises a bare ValueError rather than a JSONDecodeError,
	# and a pathologically nested payload raises RecursionError.  Neither is a
	# decode error, so a decoder-only guard lets both escape as a traceback
	# leaking absolute internal paths.  Both must refuse as diagnostics.
	local huge_payload nested_payload
	huge_payload="$(python3 -c 'print("{\"a\":" + "9" * 5000 + "}")')"
	nested_payload="$(python3 -c 'print("{\"a\":" * 20000 + "1" + "}" * 20000)')"

	run "$CONTROL_BIN" publish-event --type huge-integer-payload --payload "$huge_payload"
	[ "$status" -eq 1 ]
	echo "$output" | grep -Fq "cockpit-control: event payload is not valid JSON"
	echo "$output" | grep -Fq "4300 digits"
	if echo "$output" | grep -q "Traceback (most recent call last)"; then return 1; fi
	if echo "$output" | grep -q "cockpit_control\.py"; then return 1; fi

	run "$CONTROL_BIN" publish-event --type nested-payload --payload "$nested_payload"
	[ "$status" -eq 1 ]
	echo "$output" | grep -Fq "cockpit-control: event payload is nested too deeply to parse"
	if echo "$output" | grep -q "Traceback (most recent call last)"; then return 1; fi
	if echo "$output" | grep -q "cockpit_control\.py"; then return 1; fi

	[ "$(find "$root/events" -type f | wc -l)" -eq 0 ]
	[ "$(find "$root/pending" -type f | wc -l)" -eq 0 ]

	run "$CONTROL_BIN" publish-event --type committed-once --payload '{"queue_item_id": "QI-7"}'
	[ "$status" -eq 0 ]
	echo "$output" | grep -Fq "committed events/000000000001-"
	[ "$(find "$root/events" -type f | wc -l)" -eq 1 ]

	local before
	before="$(cd "$root/events" && cksum ./*)"
	run "$CONTROL_BIN" publish-event --type would-commit-next --dry-run
	[ "$status" -eq 0 ]
	echo "$output" | grep -Fq "at revision 2; no state changed"
	[ "$(cd "$root/events" && cksum ./*)" = "$before" ]
	[ "$(find "$root/events" -type f | wc -l)" -eq 1 ]
}

@test "event publication waits for the control lock and commits nothing when it times out" {
	local root="$BATS_TEST_TMPDIR/event-lock-required"
	export COCKPIT_CONTROL_ROOT="$root"
	"$CONTROL_BIN" init >/dev/null

	run python3 -c '
import json
import subprocess
import sys
import time
from pathlib import Path

root = Path(sys.argv[1])
module_dir = sys.argv[2]
sys.path.insert(0, module_dir)
import cockpit_control

events = root / cockpit_control.EVENTS_DIR_NAME
pending = root / cockpit_control.PENDING_DIR_NAME
control_id = json.loads((root / cockpit_control.CONTROL_METADATA_NAME).read_text())["control_id"]

holder_code = r"""
import sys
from pathlib import Path
sys.path.insert(0, sys.argv[2])
import cockpit_control
with cockpit_control.PortableControlLock(
    Path(sys.argv[1]), "publication-holder", timeout_seconds=5, poll_seconds=0.01
) as lock:
    print("HOLDER_READY " + lock.owner["lock_id"], flush=True)
    if sys.stdin.readline().strip() != "release":
        raise RuntimeError("holder release barrier was not received")
print("HOLDER_RELEASED", flush=True)
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
    ready = holder.stdout.readline().strip()
    assert ready.startswith("HOLDER_READY "), holder.stderr.read()
    started = time.monotonic()
    try:
        cockpit_control.publish_control_event(
            root, "needs-the-lock", command="contending-publisher",
            timeout_seconds=0.15, poll_seconds=0.01,
        )
    except cockpit_control.ControlStoreError as error:
        assert "timed out after 0.15s waiting for locks/control.lock" in str(error), str(error)
        assert time.monotonic() - started < 2.0
    else:
        raise AssertionError("publication proceeded without the control lock")
    assert list(events.iterdir()) == []
    assert list(pending.iterdir()) == []
    assert cockpit_control.read_committed_events(root, control_id).latest_revision == 0
finally:
    if holder.poll() is None:
        holder.stdin.write("release\n")
        holder.stdin.flush()
        assert holder.stdout.readline().strip() == "HOLDER_RELEASED", holder.stderr.read()
    holder.wait(timeout=5)
    assert holder.returncode == 0, holder.stderr.read()

published = cockpit_control.publish_control_event(
    root, "after-the-holder", command="publisher-after-holder",
    timeout_seconds=5, poll_seconds=0.01,
)
assert published.committed
assert published.revision == 1
assert [entry.name for entry in events.iterdir()] == [published.path.name]
assert list(pending.iterdir()) == []
print("event publication required the control lock")
' "$root" "$BATS_TEST_DIRNAME/../../bin"
	[ "$status" -eq 0 ]
	echo "$output" | grep -Fq "event publication required the control lock"
}

@test "a writer killed after the event rename leaves one committed revision counted once" {
	local root="$BATS_TEST_TMPDIR/event-crash-after-rename"
	export COCKPIT_CONTROL_ROOT="$root"
	"$CONTROL_BIN" init >/dev/null

	run python3 -c '
import json
import subprocess
import sys
from pathlib import Path

root = Path(sys.argv[1])
module_dir = sys.argv[2]
sys.path.insert(0, module_dir)
import cockpit_control

events = root / cockpit_control.EVENTS_DIR_NAME
pending = root / cockpit_control.PENDING_DIR_NAME
authoritative = root / cockpit_control.LOCKS_DIR_NAME / cockpit_control.CONTROL_LOCK_NAME
control_id = json.loads((root / cockpit_control.CONTROL_METADATA_NAME).read_text())["control_id"]
ledger_before = (root / cockpit_control.LEDGER_NAME).read_bytes()

writer_code = r"""
import sys
from pathlib import Path
sys.path.insert(0, sys.argv[2])
import cockpit_control

def park(boundary, publication):
    if boundary == "event-committed":
        print("EVENT_COMMITTED " + publication.committed_path.name, flush=True)
        sys.stdin.readline()
        raise AssertionError("the commit barrier was released instead of killed")

cockpit_control._event_publication_fault = park
cockpit_control.publish_control_event(
    Path(sys.argv[1]),
    "committed-then-killed",
    actor="killed-writer",
    command="killed-after-rename",
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
    parked = writer.stdout.readline().split()
    assert parked and parked[0] == "EVENT_COMMITTED", writer.stderr.read()
finally:
    writer.kill()
    writer.wait(timeout=5)
assert writer.returncode != 0

committed = events / parked[1]
assert committed.is_file()
assert list(pending.iterdir()) == [], "the private candidate became the committed event"
history = cockpit_control.read_committed_events(root, control_id)
assert [event.path for event in history.events] == [committed]
assert history.latest_revision == 1, "the rename alone committed exactly one revision"
assert history.events[0].record["event_type"] == "committed-then-killed"
assert history.events[0].record["actor"] == "killed-writer"
assert history.pending == ()
committed_bytes = committed.read_bytes()
assert committed_bytes == cockpit_control._serialized_record(
    history.events[0].record
).encode("utf-8")
assert (root / cockpit_control.LEDGER_NAME).read_bytes() == ledger_before
assert authoritative.is_dir(), "the killed writer still holds the control lock"

assert cockpit_control.repair_stale_control_lock(
    root, timeout_seconds=5, poll_seconds=0.01
).repaired
published = cockpit_control.publish_control_event(
    root, "after-committed-crash", command="writer-after-commit-crash",
    timeout_seconds=5, poll_seconds=0.01,
)
assert published.revision == 2, "the committed event is counted exactly once"
assert committed.read_bytes() == committed_bytes, "committed events are immutable"
assert cockpit_control.read_committed_events(root, control_id).latest_revision == 2
print("commit-boundary crash left one immutable committed revision")
' "$root" "$BATS_TEST_DIRNAME/../../bin"
	[ "$status" -eq 0 ]
	echo "$output" | grep -Fq "commit-boundary crash left one immutable committed revision"

	run "$CONTROL_BIN" list-events
	[ "$status" -eq 0 ]
	echo "$output" | grep -Fq "2 committed events in $root (latest revision 2)"
	run "$CONTROL_BIN" validate
	[ "$status" -eq 0 ]
}

@test "a committed event atomically advances the derived ledger to its revision" {
	local root="$BATS_TEST_TMPDIR/ledger-projection"
	export COCKPIT_CONTROL_ROOT="$root"
	"$CONTROL_BIN" init >/dev/null

	run python3 -c '
import json
import sys
from pathlib import Path
from uuid import uuid4

sys.path.insert(0, sys.argv[2])
import cockpit_control

root = Path(sys.argv[1])
ledger_path = root / cockpit_control.LEDGER_NAME
ledger_temporary = root / cockpit_control.LEDGER_TEMPORARY_NAME
view_path = root / cockpit_control.EVENTS_NAME
view_temporary = root / cockpit_control.EVENTS_VIEW_TEMPORARY_NAME
locks = root / cockpit_control.LOCKS_DIR_NAME
authoritative = locks / cockpit_control.CONTROL_LOCK_NAME
metadata_path = root / cockpit_control.CONTROL_METADATA_NAME
control_id = json.loads(metadata_path.read_text())["control_id"]
mission_id = str(uuid4())

initial_ledger = ledger_path.read_bytes()
assert json.loads(initial_ledger.decode("utf-8"))["revision"] == 0
boundaries = []
observed = {}

def fault(boundary, projection):
    boundaries.append(boundary)
    assert authoritative.is_dir(), "the projection must hold the control lock"
    owner = json.loads((authoritative / cockpit_control.LOCK_OWNER_NAME).read_text())
    assert owner["command"] == "projection-boundary-test"
    if boundary == "projection-built":
        assert projection.revision == 1
        assert projection.observed_revision == 0
        assert projection.reason == cockpit_control.PROJECTION_REASON_STALE
        assert ledger_path.read_bytes() == initial_ledger
        assert not ledger_temporary.exists()
    elif boundary == "view-replaced":
        assert ledger_path.read_bytes() == initial_ledger, "the ledger is the watermark"
    elif boundary == "ledger-temporary-written":
        assert ledger_path.read_bytes() == initial_ledger, "nothing is replaced yet"
        observed["identity"] = ledger_temporary.lstat()
        observed["bytes"] = ledger_temporary.read_bytes()
        assert observed["bytes"] == cockpit_control._serialized_record(
            projection.record
        ).encode("utf-8")
    elif boundary == "ledger-replaced":
        assert not ledger_temporary.exists()
        assert ledger_path.read_bytes() == observed["bytes"]
        assert cockpit_control._same_filesystem_identity(
            ledger_path.lstat(), observed["identity"]
        ), "the exact flushed temporary inode became the ledger"

cockpit_control._ledger_projection_fault = fault
published = cockpit_control.publish_control_event(
    root,
    "mission-dispatched",
    actor="overseer",
    payload={"active_queue_item_id": "QI-9", "active_mission_id": mission_id},
    command="projection-boundary-test",
    timeout_seconds=5,
    poll_seconds=0.01,
)
assert boundaries == [
    "projection-built",
    "view-replaced",
    "ledger-temporary-written",
    "ledger-replaced",
], boundaries
assert published.committed
assert published.projection is not None
assert published.projection.rebuilt
assert published.projection.revision == published.revision == 1
assert not authoritative.exists(), "the control lock is released after the projection"
assert sorted(entry.name for entry in locks.iterdir()) == [cockpit_control.CONTROL_GUARD_NAME]

history = cockpit_control.read_committed_events(root, control_id)
metadata = cockpit_control.validate_root_metadata(json.loads(metadata_path.read_text()), root)
derived = cockpit_control.build_ledger_projection(metadata, history.events)
projected = ledger_path.read_bytes()
assert projected == cockpit_control._serialized_record(derived).encode("utf-8")
ledger = json.loads(projected.decode("utf-8"))
assert ledger["revision"] == history.latest_revision == 1
assert ledger["active_queue_item_id"] == "QI-9"
assert ledger["active_mission_id"] == mission_id
assert ledger["updated_at"] == history.events[0].record["timestamp"]
assert ledger["control_id"] == control_id
assert ledger["canonical_roots"] == metadata["canonical_roots"]
assert view_path.read_bytes() == cockpit_control.build_events_view(history.events).encode("utf-8")
assert not ledger_temporary.exists() and not view_temporary.exists()

cockpit_control._ledger_projection_fault = lambda boundary, projection: None
repeated = cockpit_control.replay_control_ledger(root, timeout_seconds=5, poll_seconds=0.01)
assert repeated.current, repeated.outcome
assert repeated.revision == 1
assert ledger_path.read_bytes() == projected, "replay is idempotent byte for byte"
assert cockpit_control.read_committed_events(root, control_id).latest_revision == 1, (
    "replay never commits an event"
)
print("the committed event advanced the derived ledger atomically")
' "$root" "$BATS_TEST_DIRNAME/../../bin"
	[ "$status" -eq 0 ]
	echo "$output" | grep -Fq "the committed event advanced the derived ledger atomically"

	run "$CONTROL_BIN" validate
	[ "$status" -eq 0 ]
	run "$CONTROL_BIN" replay-ledger
	[ "$status" -eq 0 ]
	echo "$output" | grep -Fq "ledger.json projection is current at revision 1 in $root"
}

@test "a writer killed during the ledger temporary write keeps committed events authoritative" {
	local root="$BATS_TEST_TMPDIR/ledger-crash-temporary"
	export COCKPIT_CONTROL_ROOT="$root"
	"$CONTROL_BIN" init >/dev/null

	run python3 -c '
import json
import subprocess
import sys
from pathlib import Path

root = Path(sys.argv[1])
module_dir = sys.argv[2]
sys.path.insert(0, module_dir)
import cockpit_control

ledger_path = root / cockpit_control.LEDGER_NAME
ledger_temporary = root / cockpit_control.LEDGER_TEMPORARY_NAME
events = root / cockpit_control.EVENTS_DIR_NAME
authoritative = root / cockpit_control.LOCKS_DIR_NAME / cockpit_control.CONTROL_LOCK_NAME
metadata_path = root / cockpit_control.CONTROL_METADATA_NAME
control_id = json.loads(metadata_path.read_text())["control_id"]
ledger_before = ledger_path.read_bytes()

writer_code = r"""
import sys
from pathlib import Path
sys.path.insert(0, sys.argv[2])
import cockpit_control

def park(boundary, projection):
    if boundary == "ledger-temporary-written":
        print("LEDGER_TEMPORARY_WRITTEN", flush=True)
        sys.stdin.readline()
        raise AssertionError("the projection barrier was released instead of killed")

cockpit_control._ledger_projection_fault = park
cockpit_control.publish_control_event(
    Path(sys.argv[1]),
    "committed-before-projection",
    actor="killed-writer",
    payload={"active_queue_item_id": "QI-11"},
    command="killed-during-ledger-temporary",
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
    parked = writer.stdout.readline().strip()
    assert parked == "LEDGER_TEMPORARY_WRITTEN", writer.stderr.read()
finally:
    writer.kill()
    writer.wait(timeout=5)
assert writer.returncode != 0

history = cockpit_control.read_committed_events(root, control_id)
assert history.latest_revision == 1, "the committed event is authority regardless of projection"
assert len(history.events) == 1
assert history.pending == ()
assert ledger_path.read_bytes() == ledger_before, "the interrupted projection replaced nothing"
assert json.loads(ledger_path.read_text())["revision"] == 0
assert ledger_temporary.is_file(), "the interrupted temporary is retained as debris"
metadata = cockpit_control.validate_root_metadata(json.loads(metadata_path.read_text()), root)
expected = cockpit_control._serialized_record(
    cockpit_control.build_ledger_projection(metadata, history.events)
).encode("utf-8")
assert ledger_temporary.read_bytes() == expected, "the temporary was flushed before the kill"
assert authoritative.is_dir(), "the killed writer still holds the control lock"

assert cockpit_control.repair_stale_control_lock(
    root, timeout_seconds=5, poll_seconds=0.01
).repaired
replayed = cockpit_control.replay_control_ledger(root, timeout_seconds=5, poll_seconds=0.01)
assert replayed.rebuilt, replayed.outcome
assert replayed.reason == cockpit_control.PROJECTION_REASON_STALE
assert replayed.observed_revision == 0
assert replayed.revision == 1
assert ledger_path.read_bytes() == expected
assert not ledger_temporary.exists()
assert cockpit_control.read_committed_events(root, control_id).latest_revision == 1, (
    "replay advanced the ledger without creating another event"
)

steady = cockpit_control.replay_control_ledger(root, timeout_seconds=5, poll_seconds=0.01)
assert steady.current, steady.outcome
assert ledger_path.read_bytes() == expected
assert len(cockpit_control.read_committed_events(root, control_id).events) == 1
print("the interrupted ledger temporary never overrode committed events")
' "$root" "$BATS_TEST_DIRNAME/../../bin"
	[ "$status" -eq 0 ]
	echo "$output" | grep -Fq "the interrupted ledger temporary never overrode committed events"

	run "$CONTROL_BIN" validate
	[ "$status" -eq 0 ]
	run "$CONTROL_BIN" list-events
	[ "$status" -eq 0 ]
	echo "$output" | grep -Fq "1 committed events in $root (latest revision 1)"
	# TH3.E1.US6 liveness: a later writer completes a real mutation after the interrupted ledger temporary write.
	run "$CONTROL_BIN" publish-event --type liveness-after-interruption
	[ "$status" -eq 0 ]
	echo "$output" | grep -Fq "at revision 2"
}

@test "a writer killed after the ledger replacement agrees with committed events" {
	local root="$BATS_TEST_TMPDIR/ledger-crash-replaced"
	export COCKPIT_CONTROL_ROOT="$root"
	"$CONTROL_BIN" init >/dev/null

	run python3 -c '
import json
import subprocess
import sys
from pathlib import Path

root = Path(sys.argv[1])
module_dir = sys.argv[2]
sys.path.insert(0, module_dir)
import cockpit_control

ledger_path = root / cockpit_control.LEDGER_NAME
ledger_temporary = root / cockpit_control.LEDGER_TEMPORARY_NAME
view_path = root / cockpit_control.EVENTS_NAME
authoritative = root / cockpit_control.LOCKS_DIR_NAME / cockpit_control.CONTROL_LOCK_NAME
metadata_path = root / cockpit_control.CONTROL_METADATA_NAME
control_id = json.loads(metadata_path.read_text())["control_id"]

writer_code = r"""
import sys
from pathlib import Path
sys.path.insert(0, sys.argv[2])
import cockpit_control

def park(boundary, projection):
    if boundary == "ledger-replaced":
        print("LEDGER_REPLACED " + str(projection.revision), flush=True)
        sys.stdin.readline()
        raise AssertionError("the projection barrier was released instead of killed")

cockpit_control._ledger_projection_fault = park
cockpit_control.publish_control_event(
    Path(sys.argv[1]),
    "projected-then-killed",
    actor="killed-writer",
    payload={"active_queue_item_id": "QI-12"},
    command="killed-after-ledger-replace",
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
    parked = writer.stdout.readline().split()
    assert parked and parked[0] == "LEDGER_REPLACED", writer.stderr.read()
    assert parked[1] == "1", parked
finally:
    writer.kill()
    writer.wait(timeout=5)
assert writer.returncode != 0

history = cockpit_control.read_committed_events(root, control_id)
metadata = cockpit_control.validate_root_metadata(json.loads(metadata_path.read_text()), root)
expected = cockpit_control._serialized_record(
    cockpit_control.build_ledger_projection(metadata, history.events)
).encode("utf-8")
assert history.latest_revision == 1
assert json.loads(ledger_path.read_text())["revision"] == history.latest_revision, (
    "the event and ledger revisions agree at this boundary"
)
assert ledger_path.read_bytes() == expected
assert view_path.read_bytes() == cockpit_control.build_events_view(history.events).encode("utf-8")
assert not ledger_temporary.exists()
assert authoritative.is_dir(), "the killed writer died before releasing the control lock"

try:
    cockpit_control.PortableControlLock(
        root, "blocked-by-dead-owner", timeout_seconds=0.15, poll_seconds=0.01
    ).acquire()
except cockpit_control.ControlStoreError as error:
    assert "timed out after 0.15s waiting for locks/control.lock" in str(error), str(error)
else:
    raise AssertionError("the dead owner lock was silently taken over")

assert cockpit_control.repair_stale_control_lock(
    root, timeout_seconds=5, poll_seconds=0.01
).repaired, "later acquisition succeeds after owner-death repair"
steady = cockpit_control.replay_control_ledger(root, timeout_seconds=5, poll_seconds=0.01)
assert steady.current, steady.outcome
assert ledger_path.read_bytes() == expected, "a complete projection is replayed unchanged"

published = cockpit_control.publish_control_event(
    root, "after-projection-crash", command="writer-after-projection-crash",
    timeout_seconds=5, poll_seconds=0.01,
)
assert published.revision == 2
assert published.projection.revision == 2
assert json.loads(ledger_path.read_text())["revision"] == 2
print("the replaced projection agreed with committed events after repair")
' "$root" "$BATS_TEST_DIRNAME/../../bin"
	[ "$status" -eq 0 ]
	echo "$output" | grep -Fq "the replaced projection agreed with committed events after repair"

	run "$CONTROL_BIN" validate
	[ "$status" -eq 0 ]
}

@test "replay advances an unprojected committed event exactly once" {
	local root="$BATS_TEST_TMPDIR/ledger-replay-once"
	export COCKPIT_CONTROL_ROOT="$root"
	"$CONTROL_BIN" init >/dev/null

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
import sys
from pathlib import Path
sys.path.insert(0, sys.argv[2])
import cockpit_control

def park(boundary, publication):
    if boundary == "event-committed":
        print("EVENT_COMMITTED", flush=True)
        sys.stdin.readline()
        raise AssertionError("the commit barrier was released instead of killed")

cockpit_control._event_publication_fault = park
cockpit_control.publish_control_event(
    Path(sys.argv[1]),
    "committed-without-projection",
    actor="killed-writer",
    payload={"active_queue_item_id": "QI-13"},
    command="killed-before-projection",
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
    assert writer.stdout.readline().strip() == "EVENT_COMMITTED", writer.stderr.read()
finally:
    writer.kill()
    writer.wait(timeout=5)
assert writer.returncode != 0

control_id = json.loads((root / cockpit_control.CONTROL_METADATA_NAME).read_text())["control_id"]
assert cockpit_control.read_committed_events(root, control_id).latest_revision == 1
assert json.loads((root / cockpit_control.LEDGER_NAME).read_text())["revision"] == 0
assert (root / cockpit_control.EVENTS_NAME).read_bytes() == b"", (
    "the derived view was never replaced either"
)
assert not (root / cockpit_control.LEDGER_TEMPORARY_NAME).exists()
assert cockpit_control.repair_stale_control_lock(
    root, timeout_seconds=5, poll_seconds=0.01
).repaired
print("committed without projection")
' "$root" "$BATS_TEST_DIRNAME/../../bin"
	[ "$status" -eq 0 ]
	echo "$output" | grep -Fq "committed without projection"

	local events_before
	events_before="$(cd "$root/events" && cksum ./*)"

	run "$CONTROL_BIN" replay-ledger
	[ "$status" -eq 0 ]
	echo "$output" | grep -Fq "rebuilt ledger.json at revision 1"
	echo "$output" | grep -Fq "the derived ledger.json was stale at revision 0"
	[ "$(find "$root/events" -type f | wc -l)" -eq 1 ]
	[ "$(cd "$root/events" && cksum ./*)" = "$events_before" ]

	local projected
	projected="$(cksum "$root/ledger.json")"
	run "$CONTROL_BIN" replay-ledger
	[ "$status" -eq 0 ]
	echo "$output" | grep -Fq "ledger.json projection is current at revision 1"
	[ "$(cksum "$root/ledger.json")" = "$projected" ]
	[ "$(find "$root/events" -type f | wc -l)" -eq 1 ]
	[ "$(cd "$root/events" && cksum ./*)" = "$events_before" ]

	run "$CONTROL_BIN" list-events
	[ "$status" -eq 0 ]
	echo "$output" | grep -Fq "1 committed events in $root (latest revision 1)"
	run "$CONTROL_BIN" validate
	[ "$status" -eq 0 ]
}

@test "a ledger revision no committed event represents is discarded and rebuilt" {
	local root="$BATS_TEST_TMPDIR/ledger-derived-corruption"
	export COCKPIT_CONTROL_ROOT="$root"
	"$CONTROL_BIN" init >/dev/null

	"$CONTROL_BIN" publish-event --type first --actor overseer \
		--payload '{"active_queue_item_id": "QI-14"}' >/dev/null
	"$CONTROL_BIN" publish-event --type second --actor overseer >/dev/null

	local expected="$BATS_TEST_TMPDIR/expected-ledger.json"
	cp "$root/ledger.json" "$expected"
	local expected_view="$BATS_TEST_TMPDIR/expected-events.jsonl"
	cp "$root/events.jsonl" "$expected_view"
	local events_before
	events_before="$(cd "$root/events" && cksum ./*)"

	run python3 -c '
import json
import sys
from pathlib import Path
path = Path(sys.argv[1])
record = json.loads(path.read_text())
record["revision"] = 99
path.write_text(json.dumps(record, indent=2, sort_keys=True) + "\n")
' "$root/ledger.json"
	[ "$status" -eq 0 ]

	local corrupt
	corrupt="$(cksum "$root/ledger.json")"
	run "$CONTROL_BIN" replay-ledger --dry-run
	[ "$status" -eq 0 ]
	echo "$output" | grep -Fq "would rebuild ledger.json at revision 2"
	echo "$output" | grep -Fq "claimed revision 99, which no committed event represents"
	echo "$output" | grep -Fq "no state changed"
	[ "$(cksum "$root/ledger.json")" = "$corrupt" ]

	run "$CONTROL_BIN" replay-ledger
	[ "$status" -eq 0 ]
	echo "$output" | grep -Fq "rebuilt ledger.json at revision 2"
	echo "$output" | grep -Fq "claimed revision 99, which no committed event represents"
	cmp "$root/ledger.json" "$expected"

	printf 'not a ledger' > "$root/ledger.json"
	run "$CONTROL_BIN" replay-ledger
	[ "$status" -eq 0 ]
	echo "$output" | grep -Fq "was unreadable derived corruption"
	cmp "$root/ledger.json" "$expected"

	rm "$root/ledger.json"
	run "$CONTROL_BIN" replay-ledger
	[ "$status" -eq 0 ]
	echo "$output" | grep -Fq "the derived ledger.json was missing"
	cmp "$root/ledger.json" "$expected"

	printf '' > "$root/events.jsonl"
	run "$CONTROL_BIN" replay-ledger
	[ "$status" -eq 0 ]
	echo "$output" | grep -Fq "the derived events.jsonl view was out of date"
	cmp "$root/events.jsonl" "$expected_view"
	cmp "$root/ledger.json" "$expected"

	run python3 -c '
import json
import sys
from pathlib import Path
source = Path(sys.argv[1])
record = json.loads(source.read_text())
record["revision"] = 99
Path(sys.argv[2]).write_text(json.dumps(record, indent=2, sort_keys=True) + "\n")
' "$expected" "$root/ledger.json.tmp"
	[ "$status" -eq 0 ]

	run "$CONTROL_BIN" replay-ledger
	[ "$status" -eq 0 ]
	echo "$output" | grep -Fq "ledger.json projection is current at revision 2"
	echo "$output" | grep -Fq "ledger.json.tmp is a non-authoritative interrupted projection temporary"
	cmp "$root/ledger.json" "$expected"
	[ -f "$root/ledger.json.tmp" ]

	[ "$(find "$root/events" -type f | wc -l)" -eq 2 ]
	[ "$(cd "$root/events" && cksum ./*)" = "$events_before" ]
	run "$CONTROL_BIN" list-events
	[ "$status" -eq 0 ]
	echo "$output" | grep -Fq "2 committed events in $root (latest revision 2)"
}
