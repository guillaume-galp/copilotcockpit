#!/usr/bin/env bats
# tests/unit/cmd-control-command.bats — TH3.E2.US2 idempotent command envelopes
# and acknowledgements.
#
# The contract under test has three halves:
#
#   * every state-changing worker operation is one versioned, machine-readable
#     command envelope carrying its command ID, mission, queue item, delivery
#     target, trace and parent trace, schema version, deterministic payload
#     digest, and declared ADR-016 mission boundaries, committed through the
#     immutable event protocol and readable back by any cold process;
#   * workers acknowledge `accepted`, `applied`, `rejected`, and `duplicate`
#     outcomes durably, and every acknowledgement is answerable after the
#     derived projection is destroyed and the process that recorded it is gone;
#   * redelivery is idempotent: the same command ID with the same digest returns
#     the stored result without applying it again, while the same ID with a
#     different digest is refused and recorded as a durable conflict.
#
# Nothing here waits on wall-clock timing: the concurrency proof starts real
# processes and lets the control lock and the deterministic fold order them.

load helper

setup() {
	cc_setup_fake_home
	export CONTROL_BIN="$BATS_TEST_DIRNAME/../../bin/cockpit-control"
	export MODULE_DIR="$BATS_TEST_DIRNAME/../../bin"
	unset COCKPIT_CONTROL_ROOT COCKPIT_QUEUE_ROOT TMUX TMUX_SESSION COCKPIT_SESSION_ID COCKPIT_ID
}

# cc_uuid — print one canonical lowercase UUID.
cc_uuid() {
	python3 -c 'import uuid; print(uuid.uuid4())'
}

# cc_digest <json-payload> — print the canonical digest of one command payload.
cc_digest() {
	python3 -c '
import sys
sys.path.insert(0, sys.argv[2])
import cockpit_control
print(cockpit_control.command_payload_digest(__import__("json").loads(sys.argv[1])))
' "$1" "$MODULE_DIR"
}

# cc_command_store <root> — initialize one empty control store under <root>.
cc_command_store() {
	export COCKPIT_CONTROL_ROOT="$1"
	"$CONTROL_BIN" init >/dev/null
}

# cc_absent <pattern> <file> — require <pattern> to be absent from <file>.
#
# A negative assertion must be written this way, never as a bare
# `! grep -q ... file`.  Bash suspends `set -e` for any command whose status is
# inverted with `!` (ShellCheck SC2314), so a mid-body `! grep` can never fail a
# bats test: it reports the forged match and execution continues regardless.
# Returning non-zero from a helper is a plain command failure, which errexit
# does honour, so these assertions actually hold.
cc_absent() {
	if grep -q "$1" "$2"; then
		echo "cc_absent: forbidden pattern '$1' found in $2:" >&2
		grep -n "$1" "$2" >&2
		return 1
	fi
	return 0
}

# cc_absent_output <pattern> — require <pattern> to be absent from the captured
# `$output` of the preceding `run`.  Same errexit reasoning as cc_absent.
cc_absent_output() {
	if printf '%s\n' "$output" | grep -q "$1"; then
		echo "cc_absent_output: forbidden pattern '$1' found in captured output:" >&2
		printf '%s\n' "$output" | grep -n "$1" >&2
		return 1
	fi
	return 0
}

# cc_no_traceback <file> — require a refusal diagnostic to never leak a Python
# traceback or an internal module path.
cc_no_traceback() {
	cc_absent "Traceback (most recent call last)" "$1"
	cc_absent "cockpit_control\.py" "$1"
}

# cc_refuse <subcommand> <stdout-file> <stderr-file> <args...> — require one
# invocation to be refused as a fail-closed diagnostic: a non-zero exit, an
# empty stdout payload, a `cockpit-control:` stderr diagnostic, and never a
# Python traceback leaking an internal path.
cc_refuse() {
	local subcommand="$1" out="$2" err="$3"
	shift 3
	local code=0
	"$CONTROL_BIN" "$subcommand" "$@" >"$out" 2>"$err" || code=$?
	[ "$code" -ne 0 ]
	[ ! -s "$out" ]
	head -n 1 "$err" | grep -q "^cockpit-control: "
	cc_no_traceback "$err"
}

@test "a registered command persists every identity trace boundary and schema field" {
	local root="$BATS_TEST_TMPDIR/command-envelope"
	cc_command_store "$root"
	local command_id mission trace parent digest
	command_id="$(cc_uuid)"
	mission="$(cc_uuid)"
	trace="$(cc_uuid)"
	parent="$(cc_uuid)"
	digest="$(cc_digest '{"instruction":"deliver TH3.E2.US2","limit":3}')"

	run "$CONTROL_BIN" register-command --command-id "$command_id" --type dispatch \
		--mission "$mission" --queue-item QI-1 --target worker-dev --trace "$trace" \
		--parent-trace "$parent" --payload '{"instruction":"deliver TH3.E2.US2","limit":3}' \
		--queue-root "$root/queue" --planning-root "$root/planning" \
		--implementation-root "$root/impl-a" --implementation-root "$root/impl-b" \
		--runtime-boundary deploy:none --runtime-boundary image:pinned \
		--deadline 2099-01-01T00:00:00Z
	[ "$status" -eq 0 ]
	echo "$output" | grep -Fq "committed events/000000000001-"
	echo "$output" | grep -Fq "is registered with digest $digest"
	echo "$output" | grep -Fq "command $command_id type dispatch mission $mission queue-item QI-1 target worker/worker-dev trace $trace parent-trace $parent digest $digest schema-version 1 status registered deliveries 1 acknowledgements 0 conflicts 0"
	echo "$output" | grep -Fq "boundaries $command_id control-root declared queue-root declared planning-root declared implementation-roots 2 runtime deploy:none,image:pinned"

	run "$CONTROL_BIN" list-events
	[ "$status" -eq 0 ]
	echo "$output" | grep -q "type command-registered actor cockpit-control$"

	# Every AC1 field is read back from the committed event file and from the
	# derived projection by a separate process, and the command payload itself
	# is deliberately absent: ADR-016 keeps canonical records metadata-only.
	run python3 -c '
import json
import sys
from pathlib import Path
from uuid import UUID

sys.path.insert(0, sys.argv[2])
import cockpit_control

root = Path(sys.argv[1])
control_id = json.loads((root / cockpit_control.CONTROL_METADATA_NAME).read_text())["control_id"]
history = cockpit_control.read_committed_events(root, control_id)
assert len(history.events) == 1, history.events
event = history.events[0]
assert event.record["event_type"] == cockpit_control.COMMAND_REGISTERED_EVENT_TYPE
envelope = cockpit_control.event_command_envelope(event.record, event.path.name)
assert envelope is not None

assert envelope["schema_version"] == cockpit_control.COMMAND_SCHEMA_VERSION
assert envelope["record_type"] == cockpit_control.COMMAND_ENVELOPE_RECORD_TYPE
assert envelope["command_id"] == sys.argv[3], envelope
assert envelope["command_type"] == "dispatch"
assert envelope["mission_id"] == sys.argv[4]
assert envelope["queue_item_id"] == "QI-1"
assert envelope["target"] == {"kind": "worker", "id": "worker-dev"}
assert envelope["trace_id"] == sys.argv[5]
assert envelope["parent_trace_id"] == sys.argv[6]
assert envelope["payload_digest"] == sys.argv[7], envelope
assert envelope["deadline_at"] == "2099-01-01T00:00:00Z"
UUID(envelope["command_id"])
UUID(envelope["mission_id"])
UUID(envelope["trace_id"])
UUID(envelope["parent_trace_id"])

boundaries = envelope["boundaries"]
assert boundaries["control_root"] == str(root), boundaries
assert boundaries["queue_root"] == str(root / "queue"), boundaries
assert boundaries["planning_root"] == str(root / "planning"), boundaries
assert boundaries["implementation_roots"] == [str(root / "impl-a"), str(root / "impl-b")]
assert boundaries["runtime_boundaries"] == ["deploy:none", "image:pinned"]

serialized = json.dumps(event.record)
assert "deliver TH3.E2.US2" not in serialized, "the payload body is not a canonical record"

ledger = json.loads((root / cockpit_control.LEDGER_NAME).read_text())
slot = ledger[cockpit_control.LEDGER_COMMANDS_FIELD][sys.argv[3]]
assert slot["envelope"] == envelope, slot
assert slot["status"] == cockpit_control.COMMAND_STATUS_REGISTERED
assert [delivery["revision"] for delivery in slot["deliveries"]] == [1]
assert slot["acknowledgements"] == [] and slot["conflicts"] == []
print("every command envelope field was persisted and read back")
' "$root" "$MODULE_DIR" "$command_id" "$mission" "$trace" "$parent" "$digest"
	[ "$status" -eq 0 ]
	echo "$output" | grep -Fq "every command envelope field was persisted and read back"

	run "$CONTROL_BIN" validate
	[ "$status" -eq 0 ]
}

@test "an idle capable worker acknowledges a new command accepted and then applied" {
	local root="$BATS_TEST_TMPDIR/command-happy-path"
	cc_command_store "$root"
	local command_id mission trace digest payload
	command_id="$(cc_uuid)"
	mission="$(cc_uuid)"
	trace="$(cc_uuid)"
	payload='{"instruction":"run the governed gate"}'
	digest="$(cc_digest "$payload")"

	run "$CONTROL_BIN" register-command --command-id "$command_id" --type dispatch \
		--mission "$mission" --queue-item QI-2 --target worker-dev --trace "$trace" \
		--payload "$payload"
	[ "$status" -eq 0 ]

	run "$CONTROL_BIN" acknowledge-command --command-id "$command_id" --outcome accepted \
		--by worker-dev --digest "$digest"
	[ "$status" -eq 0 ]
	echo "$output" | grep -Fq "is acknowledged accepted by worker-dev (status accepted)"

	run "$CONTROL_BIN" acknowledge-command --command-id "$command_id" --outcome applied \
		--by worker-dev --payload "$payload" --result "report:worker-dev/$mission/1" \
		--result test:RUN-1
	[ "$status" -eq 0 ]
	echo "$output" | grep -Fq "is acknowledged applied by worker-dev (status applied)"

	run "$CONTROL_BIN" list-events
	[ "$status" -eq 0 ]
	echo "$output" | grep -q "type command-acknowledged-accepted actor worker-dev$"
	echo "$output" | grep -q "type command-acknowledged-applied actor worker-dev$"

	# Both acknowledgements are durable and answerable by a separate process.
	run "$CONTROL_BIN" command-status --command-id "$command_id"
	[ "$status" -eq 0 ]
	echo "$output" | grep -Fq "status applied deliveries 1 acknowledgements 2 conflicts 0"
	echo "$output" | grep -Fq "acknowledgement $command_id outcome accepted by worker-dev digest $digest revision 2"
	echo "$output" | grep -Fq "acknowledgement $command_id outcome applied by worker-dev digest $digest revision 3"
	echo "$output" | grep -Fq "result-refs report:worker-dev/$mission/1,test:RUN-1"

	run "$CONTROL_BIN" validate
	[ "$status" -eq 0 ]
}

@test "every acknowledgement outcome stays queryable after a cold controller restart" {
	local root="$BATS_TEST_TMPDIR/command-restart"
	cc_command_store "$root"
	local applied_command rejected_command mission trace applied_digest rejected_digest
	applied_command="$(cc_uuid)"
	rejected_command="$(cc_uuid)"
	mission="$(cc_uuid)"
	trace="$(cc_uuid)"
	applied_digest="$(cc_digest '{"step":"apply"}')"
	rejected_digest="$(cc_digest '{"step":"reject"}')"

	"$CONTROL_BIN" register-command --command-id "$applied_command" --type dispatch \
		--mission "$mission" --queue-item QI-3 --target worker-dev --trace "$trace" \
		--payload '{"step":"apply"}' >/dev/null
	"$CONTROL_BIN" acknowledge-command --command-id "$applied_command" --outcome accepted \
		--by worker-dev --digest "$applied_digest" >/dev/null
	"$CONTROL_BIN" acknowledge-command --command-id "$applied_command" --outcome applied \
		--by worker-dev --digest "$applied_digest" --result "report:worker-dev/$mission/1" >/dev/null
	"$CONTROL_BIN" register-command --command-id "$applied_command" --type dispatch \
		--mission "$mission" --queue-item QI-3 --target worker-dev --trace "$trace" \
		--payload '{"step":"apply"}' >/dev/null
	"$CONTROL_BIN" register-command --command-id "$rejected_command" --type cancel \
		--mission "$mission" --queue-item QI-3 --target worker-dev --trace "$trace" \
		--payload '{"step":"reject"}' >/dev/null
	"$CONTROL_BIN" acknowledge-command --command-id "$rejected_command" --outcome rejected \
		--by worker-dev --digest "$rejected_digest" --reason "the worker cannot cancel a merged mission" >/dev/null

	# Simulate a controller restart that also loses every derived projection:
	# only the immutable committed event files remain.
	rm "$root/ledger.json" "$root/events.jsonl"

	run "$CONTROL_BIN" command-status
	[ "$status" -eq 0 ]
	echo "$output" | grep -Fq "cockpit-control: 2 command(s) in $root; 0 with recorded conflict(s)"
	echo "$output" | grep -Fq "acknowledgement $applied_command outcome accepted by worker-dev"
	echo "$output" | grep -Fq "acknowledgement $applied_command outcome applied by worker-dev"
	echo "$output" | grep -Fq "acknowledgement $applied_command outcome duplicate by worker-dev"
	echo "$output" | grep -Fq "acknowledgement $rejected_command outcome rejected by worker-dev"
	[ "$(echo "$output" | grep -c "^acknowledgement ")" -eq 4 ]

	# The derived projection self-heals from the same committed events and then
	# validates, so the restart loses no acknowledgement at all.
	run "$CONTROL_BIN" replay-ledger
	[ "$status" -eq 0 ]
	echo "$output" | grep -Fq "rebuilt ledger.json at revision 6"

	run "$CONTROL_BIN" validate
	[ "$status" -eq 0 ]

	run python3 -c '
import json
import sys
from pathlib import Path

sys.path.insert(0, sys.argv[2])
import cockpit_control

root = Path(sys.argv[1])
ledger = json.loads((root / cockpit_control.LEDGER_NAME).read_text())
commands = ledger[cockpit_control.LEDGER_COMMANDS_FIELD]
outcomes = {
    command_id: [
        entry["acknowledgement"]["outcome"] for entry in slot["acknowledgements"]
    ]
    for command_id, slot in commands.items()
}
assert outcomes[sys.argv[3]] == ["accepted", "applied", "duplicate"], outcomes
assert outcomes[sys.argv[4]] == ["rejected"], outcomes
assert commands[sys.argv[3]]["status"] == "applied"
assert commands[sys.argv[4]]["status"] == "rejected"
observed = sorted({outcome for values in outcomes.values() for outcome in values})
assert observed == sorted(cockpit_control.COMMAND_ACKNOWLEDGEMENT_OUTCOMES), observed
print("every acknowledgement outcome survived the restart")
' "$root" "$MODULE_DIR" "$applied_command" "$rejected_command"
	[ "$status" -eq 0 ]
	echo "$output" | grep -Fq "every acknowledgement outcome survived the restart"
}

@test "redelivering the same command ID and digest returns the stored result without applying it again" {
	local root="$BATS_TEST_TMPDIR/command-redelivery"
	cc_command_store "$root"
	local command_id mission trace digest payload reordered
	command_id="$(cc_uuid)"
	mission="$(cc_uuid)"
	trace="$(cc_uuid)"
	payload='{"instruction":"deliver once","limit":3}'
	reordered='{"limit":3,"instruction":"deliver once"}'
	digest="$(cc_digest "$payload")"

	"$CONTROL_BIN" register-command --command-id "$command_id" --type dispatch \
		--mission "$mission" --queue-item QI-4 --target worker-dev --trace "$trace" \
		--payload "$payload" >/dev/null
	"$CONTROL_BIN" acknowledge-command --command-id "$command_id" --outcome accepted \
		--by worker-dev --digest "$digest" >/dev/null
	"$CONTROL_BIN" acknowledge-command --command-id "$command_id" --outcome applied \
		--by worker-dev --digest "$digest" --result "report:worker-dev/$mission/1" >/dev/null

	local applied_before
	applied_before="$("$CONTROL_BIN" command-status --command-id "$command_id" | grep -c "outcome applied")"
	[ "$applied_before" -eq 1 ]

	# The lost acknowledgement is retried with the same ID and the same logical
	# payload, written in a different key order to prove the digest is canonical.
	run "$CONTROL_BIN" register-command --command-id "$command_id" --type dispatch \
		--mission "$mission" --queue-item QI-4 --target worker-dev --trace "$trace" \
		--payload "$reordered"
	[ "$status" -eq 0 ]
	echo "$output" | grep -Fq "acknowledged duplicate; the stored result is returned without applying it again (status applied)"
	# The stored result itself is returned to the caller.
	echo "$output" | grep -Fq "acknowledgement $command_id outcome applied by worker-dev digest $digest revision 3 "
	echo "$output" | grep -Fq "result-refs report:worker-dev/$mission/1"

	# Nothing was applied a second time: one delivery, one applied
	# acknowledgement, and the same registration revision as before.
	run "$CONTROL_BIN" command-status --command-id "$command_id"
	[ "$status" -eq 0 ]
	echo "$output" | grep -Fq "status applied deliveries 1 acknowledgements 3 conflicts 0"
	[ "$(echo "$output" | grep -c "outcome applied")" -eq 1 ]
	[ "$(echo "$output" | grep -c "outcome duplicate")" -eq 1 ]

	# Applying the same command twice is refused outright rather than repeated.
	local out="$BATS_TEST_TMPDIR/redelivery-stdout" err="$BATS_TEST_TMPDIR/redelivery-stderr"
	local before
	before="$(cc_control_contents "$root")"
	cc_refuse acknowledge-command "$out" "$err" --command-id "$command_id" \
		--outcome applied --by worker-dev --digest "$digest" \
		--result "report:worker-dev/$mission/2"
	grep -Fq "is applied; a 'applied' acknowledgement is not allowed from that status (allowed: duplicate)" "$err"
	[ "$before" = "$(cc_control_contents "$root")" ]

	run python3 -c '
import json
import sys
from pathlib import Path

sys.path.insert(0, sys.argv[2])
import cockpit_control

root = Path(sys.argv[1])
slot = json.loads((root / cockpit_control.LEDGER_NAME).read_text())[
    cockpit_control.LEDGER_COMMANDS_FIELD
][sys.argv[3]]
applied = [
    entry for entry in slot["acknowledgements"]
    if entry["acknowledgement"]["outcome"] == "applied"
]
assert len(applied) == 1, applied
assert applied[0]["revision"] == 3, applied
assert [delivery["revision"] for delivery in slot["deliveries"]] == [1], slot
assert slot["revision"] == 1 and slot["status"] == "applied", slot
print("the redelivery returned the stored result and applied nothing")
' "$root" "$MODULE_DIR" "$command_id"
	[ "$status" -eq 0 ]
	echo "$output" | grep -Fq "the redelivery returned the stored result and applied nothing"

	run "$CONTROL_BIN" validate
	[ "$status" -eq 0 ]
}

@test "reusing one command ID for another payload is refused and records a durable conflict" {
	local root="$BATS_TEST_TMPDIR/command-conflict"
	cc_command_store "$root"
	local command_id mission trace digest other_digest
	command_id="$(cc_uuid)"
	mission="$(cc_uuid)"
	trace="$(cc_uuid)"
	digest="$(cc_digest '{"instruction":"first"}')"
	other_digest="$(cc_digest '{"instruction":"second"}')"

	"$CONTROL_BIN" register-command --command-id "$command_id" --type dispatch \
		--mission "$mission" --queue-item QI-5 --target worker-dev --trace "$trace" \
		--payload '{"instruction":"first"}' >/dev/null
	"$CONTROL_BIN" acknowledge-command --command-id "$command_id" --outcome accepted \
		--by worker-dev --digest "$digest" >/dev/null

	local out="$BATS_TEST_TMPDIR/conflict-stdout" err="$BATS_TEST_TMPDIR/conflict-stderr"
	local code=0
	"$CONTROL_BIN" register-command --command-id "$command_id" --type dispatch \
		--mission "$mission" --queue-item QI-5 --target worker-dev --trace "$trace" \
		--payload '{"instruction":"second"}' >"$out" 2>"$err" || code=$?
	[ "$code" -ne 0 ]
	grep -Fq "refused as a digest-conflict" "$out"
	grep -Fq "the command ID already belongs to a different payload digest" "$err"
	grep -Fq "the stored command keeps digest $digest and the offered delivery declared $other_digest" "$err"
	cc_no_traceback "$err"

	# Reusing the ID for a different envelope is the same class of conflict even
	# when the payload digest is unchanged.
	local other_mission
	other_mission="$(cc_uuid)"
	code=0
	"$CONTROL_BIN" register-command --command-id "$command_id" --type dispatch \
		--mission "$other_mission" --queue-item QI-5 --target worker-dev --trace "$trace" \
		--payload '{"instruction":"first"}' >"$out" 2>"$err" || code=$?
	[ "$code" -ne 0 ]
	grep -Fq "refused as a envelope-conflict" "$out"
	grep -Fq "the command ID already belongs to a different command envelope" "$err"

	# A worker that observes the reuse itself records the same conflict from the
	# acknowledgement side.
	code=0
	"$CONTROL_BIN" acknowledge-command --command-id "$command_id" --outcome rejected \
		--by worker-dev --digest "$other_digest" \
		--reason "this worker was handed a different payload under a known command ID" \
		>"$out" 2>"$err" || code=$?
	[ "$code" -ne 0 ]
	grep -Fq "refused as a digest-conflict" "$out"

	# The stored command is untouched: same digest, same status, and the
	# conflicts are durable and queryable from a cold process.
	run "$CONTROL_BIN" command-status --command-id "$command_id"
	[ "$status" -eq 0 ]
	echo "$output" | grep -Fq "digest $digest schema-version 1 status accepted deliveries 1 acknowledgements 2 conflicts 3"
	echo "$output" | grep -Fq "conflict $command_id reason digest-conflict source delivery digest $other_digest revision 3"
	echo "$output" | grep -Fq "conflict $command_id reason envelope-conflict source delivery digest $digest revision 4"
	echo "$output" | grep -Fq "conflict $command_id reason digest-conflict source acknowledgement digest $other_digest revision 5"
	echo "$output" | grep -Fq "cockpit-control: command $command_id has 3 recorded conflict(s)"

	run "$CONTROL_BIN" validate
	[ "$status" -eq 0 ]

	run python3 -c '
import json
import sys
from pathlib import Path

sys.path.insert(0, sys.argv[2])
import cockpit_control

root = Path(sys.argv[1])
slot = json.loads((root / cockpit_control.LEDGER_NAME).read_text())[
    cockpit_control.LEDGER_COMMANDS_FIELD
][sys.argv[3]]
assert slot["envelope"]["payload_digest"] == sys.argv[4], slot
assert slot["envelope"]["mission_id"] == sys.argv[6], slot
assert slot["status"] == "accepted", slot
assert [
    (conflict["reason"], conflict["source"], conflict["payload_digest"])
    for conflict in slot["conflicts"]
] == [
    ("digest-conflict", "delivery", sys.argv[5]),
    ("envelope-conflict", "delivery", sys.argv[4]),
    ("digest-conflict", "acknowledgement", sys.argv[5]),
], slot["conflicts"]
print("the reused command ID was refused and every conflict was recorded")
' "$root" "$MODULE_DIR" "$command_id" "$digest" "$other_digest" "$mission"
	[ "$status" -eq 0 ]
	echo "$output" | grep -Fq "the reused command ID was refused and every conflict was recorded"
}

@test "the payload digest is canonical deterministic and sensitive to every difference" {
	run python3 -c '
import sys

sys.path.insert(0, sys.argv[1])
import cockpit_control
from cockpit_control import ControlStoreError, command_payload_digest

# The same logical payload always yields the same digest, whatever the key
# order or the object identity.
first = {"b": [1, 2], "a": {"y": True, "x": None}}
second = {"a": {"x": None, "y": True}, "b": [1, 2]}
assert command_payload_digest(first) == command_payload_digest(second)
assert command_payload_digest(first) == command_payload_digest(dict(first))
assert command_payload_digest({}) == command_payload_digest({})

# Any difference at all yields a different digest.
different = (
    {},
    {"a": {"x": None, "y": True}},
    {"b": [2, 1], "a": {"y": True, "x": None}},
    {"b": [1, 2], "a": {"y": True, "x": False}},
    {"b": [1, 2], "a": {"y": True, "x": None}, "c": 0},
    {"b": ["1", 2], "a": {"y": True, "x": None}},
    {"B": [1, 2], "a": {"y": True, "x": None}},
)
digests = {command_payload_digest(payload) for payload in different}
assert len(digests) == len(different), digests
assert command_payload_digest(first) not in digests

# The digest shape is exactly what every record requires.
digest = command_payload_digest(first)
assert digest.startswith(cockpit_control.COMMAND_DIGEST_PREFIX), digest
assert len(digest) == len(cockpit_control.COMMAND_DIGEST_PREFIX) + 64, digest
cockpit_control._require_digest({"payload_digest": digest}, "payload_digest", "command")

# Anything that could make two payloads share one digest is refused.
for rejected in ([], "payload", None, 3, {1: "int-key"}, {"a": {2: "nested"}}):
    try:
        command_payload_digest(rejected)
    except ControlStoreError:
        continue
    raise AssertionError("an ambiguous payload was digested: %r" % (rejected,))
try:
    command_payload_digest({"a": float("nan")})
except ControlStoreError:
    pass
else:
    raise AssertionError("a non-finite number was digested")
print("the payload digest is canonical and sensitive")
' "$MODULE_DIR"
	[ "$status" -eq 0 ]
	echo "$output" | grep -Fq "the payload digest is canonical and sensitive"

	# The same determinism decides idempotency at the command boundary: a
	# re-ordered payload is a redelivery, a changed payload is a conflict.
	local root="$BATS_TEST_TMPDIR/command-digest"
	cc_command_store "$root"
	local command_id mission trace
	command_id="$(cc_uuid)"
	mission="$(cc_uuid)"
	trace="$(cc_uuid)"

	run "$CONTROL_BIN" register-command --command-id "$command_id" --type dispatch \
		--mission "$mission" --queue-item QI-6 --target worker-dev --trace "$trace" \
		--payload '{"a":1,"b":{"c":[true,null]}}'
	[ "$status" -eq 0 ]
	run "$CONTROL_BIN" register-command --command-id "$command_id" --type dispatch \
		--mission "$mission" --queue-item QI-6 --target worker-dev --trace "$trace" \
		--payload '{"b":{"c":[true,null]},"a":1}'
	[ "$status" -eq 0 ]
	echo "$output" | grep -Fq "acknowledged duplicate"
	run "$CONTROL_BIN" register-command --command-id "$command_id" --type dispatch \
		--mission "$mission" --queue-item QI-6 --target worker-dev --trace "$trace" \
		--payload '{"b":{"c":[true,false]},"a":1}'
	[ "$status" -ne 0 ]
	echo "$output" | grep -Fq "refused as a digest-conflict"
}

@test "malformed unversioned and future-versioned command records fail closed when read back" {
	local root="$BATS_TEST_TMPDIR/command-schema"
	cc_command_store "$root"

	run python3 -c '
import sys
from uuid import uuid4

sys.path.insert(0, sys.argv[1])
import cockpit_control
from cockpit_control import (
    ControlStoreError,
    validate_command_acknowledgement,
    validate_command_envelope,
)

command_id = str(uuid4())
timestamp = "2026-09-03T15:35:15Z"
digest = cockpit_control.command_payload_digest({"a": 1})
envelope = {
    "schema_version": 1, "record_type": "command-envelope", "command_id": command_id,
    "command_type": "dispatch", "mission_id": str(uuid4()), "queue_item_id": "QI-1",
    "target": {"kind": "worker", "id": "worker-dev"}, "trace_id": str(uuid4()),
    "parent_trace_id": None, "payload_digest": digest,
    "boundaries": {
        "control_root": "/control", "queue_root": None, "planning_root": None,
        "implementation_roots": [], "runtime_boundaries": [],
    },
    "created_at": timestamp, "deadline_at": None,
}
acknowledgement = {
    "schema_version": 1, "record_type": "command-acknowledgement",
    "command_id": command_id, "payload_digest": digest, "outcome": "applied",
    "acknowledged_by": "worker-dev", "acknowledged_at": timestamp, "reason": None,
    "result_refs": ["report:worker-dev/mission/1"],
}
validate_command_envelope(dict(envelope))
validate_command_acknowledgement(dict(acknowledgement))


def refused(record, validator, fragment):
    try:
        validator(record)
    except ControlStoreError as error:
        assert fragment in str(error), (fragment, str(error))
        return
    raise AssertionError("accepted %s" % (fragment,))


for version, fragment in ((2, "unsupported future schema_version"), (0, "unsupported schema_version")):
    for record, validator in (
        (envelope, validate_command_envelope),
        (acknowledgement, validate_command_acknowledgement),
    ):
        broken = dict(record)
        broken["schema_version"] = version
        refused(broken, validator, fragment)
    for record, validator in (
        (envelope, validate_command_envelope),
        (acknowledgement, validate_command_acknowledgement),
    ):
        broken = dict(record)
        del broken["schema_version"]
        refused(broken, validator, "requires integer schema_version")

for field in envelope:
    if field == "schema_version":
        continue
    broken = dict(envelope)
    del broken[field]
    refused(broken, validate_command_envelope, "requires %s" % field)
for field in acknowledgement:
    if field == "schema_version":
        continue
    broken = dict(acknowledgement)
    del broken[field]
    refused(broken, validate_command_acknowledgement, "requires %s" % field)

refused(dict(envelope, command_id="not-a-uuid"), validate_command_envelope, "requires UUID command_id")
refused(dict(envelope, payload_digest="sha256:zz"), validate_command_envelope, "lowercase hex digits")
refused(dict(envelope, payload_digest=digest.upper()), validate_command_envelope, "lowercase hex digits")
refused(dict(envelope, invented="x"), validate_command_envelope, "declares unknown field(s) invented")
refused(
    dict(envelope, target={"kind": "pane", "id": "worker-dev"}),
    validate_command_envelope,
    "declares unknown target kind",
)
refused(
    dict(envelope, target={"kind": "worker"}), validate_command_envelope, "target requires id"
)
refused(
    dict(envelope, parent_trace_id=envelope["trace_id"]),
    validate_command_envelope,
    "requires parent_trace_id to name a different trace",
)
refused(
    dict(envelope, deadline_at="2020-01-01T00:00:00Z"),
    validate_command_envelope,
    "requires deadline_at after created_at",
)
refused(
    dict(envelope, boundaries=dict(envelope["boundaries"], control_root="relative")),
    validate_command_envelope,
    "must be an absolute path",
)
refused(
    dict(envelope, boundaries=dict(envelope["boundaries"], implementation_roots=["/a", "/a"])),
    validate_command_envelope,
    "duplicates implementation root /a",
)
refused(
    dict(envelope, boundaries=dict(envelope["boundaries"], runtime_boundaries=["untyped"])),
    validate_command_envelope,
    "must be a typed '"'"'<type>:<value>'"'"' boundary reference",
)
broken = dict(envelope)
broken["boundaries"] = {"control_root": "/control"}
refused(broken, validate_command_envelope, "boundaries requires")

refused(
    dict(acknowledgement, outcome="acknowledged"),
    validate_command_acknowledgement,
    "declares unknown outcome",
)
refused(
    dict(acknowledgement, result_refs=[]),
    validate_command_acknowledgement,
    "requires at least one result reference",
)
for outcome in ("rejected", "duplicate"):
    refused(
        dict(acknowledgement, outcome=outcome, result_refs=[]),
        validate_command_acknowledgement,
        "requires a reason for outcome",
    )
refused(
    dict(acknowledgement, invented=1),
    validate_command_acknowledgement,
    "declares unknown field(s) invented",
)
print("every malformed command record was refused")
' "$MODULE_DIR"
	[ "$status" -eq 0 ]
	echo "$output" | grep -Fq "every malformed command record was refused"

	# The same refusal happens when a hand-written event is read back: a
	# committed file can never be understood as half a command contract.
	local out="$BATS_TEST_TMPDIR/schema-stdout" err="$BATS_TEST_TMPDIR/schema-stderr"
	local command_id mission trace
	command_id="$(cc_uuid)"
	mission="$(cc_uuid)"
	trace="$(cc_uuid)"
	"$CONTROL_BIN" register-command --command-id "$command_id" --type dispatch \
		--mission "$mission" --queue-item QI-7 --target worker-dev --trace "$trace" \
		--payload '{"a":1}' >/dev/null

	run python3 -c '
import json
import sys
from pathlib import Path

sys.path.insert(0, sys.argv[2])
import cockpit_control

root = Path(sys.argv[1])
events = sorted((root / cockpit_control.EVENTS_DIR_NAME).iterdir())
record = json.loads(events[0].read_text())
record["payload"][cockpit_control.COMMAND_ENVELOPE_PAYLOAD_FIELD]["schema_version"] = 2
events[0].write_text(json.dumps(record, indent=2, sort_keys=True) + "\n")
' "$root" "$MODULE_DIR"
	[ "$status" -eq 0 ]

	# Every read of the future-versioned command refuses it and changes nothing.
	local before
	before="$(cc_control_contents "$root")"
	local code=0
	"$CONTROL_BIN" command-status >"$out" 2>"$err" || code=$?
	[ "$code" -ne 0 ]
	[ ! -s "$out" ]
	grep -Fq "unsupported future schema_version 2; upgrade cockpit tools before mutation" "$err"
	cc_no_traceback "$err"

	code=0
	"$CONTROL_BIN" validate >"$out" 2>"$err" || code=$?
	[ "$code" -ne 0 ]
	grep -Fq "unsupported future schema_version 2" "$err"

	# The same fail-closed contract covers a committed record whose JSON the
	# interpreter refuses to decode at all rather than reports as a decode
	# error: an integer literal past CPython's 4300-digit int->str conversion
	# limit raises a bare ValueError and a pathologically nested document
	# raises RecursionError.  Neither may reach the reader as a traceback.
	local subcommand event_backup
	event_backup="$BATS_TEST_TMPDIR/schema-event-backup"
	cp "$(find "$root/events" -type f | sort | head -n 1)" "$event_backup"
	python3 -c '
import sys
from pathlib import Path

sys.path.insert(0, sys.argv[2])
import cockpit_control

events = sorted((Path(sys.argv[1]) / cockpit_control.EVENTS_DIR_NAME).iterdir())
events[0].write_text("{\"schema_version\": " + "9" * 5000 + "}\n")
' "$root" "$MODULE_DIR"
	for subcommand in command-status validate list-events replay-ledger; do
		code=0
		"$CONTROL_BIN" "$subcommand" >"$out" 2>"$err" || code=$?
		[ "$code" -ne 0 ]
		grep -q "^cockpit-control: malformed events/" "$err"
		grep -Fq "4300 digits" "$err"
		cc_no_traceback "$err"
	done

	python3 -c '
import sys
from pathlib import Path

sys.path.insert(0, sys.argv[2])
import cockpit_control

events = sorted((Path(sys.argv[1]) / cockpit_control.EVENTS_DIR_NAME).iterdir())
events[0].write_text("[" * 20000 + "]" * 20000 + "\n")
' "$root" "$MODULE_DIR"
	for subcommand in command-status validate list-events replay-ledger; do
		code=0
		"$CONTROL_BIN" "$subcommand" >"$out" 2>"$err" || code=$?
		[ "$code" -ne 0 ]
		grep -Fq "nested too deeply to parse" "$err"
		cc_no_traceback "$err"
	done

	# Restore the future-versioned record so the no-mutation assertion below
	# still measures exactly what the earlier hand-edit established.
	cp "$event_backup" "$(find "$root/events" -type f | sort | head -n 1)"

	# A lifecycle-style half contract is refused in both directions.
	run python3 -c '
import json
import sys
from pathlib import Path

sys.path.insert(0, sys.argv[2])
import cockpit_control
from cockpit_control import ControlStoreError

control_id = str(__import__("uuid").uuid4())
timestamp = "2026-09-03T15:35:15Z"
digest = cockpit_control.command_payload_digest({"a": 1})
envelope = cockpit_control.build_command_envelope(
    command_id=str(__import__("uuid").uuid4()), command_type="dispatch",
    mission_id=str(__import__("uuid").uuid4()), queue_item_id="QI-1",
    target_kind="worker", target_id="worker-dev",
    trace_id=str(__import__("uuid").uuid4()), payload_digest=digest,
    control_root="/control", created_at=timestamp,
)
acknowledgement = cockpit_control.build_command_acknowledgement(
    envelope["command_id"], digest, "accepted", "worker-dev", acknowledged_at=timestamp,
)


def refused(record, fragment):
    try:
        cockpit_control.validate_event(record, control_id, "event")
    except ControlStoreError as error:
        assert fragment in str(error), (fragment, str(error))
        return
    raise AssertionError("accepted %s" % (fragment,))


base = {
    "schema_version": 1, "record_type": "event", "event_id": str(__import__("uuid").uuid4()),
    "control_id": control_id, "timestamp": timestamp, "revision": 1,
    "event_type": "command-registered", "actor": "cockpit-control", "payload": {},
}
refused(dict(base), "without a payload.command_envelope record")
refused(
    dict(base, event_type="command-acknowledged-accepted"),
    "without a payload.command_acknowledgement record",
)
refused(
    dict(base, event_type="mission-dispatched", payload={"command_envelope": envelope}),
    "expected event_type '"'"'command-registered'"'"'",
)
refused(
    dict(base, event_type="command-acknowledged-applied",
         payload={"command_acknowledgement": acknowledgement}),
    "expected event_type '"'"'command-acknowledged-accepted'"'"'",
)
refused(
    dict(base, payload={"command_envelope": dict(envelope, command_type="")}),
    "requires non-empty command_type",
)
cockpit_control.validate_event(dict(base, payload={"command_envelope": envelope}), control_id)
print("every half command contract was refused")
' "$root" "$MODULE_DIR"
	[ "$status" -eq 0 ]
	echo "$output" | grep -Fq "every half command contract was refused"
	[ "$before" = "$(cc_control_contents "$root")" ]
}

@test "malformed command arguments are refused before any event is committed" {
	local root="$BATS_TEST_TMPDIR/command-arguments"
	cc_command_store "$root"
	local command_id mission trace digest
	command_id="$(cc_uuid)"
	mission="$(cc_uuid)"
	trace="$(cc_uuid)"
	digest="$(cc_digest '{"a":1}')"
	local out="$BATS_TEST_TMPDIR/arguments-stdout" err="$BATS_TEST_TMPDIR/arguments-stderr"
	local before
	before="$(cc_control_contents "$root")"

	cc_refuse register-command "$out" "$err" --command-id "$command_id" --type dispatch \
		--mission "$mission" --queue-item QI-8 --target worker-dev --trace "$trace"
	grep -Fq "a command payload digest is required; pass --payload or --digest" "$err"

	cc_refuse register-command "$out" "$err" --command-id "$command_id" --type dispatch \
		--mission "$mission" --queue-item QI-8 --target worker-dev --trace "$trace" \
		--payload '{"a":1}' --digest "$digest"
	grep -Fq -e "--payload and --digest cannot be combined" "$err"

	cc_refuse register-command "$out" "$err" --command-id "$command_id" --type dispatch \
		--mission "$mission" --queue-item QI-8 --target worker-dev --trace "$trace" \
		--payload 'not-json'
	grep -Fq "command payload is not valid JSON" "$err"

	cc_refuse register-command "$out" "$err" --command-id "$command_id" --type dispatch \
		--mission "$mission" --queue-item QI-8 --target worker-dev --trace "$trace" \
		--payload '[1,2]'
	grep -Fq "command payload must be a JSON object of structured metadata" "$err"

	cc_refuse register-command "$out" "$err" --command-id not-a-uuid --type dispatch \
		--mission "$mission" --queue-item QI-8 --target worker-dev --trace "$trace" \
		--payload '{"a":1}'
	grep -Fq "requires UUID command_id" "$err"

	# A payload nested more deeply than the interpreter can walk is a refusal
	# with a diagnostic, never a RecursionError traceback leaking a real path.
	local nested
	nested="$(python3 -c 'print("{\"a\":" * 2000 + "1" + "}" * 2000)')"
	cc_refuse register-command "$out" "$err" --command-id "$command_id" --type dispatch \
		--mission "$mission" --queue-item QI-8 --target worker-dev --trace "$trace" \
		--payload "$nested"
	grep -Fq "command payload is nested too deeply to serialize canonically" "$err"
	nested="$(python3 -c 'print("{\"a\":" * 20000 + "1" + "}" * 20000)')"
	cc_refuse register-command "$out" "$err" --command-id "$command_id" --type dispatch \
		--mission "$mission" --queue-item QI-8 --target worker-dev --trace "$trace" \
		--payload "$nested"
	grep -Fq "command payload is nested too deeply to parse" "$err"

	# An integer literal longer than CPython's 4300-digit int->str conversion
	# limit is refused by `json.loads` as a bare ValueError, not as a
	# JSONDecodeError, so it escapes a decoder-only guard and prints a traceback
	# naming real filesystem paths.  Both command entry points refuse it as a
	# diagnostic instead, on a top-level, a negative, and a nested literal.
	local huge nested_huge negative_huge
	huge="$(python3 -c 'print("{\"a\":" + "9" * 5000 + "}")')"
	negative_huge="$(python3 -c 'print("{\"a\":-" + "9" * 5000 + "}")')"
	nested_huge="$(python3 -c 'print("{\"a\":{\"b\":" + "9" * 5000 + "}}")')"
	local hostile
	for hostile in "$huge" "$negative_huge" "$nested_huge"; do
		cc_refuse register-command "$out" "$err" --command-id "$command_id" --type dispatch \
			--mission "$mission" --queue-item QI-8 --target worker-dev --trace "$trace" \
			--payload "$hostile"
		grep -Fq "command payload is not valid JSON" "$err"
		grep -Fq "4300 digits" "$err"

		cc_refuse acknowledge-command "$out" "$err" --command-id "$command_id" \
			--outcome accepted --by worker-dev --payload "$hostile"
		grep -Fq "command payload is not valid JSON" "$err"
	done

	# The same fail-closed contract holds for the pre-existing event payload
	# path, which shares the parser.
	cc_refuse publish-event "$out" "$err" --type command-probe --payload "$huge"
	grep -Fq "event payload is not valid JSON" "$err"

	cc_refuse command-status "$out" "$err" --command-id not-a-uuid
	grep -Fq "requires UUID command_id" "$err"

	cc_refuse register-command "$out" "$err" --command-id "$command_id" --type dispatch \
		--mission "$mission" --queue-item QI-8 --target worker-dev --trace "$trace" \
		--target-kind pane --payload '{"a":1}'
	grep -Fq "declares unknown target kind 'pane'" "$err"

	cc_refuse register-command "$out" "$err" --command-id "$command_id" --type dispatch \
		--mission "$mission" --queue-item QI-8 --target worker-dev --trace "$trace" \
		--payload '{"a":1}' --implementation-root relative/root
	grep -Fq "must be an absolute path" "$err"

	cc_refuse register-command "$out" "$err" --command-id "$command_id" --type dispatch \
		--mission "$mission" --queue-item QI-8 --target worker-dev --trace "$trace" \
		--payload '{"a":1}' --runtime-boundary untyped
	grep -Fq "must be a typed '<type>:<value>' boundary reference" "$err"

	cc_refuse register-command "$out" "$err" --command-id "$command_id" --type dispatch \
		--mission "$mission" --queue-item QI-8 --target worker-dev --trace "$trace" \
		--payload '{"a":1}' --deadline 2000-01-01T00:00:00Z
	grep -Fq "requires deadline_at after created_at" "$err"

	cc_refuse acknowledge-command "$out" "$err" --command-id "$command_id" --outcome applied \
		--by worker-dev --digest "$digest" --result test:RUN-1
	grep -Fq "which this control root has never registered" "$err"

	cc_refuse acknowledge-command "$out" "$err" --command-id "$command_id" --outcome applied \
		--by worker-dev --digest "$digest"
	grep -Fq "requires at least one result reference" "$err"

	cc_refuse acknowledge-command "$out" "$err" --command-id "$command_id" --outcome rejected \
		--by worker-dev --digest "$digest"
	grep -Fq "requires a reason for outcome 'rejected'" "$err"

	cc_refuse acknowledge-command "$out" "$err" --command-id "$command_id" --outcome unknown \
		--by worker-dev --digest "$digest"
	grep -Fq "declares unknown outcome 'unknown'" "$err"

	cc_refuse acknowledge-command "$out" "$err" --command-id "$command_id" --outcome accepted \
		--by worker-dev --digest sha256:not-a-digest
	grep -Fq "lowercase hex digits" "$err"

	[ "$before" = "$(cc_control_contents "$root")" ]
	run "$CONTROL_BIN" list-events
	[ "$status" -eq 0 ]
	echo "$output" | grep -Fq "0 committed events in $root (latest revision 0)"

	# Ordering refusals are equally fail-closed once a command does exist.
	"$CONTROL_BIN" register-command --command-id "$command_id" --type dispatch \
		--mission "$mission" --queue-item QI-8 --target worker-dev --trace "$trace" \
		--payload '{"a":1}' >/dev/null
	before="$(cc_control_contents "$root")"

	cc_refuse acknowledge-command "$out" "$err" --command-id "$command_id" --outcome applied \
		--by worker-dev --digest "$digest" --result test:RUN-1
	grep -Fq "is registered; a 'applied' acknowledgement is not allowed from that status (allowed: accepted, duplicate, rejected)" "$err"

	cc_refuse acknowledge-command "$out" "$err" --command-id "$command_id" --outcome accepted \
		--by worker-dev --digest "$(cc_digest '{"a":2}')"
	grep -Fq "only a 'rejected' acknowledgement may name a different digest" "$err"

	[ "$before" = "$(cc_control_contents "$root")" ]
	run "$CONTROL_BIN" validate
	[ "$status" -eq 0 ]
}

@test "worker-controlled strings cannot forge a command record boundary" {
	local root="$BATS_TEST_TMPDIR/command-injection"
	cc_command_store "$root"
	local command_id mission trace digest forged
	command_id="$(cc_uuid)"
	mission="$(cc_uuid)"
	trace="$(cc_uuid)"
	digest="$(cc_digest '{"a":1}')"
	local out="$BATS_TEST_TMPDIR/injection-stdout" err="$BATS_TEST_TMPDIR/injection-stderr"

	"$CONTROL_BIN" register-command --command-id "$command_id" --type dispatch \
		--mission "$mission" --queue-item QI-9 --target worker-dev --trace "$trace" \
		--payload '{"a":1}' >/dev/null
	local before
	before="$(cc_control_contents "$root")"

	# One fully-formed spoofed command record, exactly as an untrusted worker
	# would append it to the line-oriented payload if strings were rendered
	# verbatim.
	forged=$'worker-evil\nSPOOFED command 00000000-0000-4000-8000-000000000000 type dispatch status applied'

	cc_refuse register-command "$out" "$err" --command-id "$(cc_uuid)" --type dispatch \
		--mission "$mission" --queue-item QI-9 --target "$forged" --trace "$trace" \
		--payload '{"a":1}'
	grep -Fq "requires printable non-whitespace ASCII id" "$err"
	grep -Fq "could forge a record boundary" "$err"
	cc_absent "^SPOOFED" "$err"

	cc_refuse register-command "$out" "$err" --command-id "$(cc_uuid)" --type "$forged" \
		--mission "$mission" --queue-item QI-9 --target worker-dev --trace "$trace" \
		--payload '{"a":1}'
	grep -Fq "requires printable non-whitespace ASCII command_type" "$err"

	cc_refuse register-command "$out" "$err" --command-id "$(cc_uuid)" --type dispatch \
		--mission "$mission" --queue-item "$forged" --target worker-dev --trace "$trace" \
		--payload '{"a":1}'
	grep -Fq "requires printable non-whitespace ASCII queue_item_id" "$err"

	# A declared mission boundary is worker-supplied too, so a control character
	# in a declared root can never reach the payload either.
	cc_refuse register-command "$out" "$err" --command-id "$(cc_uuid)" --type dispatch \
		--mission "$mission" --queue-item QI-9 --target worker-dev --trace "$trace" \
		--payload '{"a":1}' --implementation-root $'/tmp/forged\nSPOOFED command'
	grep -Fq "contains a control character that could forge a record boundary" "$err"
	cc_absent "^SPOOFED" "$err"

	cc_refuse register-command "$out" "$err" --command-id "$(cc_uuid)" --type dispatch \
		--mission "$mission" --queue-item QI-9 --target worker-dev --trace "$trace" \
		--payload '{"a":1}' --runtime-boundary $'deploy:none\nSPOOFED boundary'
	grep -Fq "must not contain whitespace" "$err"

	# The acknowledgement half is constrained on exactly the same ground.
	cc_refuse acknowledge-command "$out" "$err" --command-id "$command_id" --outcome accepted \
		--by "$forged" --digest "$digest"
	grep -Fq "requires printable non-whitespace ASCII acknowledged_by" "$err"
	cc_refuse acknowledge-command "$out" "$err" --command-id "$command_id" --outcome accepted \
		--by $'worker\tdev' --digest "$digest"
	grep -Fq "requires printable non-whitespace ASCII acknowledged_by" "$err"
	cc_refuse acknowledge-command "$out" "$err" --command-id "$command_id" --outcome applied \
		--by worker-dev --digest "$digest" --result $'test:RUN\n1'
	grep -Fq "must not contain whitespace" "$err"

	# The event actor is rendered verbatim by `list-events`, so both command
	# entry points constrain it too.
	cc_refuse register-command "$out" "$err" --command-id "$(cc_uuid)" --type dispatch \
		--mission "$mission" --queue-item QI-9 --target worker-dev --trace "$trace" \
		--payload '{"a":1}' --actor "$forged"
	grep -Fq "requires printable non-whitespace ASCII actor" "$err"
	cc_refuse acknowledge-command "$out" "$err" --command-id "$command_id" \
		--outcome accepted --by worker-dev --digest "$digest" --actor "$forged"
	grep -Fq "requires printable non-whitespace ASCII actor" "$err"

	# Nothing was committed and the forged boundary never becomes a second
	# record: exactly one command is reported and nothing claims to be applied.
	[ "$before" = "$(cc_control_contents "$root")" ]
	"$CONTROL_BIN" command-status >"$out" 2>"$err"
	[ "$(grep -c "^command " "$out")" -eq 1 ]
	cc_absent "SPOOFED" "$out"
	cc_absent "status applied" "$out"
	grep -Fq "1 command(s) in $root" "$out"

	# Free-text prose is not constrained, so it is instead kept out of every
	# parseable stdout position: a forged reason reaches the JSON records only.
	"$CONTROL_BIN" acknowledge-command --command-id "$command_id" --outcome rejected \
		--by worker-dev --digest "$digest" --reason "$forged" >/dev/null
	"$CONTROL_BIN" command-status >"$out" 2>"$err"
	cc_absent "SPOOFED" "$out"
	cc_absent "SPOOFED" "$err"
	[ "$(grep -c "^command " "$out")" -eq 1 ]
	grep -Fq "outcome rejected by worker-dev" "$out"
	run python3 -c '
import json
import sys
from pathlib import Path

sys.path.insert(0, sys.argv[2])
import cockpit_control

root = Path(sys.argv[1])
slot = json.loads((root / cockpit_control.LEDGER_NAME).read_text())[
    cockpit_control.LEDGER_COMMANDS_FIELD
][sys.argv[3]]
reason = slot["acknowledgements"][0]["acknowledgement"]["reason"]
assert "SPOOFED" in reason and "\n" in reason, reason
print("free text is retained in the record and never in the payload")
' "$root" "$MODULE_DIR" "$command_id"
	[ "$status" -eq 0 ]
	echo "$output" | grep -Fq "free text is retained in the record and never in the payload"
}

@test "the register-command and acknowledge-command dry runs report their decisions and change no state" {
	local root="$BATS_TEST_TMPDIR/command-dry-run"
	cc_command_store "$root"
	local command_id fresh mission trace digest
	command_id="$(cc_uuid)"
	fresh="$(cc_uuid)"
	mission="$(cc_uuid)"
	trace="$(cc_uuid)"
	digest="$(cc_digest '{"a":1}')"

	"$CONTROL_BIN" register-command --command-id "$command_id" --type dispatch \
		--mission "$mission" --queue-item QI-10 --target worker-dev --trace "$trace" \
		--payload '{"a":1}' >/dev/null
	local before
	before="$(cc_control_contents "$root")"

	run "$CONTROL_BIN" register-command --command-id "$fresh" --type dispatch \
		--mission "$mission" --queue-item QI-10 --target worker-dev --trace "$trace" \
		--payload '{"a":1}' --dry-run
	[ "$status" -eq 0 ]
	echo "$output" | grep -Fq "at revision 2; no state changed"
	echo "$output" | grep -Fq "command $fresh would be registered with digest $digest"
	[ "$before" = "$(cc_control_contents "$root")" ]

	run "$CONTROL_BIN" register-command --command-id "$command_id" --type dispatch \
		--mission "$mission" --queue-item QI-10 --target worker-dev --trace "$trace" \
		--payload '{"a":1}' --dry-run
	[ "$status" -eq 0 ]
	echo "$output" | grep -Fq "would be acknowledged duplicate; the stored result is returned without applying it again (status registered)"
	[ "$before" = "$(cc_control_contents "$root")" ]

	run "$CONTROL_BIN" register-command --command-id "$command_id" --type dispatch \
		--mission "$mission" --queue-item QI-10 --target worker-dev --trace "$trace" \
		--payload '{"a":2}' --dry-run
	[ "$status" -ne 0 ]
	echo "$output" | grep -Fq "would be refused as a digest-conflict"
	[ "$before" = "$(cc_control_contents "$root")" ]

	run "$CONTROL_BIN" acknowledge-command --command-id "$command_id" --outcome accepted \
		--by worker-dev --digest "$digest" --dry-run
	[ "$status" -eq 0 ]
	echo "$output" | grep -Fq "would be acknowledged accepted by worker-dev (status accepted)"
	[ "$before" = "$(cc_control_contents "$root")" ]

	run "$CONTROL_BIN" list-events
	[ "$status" -eq 0 ]
	echo "$output" | grep -Fq "1 committed events in $root (latest revision 1)"
	run "$CONTROL_BIN" command-status
	[ "$status" -eq 0 ]
	echo "$output" | grep -Fq "status registered deliveries 1 acknowledgements 0 conflicts 0"
}

@test "a hand-edited ledger command slot is refused as derived corruption and replayed" {
	local root="$BATS_TEST_TMPDIR/command-ledger-repair"
	cc_command_store "$root"
	local command_id mission trace digest
	command_id="$(cc_uuid)"
	mission="$(cc_uuid)"
	trace="$(cc_uuid)"
	digest="$(cc_digest '{"a":1}')"

	"$CONTROL_BIN" register-command --command-id "$command_id" --type dispatch \
		--mission "$mission" --queue-item QI-11 --target worker-dev --trace "$trace" \
		--payload '{"a":1}' >/dev/null
	"$CONTROL_BIN" acknowledge-command --command-id "$command_id" --outcome accepted \
		--by worker-dev --digest "$digest" >/dev/null

	local projected
	projected="$(cat "$root/ledger.json")"

	# A ledger claiming a command applied that no committed acknowledgement
	# published is derived corruption: it never becomes authority.
	python3 -c '
import json
import sys

path = sys.argv[1]
ledger = json.load(open(path))
ledger["commands"][sys.argv[2]]["status"] = "applied"
with open(path, "w") as handle:
    handle.write(json.dumps(ledger, indent=2, sort_keys=True) + "\n")
' "$root/ledger.json" "$command_id"

	run "$CONTROL_BIN" command-status
	[ "$status" -eq 0 ]
	echo "$output" | grep -Fq "status accepted deliveries 1"
	cc_absent_output "status applied"

	run "$CONTROL_BIN" replay-ledger
	[ "$status" -eq 0 ]
	echo "$output" | grep -Fq "rebuilt ledger.json at revision 2"
	[ "$projected" = "$(cat "$root/ledger.json")" ]

	# A ledger that drops the materialized command slots altogether is refused
	# rather than silently defaulted to an empty projection, and one replay
	# heals it from the committed events.
	python3 -c '
import json
import sys

path = sys.argv[1]
ledger = json.load(open(path))
del ledger["commands"]
with open(path, "w") as handle:
    handle.write(json.dumps(ledger, indent=2, sort_keys=True) + "\n")
' "$root/ledger.json"

	run "$CONTROL_BIN" validate
	[ "$status" -ne 0 ]
	echo "$output" | grep -Fq "ledger.json requires commands"

	run "$CONTROL_BIN" replay-ledger
	[ "$status" -eq 0 ]
	[ "$projected" = "$(cat "$root/ledger.json")" ]

	run "$CONTROL_BIN" validate
	[ "$status" -eq 0 ]

	# An invented acknowledgement in the derived projection is refused too.
	python3 -c '
import json
import sys

path = sys.argv[1]
ledger = json.load(open(path))
slot = ledger["commands"][sys.argv[2]]
entry = json.loads(json.dumps(slot["acknowledgements"][0]))
entry["acknowledgement"]["outcome"] = "applied"
entry["acknowledgement"]["result_refs"] = []
slot["acknowledgements"].append(entry)
with open(path, "w") as handle:
    handle.write(json.dumps(ledger, indent=2, sort_keys=True) + "\n")
' "$root/ledger.json" "$command_id"

	run "$CONTROL_BIN" validate
	[ "$status" -ne 0 ]
	echo "$output" | grep -Fq "requires at least one result reference"

	run "$CONTROL_BIN" replay-ledger
	[ "$status" -eq 0 ]
	[ "$projected" = "$(cat "$root/ledger.json")" ]
}

@test "concurrent redelivery of one command ID applies exactly one registration" {
	local root="$BATS_TEST_TMPDIR/command-concurrency"
	cc_command_store "$root"
	local command_id mission trace
	command_id="$(cc_uuid)"
	mission="$(cc_uuid)"
	trace="$(cc_uuid)"

	run python3 -c '
import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, sys.argv[2])
import cockpit_control

root = Path(sys.argv[1])
control_bin = sys.argv[3]
command_id, mission, trace = sys.argv[4], sys.argv[5], sys.argv[6]
digest = cockpit_control.command_payload_digest({"instruction": "deliver once"})

environment = dict(__import__("os").environ)
environment["COCKPIT_CONTROL_ROOT"] = str(root)
# Contention is resolved by the control lock and the deterministic fold, so the
# only timing this test needs is a bound generous enough not to expire.
environment["COCKPIT_CONTROL_LOCK_TIMEOUT_SECONDS"] = "60"


def deliver(payload):
    return [
        control_bin, "register-command", "--command-id", command_id, "--type", "dispatch",
        "--mission", mission, "--queue-item", "QI-12", "--target", "worker-dev",
        "--trace", trace, "--payload", payload,
    ]


def run_together(commands):
    processes = [
        subprocess.Popen(
            argv, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=environment
        )
        for argv in commands
    ]
    results = []
    for process in processes:
        stdout, stderr = process.communicate(timeout=120)
        results.append((process.returncode, stdout, stderr))
    return results


# Four real processes deliver the identical command at once.
results = run_together([deliver("{\"instruction\": \"deliver once\"}")] * 4)
for code, stdout, stderr in results:
    assert code == 0, (code, stdout, stderr)
    assert "Traceback" not in stderr, stderr
registered = [result for result in results if "is registered with digest" in result[1]]
duplicates = [
    result for result in results
    if "acknowledged duplicate" in result[1] or "duplicate delivery retained" in result[1]
]
assert len(registered) == 1, [result[1] for result in results]
assert len(registered) + len(duplicates) == 4, [result[1] for result in results]

control_id = json.loads((root / cockpit_control.CONTROL_METADATA_NAME).read_text())["control_id"]
history = cockpit_control.read_committed_events(root, control_id)
assert len(history.events) == 4, history.events
slots, outcomes = cockpit_control.fold_commands(history.events)
assert sorted(slots) == [command_id], slots
applied = [
    outcome for outcome in outcomes.values()
    if outcome == (True, cockpit_control.COMMAND_FOLD_REGISTERED)
]
assert len(applied) == 1, outcomes
slot = slots[command_id]
assert slot["status"] == cockpit_control.COMMAND_STATUS_REGISTERED, slot
assert slot["envelope"]["payload_digest"] == digest
assert slot["conflicts"] == [], slot
assert len(slot["deliveries"]) + len(slot["acknowledgements"]) == 4, slot
assert all(
    entry["acknowledgement"]["outcome"] == cockpit_control.COMMAND_DUPLICATE
    for entry in slot["acknowledgements"]
), slot

# The same determinism decides application: several workers race to apply the
# one accepted command and exactly one acknowledgement is materialized.
accept = subprocess.run(
    [
        control_bin, "acknowledge-command", "--command-id", command_id, "--outcome",
        "accepted", "--by", "worker-dev", "--digest", digest,
    ],
    stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=environment, timeout=120,
)
assert accept.returncode == 0, accept.stderr

apply_argv = [
    control_bin, "acknowledge-command", "--command-id", command_id, "--outcome", "applied",
    "--by", "worker-dev", "--digest", digest, "--result", "report:worker-dev/mission/1",
]
results = run_together([apply_argv] * 4)
for code, stdout, stderr in results:
    assert "Traceback" not in stderr, stderr
succeeded = [result for result in results if result[0] == 0]
assert len(succeeded) == 1, [(result[0], result[1]) for result in results]

slots, _outcomes = cockpit_control.fold_commands(
    cockpit_control.read_committed_events(root, control_id).events
)
slot = slots[command_id]
assert slot["status"] == cockpit_control.COMMAND_APPLIED, slot
applied_entries = [
    entry for entry in slot["acknowledgements"]
    if entry["acknowledgement"]["outcome"] == cockpit_control.COMMAND_APPLIED
]
assert len(applied_entries) == 1, applied_entries
print("exactly one concurrent delivery registered and exactly one applied")
' "$root" "$MODULE_DIR" "$CONTROL_BIN" "$command_id" "$mission" "$trace"
	[ "$status" -eq 0 ]
	echo "$output" | grep -Fq "exactly one concurrent delivery registered and exactly one applied"

	run "$CONTROL_BIN" replay-ledger
	[ "$status" -eq 0 ]
	run "$CONTROL_BIN" validate
	[ "$status" -eq 0 ]
	run "$CONTROL_BIN" preflight
	[ "$status" -eq 0 ]
	echo "$output" | grep -Fq "preflight ready"
}
