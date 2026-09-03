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
