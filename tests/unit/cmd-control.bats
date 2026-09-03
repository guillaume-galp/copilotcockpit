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
