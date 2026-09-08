#!/usr/bin/env bats
# tests/unit/cmd-control-mission.bats — TH3.E2.US3 managed questions,
# cancellation, and replacement.
#
# The contract under test has three halves:
#
#   * a question, a reply, and an access-prompt response are durable commands
#     correlated to one active mission, its worker, its queue item, and a child
#     trace, and they are answerable only through the protocol: never through an
#     unscoped temporary file, never through pane input, and never through the
#     generic delivery surface;
#   * cooperative cancellation has a durably observable outcome, evaluated from
#     committed evidence and one explicit as-of moment, that is either an
#     acknowledgement or an explicitly declared timeout, and a timeout is a
#     recoverable observation that never claims a mission failure;
#   * replacement terminates the prior mission with the existing `replaced`
#     lifecycle state, links it through `superseded_by_mission_id`, claims the
#     worker's single mission slot for one explicit new mission ID, and refuses
#     and durably records a conflict rather than ever creating a second one.
#
# Nothing here waits on wall-clock timing: every deadline assertion uses an
# explicit `--as-of` against an explicit `--requested-at`, and the concurrency
# proof starts real processes and lets the control lock and the deterministic
# fold order them.

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

# cc_mission_store <root> — initialize one empty control store under <root>.
cc_mission_store() {
	export COCKPIT_CONTROL_ROOT="$1"
	export COCKPIT_QUEUE_ROOT="$1-queue"
	"$CONTROL_BIN" init \
		--queue-root "$1-queue" \
		--planning-root "$1-planning" \
		--implementation-root "$1-implementation" >/dev/null
}

# cc_emit <record-lifecycle args...> — emit one lifecycle event successfully.
cc_emit() {
	run "$CONTROL_BIN" record-lifecycle "$@"
	[ "$status" -eq 0 ]
}

# cc_running <worker> <mission> <queue-item> <trace> — bring one mission to a
# materialized `running` state through the ordinary US1 lifecycle path.
cc_running() {
	cc_emit --state accepted --worker "$1" --mission "$2" --queue-item "$3" \
		--trace "$4" --sequence 1 --fresh-for 3600
	cc_emit --state running --worker "$1" --mission "$2" --queue-item "$3" \
		--trace "$4" --sequence 2 --fresh-for 3600
}

# cc_digest_of <command-id> — print the digest one registered command carries.
cc_digest_of() {
	"$CONTROL_BIN" command-status --command-id "$1" |
		grep "^command $1 " | sed 's/.* digest \([^ ]*\) .*/\1/'
}

# cc_ack <command-id> <outcome> <by> [extra args...] — acknowledge one command
# with the digest it was actually registered with.
cc_ack() {
	local command_id="$1" outcome="$2" by="$3"
	shift 3
	run "$CONTROL_BIN" acknowledge-command --command-id "$command_id" \
		--outcome "$outcome" --by "$by" --digest "$(cc_digest_of "$command_id")" "$@"
	[ "$status" -eq 0 ]
}

# A replacement request alone never stops accepted work. Test workers explicitly
# acknowledge inspection/application before tests inspect the replacement slot.
cc_apply_replacement() {
	cc_ack "$1" accepted "$2"
	cc_ack "$1" applied "$2" --result "test:cooperative-replacement"
}

@test "a mission question is a durable command correlated to the active mission and its trace" {
	local root="$BATS_TEST_TMPDIR/mission-question"
	cc_mission_store "$root"
	local mission trace prompt dialog_trace
	mission="$(cc_uuid)"
	trace="$(cc_uuid)"
	prompt="$(cc_uuid)"
	dialog_trace="$(cc_uuid)"
	cc_running worker-dev "$mission" QI-1 "$trace"
	cc_emit --state blocked --worker worker-dev --mission "$mission" --queue-item QI-1 \
		--trace "$trace" --sequence 3 --fresh-for 3600 --reason "needs a decision" \
		--blocker-category question

	run "$CONTROL_BIN" raise-question --command-id "$prompt" --worker worker-dev \
		--mission "$mission" --queue-item QI-1 --trace "$dialog_trace" \
		--parent-trace "$trace" --category architecture-decision \
		--body-ref "report:worker-dev/$mission/3" \
		--payload '{"question":"which adapter should the reader use?"}'
	[ "$status" -eq 0 ]
	echo "$output" | grep -Fq "command $prompt type mission-question mission $mission queue-item QI-1 target worker/worker-dev trace $dialog_trace parent-trace $trace"
	echo "$output" | grep -Fq "dialog $prompt kind question state pending mission $mission worker worker-dev queue-item QI-1 trace $dialog_trace parent-trace $trace category architecture-decision answers - answered-by - answered-at -"
	echo "$output" | grep -Fq "body-refs report:worker-dev/$mission/3 schema-version 1"
	echo "$output" | grep -Fq "slot worker-dev mission $mission queue-item QI-1 state active"

	# The prompt and the command envelope that carries it are one committed
	# event, and the question body itself is stored nowhere at all.
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
event = history.events[-1]
assert event.record["event_type"] == cockpit_control.COMMAND_REGISTERED_EVENT_TYPE, event.record
field, dialog = cockpit_control.event_mission_control(event.record, event.path.name)
assert field == cockpit_control.MISSION_DIALOG_PAYLOAD_FIELD, field
envelope = cockpit_control.event_command_envelope(event.record, event.path.name)
assert envelope["command_id"] == dialog["command_id"] == sys.argv[3], (envelope, dialog)
assert envelope["command_type"] == cockpit_control.COMMAND_TYPE_MISSION_QUESTION
assert dialog["schema_version"] == cockpit_control.MISSION_CONTROL_SCHEMA_VERSION
assert dialog["record_type"] == cockpit_control.MISSION_DIALOG_RECORD_TYPE
assert dialog["kind"] == cockpit_control.MISSION_DIALOG_QUESTION
assert dialog["mission_id"] == sys.argv[4]
assert dialog["worker_id"] == "worker-dev"
assert dialog["queue_item_id"] == "QI-1"
assert dialog["trace_id"] == sys.argv[5]
assert dialog["parent_trace_id"] == sys.argv[6]
assert dialog["category"] == "architecture-decision"
assert dialog["answers_command_id"] is None
assert dialog["body_refs"] == ["report:worker-dev/%s/3" % sys.argv[4]]
UUID(dialog["command_id"])
UUID(dialog["trace_id"])

ledger = json.loads((root / cockpit_control.LEDGER_NAME).read_text())
slot = ledger[cockpit_control.LEDGER_MISSION_DIALOGS_FIELD][sys.argv[3]]
assert slot["dialog"] == dialog, slot
assert slot["state"] == cockpit_control.MISSION_DIALOG_PENDING
assert slot["answered_by_command_id"] is None and slot["answered_at"] is None
worker_slot = ledger[cockpit_control.LEDGER_MISSION_SLOTS_FIELD]["worker-dev"]
assert worker_slot["mission_id"] == sys.argv[4]
assert worker_slot["state"] == cockpit_control.MISSION_SLOT_ACTIVE
print("the prompt is correlated metadata, not a body")
' "$root" "$MODULE_DIR" "$prompt" "$mission" "$dialog_trace" "$trace"
	[ "$status" -eq 0 ]
	echo "$output" | grep -Fq "the prompt is correlated metadata, not a body"

	# ADR-016 keeps canonical records metadata-only: the question body is not in
	# the event, the ledger, or anywhere else under the control root.
	run grep -rl "which adapter should the reader use" "$root"
	[ "$status" -ne 0 ]

	run "$CONTROL_BIN" validate
	[ "$status" -eq 0 ]
}

@test "a mission prompt is answerable only through the protocol" {
	local root="$BATS_TEST_TMPDIR/mission-protocol-only"
	cc_mission_store "$root"
	local mission trace prompt
	mission="$(cc_uuid)"
	trace="$(cc_uuid)"
	prompt="$(cc_uuid)"
	cc_running worker-dev "$mission" QI-2 "$trace"
	"$CONTROL_BIN" raise-question --command-id "$prompt" --worker worker-dev \
		--mission "$mission" --queue-item QI-2 --trace "$(cc_uuid)" \
		--category filesystem-access --body-ref "report:worker-dev/$mission/2" \
		--payload '{"question":"may I read /etc?"}' >/dev/null

	local out="$BATS_TEST_TMPDIR/protocol-stdout" err="$BATS_TEST_TMPDIR/protocol-stderr"
	local before
	before="$(cc_control_contents "$root")"

	# The generic delivery surface cannot mint any managed mission command, so a
	# reply can never arrive as a bare envelope correlated to nothing.
	local managed
	for managed in mission-question mission-access-prompt mission-reply \
		mission-access-prompt-response mission-cancel mission-replace; do
		cc_refuse register-command "$out" "$err" --command-id "$(cc_uuid)" \
			--type "$managed" --mission "$mission" --queue-item QI-2 --target worker-dev \
			--trace "$(cc_uuid)" --payload '{"a":1}'
		grep -Fq "is a managed mission command and cannot be delivered through cockpit-control register-command" "$err"
	done

	# A hand-published event cannot carry half of the contract either: a dialog
	# record without the command envelope that delivers it is refused, and so is
	# a command-registered event with neither.
	local forged_dialog
	forged_dialog="{\"mission_dialog\": {\"schema_version\": 1, \"record_type\": \"mission-dialog\", \"command_id\": \"$prompt\", \"kind\": \"reply\", \"mission_id\": \"$mission\", \"worker_id\": \"worker-dev\", \"queue_item_id\": \"QI-2\", \"trace_id\": \"$(cc_uuid)\", \"parent_trace_id\": null, \"answers_command_id\": \"$prompt\", \"category\": \"x\", \"body_refs\": [\"note:x\"], \"raised_at\": \"2026-01-01T00:00:00Z\"}}"
	cc_refuse publish-event "$out" "$err" --type mission-answer-probe --payload "$forged_dialog"
	grep -Fq "carries a payload.mission_dialog record without a payload.command_envelope command envelope" "$err"
	cc_refuse publish-event "$out" "$err" --type command-registered --payload "$forged_dialog"
	grep -Fq "declares event_type 'command-registered' without a payload.command_envelope record" "$err"

	# The other half of the same contract is enforced in the other direction: a
	# well-formed command envelope that declares a managed mission command type
	# but carries no correlated mission-control record at all is refused, so a
	# hand-published event can never mint a managed command correlated to
	# nothing.  Without this the envelope alone would commit and the reply would
	# answer no prompt, on no mission, for no worker.
	local forged_envelope
	forged_envelope="{\"command_envelope\": {\"schema_version\": 1, \"record_type\": \"command-envelope\", \"command_id\": \"$(cc_uuid)\", \"command_type\": \"mission-reply\", \"mission_id\": \"$mission\", \"queue_item_id\": \"QI-2\", \"target\": {\"kind\": \"worker\", \"id\": \"worker-dev\"}, \"trace_id\": \"$(cc_uuid)\", \"parent_trace_id\": null, \"payload_digest\": \"sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa\", \"boundaries\": {\"control_root\": \"$root\", \"queue_root\": null, \"planning_root\": null, \"implementation_roots\": [], \"runtime_boundaries\": []}, \"created_at\": \"2026-01-01T00:00:00.000000Z\", \"deadline_at\": null}}"
	cc_refuse publish-event "$out" "$err" --type command-registered --payload "$forged_envelope"
	grep -Fq "declares managed command_type 'mission-reply' without its correlated mission-control record" "$err"

	# An answer to a command this control root never raised is refused, so an
	# out-of-band identifier cannot become a mission answer.
	cc_refuse answer-question "$out" "$err" --command-id "$(cc_uuid)" \
		--answers "$(cc_uuid)" --by overseer --trace "$(cc_uuid)" \
		--category filesystem-access --body-ref "human-decision:x" --payload '{"answer":"no"}'
	grep -Fq "which this control root has never raised as a mission prompt" "$err"

	# Nothing above changed one byte of the store.
	[ "$before" = "$(cc_control_contents "$root")" ]
	run "$CONTROL_BIN" validate
	[ "$status" -eq 0 ]
}

@test "a pending question answered through the protocol is acknowledged and the worker returns to running" {
	local root="$BATS_TEST_TMPDIR/mission-happy-path"
	cc_mission_store "$root"
	local mission trace prompt answer prompt_trace answer_trace
	mission="$(cc_uuid)"
	trace="$(cc_uuid)"
	prompt="$(cc_uuid)"
	answer="$(cc_uuid)"
	prompt_trace="$(cc_uuid)"
	answer_trace="$(cc_uuid)"
	cc_running worker-dev "$mission" QI-3 "$trace"
	cc_emit --state blocked --worker worker-dev --mission "$mission" --queue-item QI-3 \
		--trace "$trace" --sequence 3 --fresh-for 3600 --reason "awaiting an answer" \
		--blocker-category question
	"$CONTROL_BIN" raise-question --command-id "$prompt" --worker worker-dev \
		--mission "$mission" --queue-item QI-3 --trace "$prompt_trace" --parent-trace "$trace" \
		--category architecture-decision --body-ref "report:worker-dev/$mission/3" \
		--payload '{"question":"which adapter?"}' >/dev/null

	run "$CONTROL_BIN" mission-status
	[ "$status" -eq 0 ]
	echo "$output" | grep -Fq "1 pending prompt(s)"
	echo "$output" | grep -Fq "it is answerable only through cockpit-control answer-question"

	run "$CONTROL_BIN" answer-question --command-id "$answer" --answers "$prompt" \
		--by overseer --trace "$answer_trace" --category architecture-decision \
		--body-ref "human-decision:$answer" --payload '{"answer":"use adapter B"}'
	[ "$status" -eq 0 ]
	echo "$output" | grep -Fq "command $answer type mission-reply mission $mission queue-item QI-3 target worker/worker-dev trace $answer_trace parent-trace $prompt_trace"
	echo "$output" | grep -Fq "dialog $prompt kind question state answered mission $mission"
	echo "$output" | grep -Fq "answered-by $answer"
	echo "$output" | grep -Fq "dialog $answer kind reply state delivered mission $mission"
	echo "$output" | grep -Fq "answers $prompt"

	# The worker acknowledges the answer through the ordinary US2 contract.
	cc_ack "$answer" accepted worker-dev
	cc_ack "$answer" applied worker-dev --result "report:worker-dev/$mission/4"
	run "$CONTROL_BIN" command-status --command-id "$answer"
	[ "$status" -eq 0 ]
	echo "$output" | grep -Fq "status applied deliveries 1 acknowledgements 2 conflicts 0"
	echo "$output" | grep -Fq "acknowledgement $answer outcome applied by worker-dev"

	# Only now may the worker legitimately return to running.
	cc_emit --state running --worker worker-dev --mission "$mission" --queue-item QI-3 \
		--trace "$trace" --sequence 4 --fresh-for 3600
	echo "$output" | grep -Fq "advanced the materialized mission state"

	run "$CONTROL_BIN" lifecycle-status --as-of 2026-01-01T00:00:00.000000Z
	[ "$status" -eq 0 ]
	echo "$output" | grep -Fq "state running sequence 4"

	run "$CONTROL_BIN" mission-status
	[ "$status" -eq 0 ]
	echo "$output" | grep -Fq "0 pending prompt(s)"
	cc_absent_output "state pending"

	run "$CONTROL_BIN" validate
	[ "$status" -eq 0 ]
	run "$CONTROL_BIN" replay-ledger
	[ "$status" -eq 0 ]
	echo "$output" | grep -Fq "projection is current"
}

@test "a reply to an unknown, already answered, or ended mission prompt is refused" {
	local root="$BATS_TEST_TMPDIR/mission-refused-answers"
	cc_mission_store "$root"
	local mission trace prompt answer second
	mission="$(cc_uuid)"
	trace="$(cc_uuid)"
	prompt="$(cc_uuid)"
	answer="$(cc_uuid)"
	second="$(cc_uuid)"
	cc_running worker-dev "$mission" QI-4 "$trace"
	"$CONTROL_BIN" raise-question --command-id "$prompt" --worker worker-dev \
		--mission "$mission" --queue-item QI-4 --trace "$(cc_uuid)" \
		--category architecture-decision --body-ref "report:worker-dev/$mission/2" \
		--payload '{"question":"which adapter?"}' >/dev/null
	"$CONTROL_BIN" answer-question --command-id "$answer" --answers "$prompt" \
		--by overseer --trace "$(cc_uuid)" --category architecture-decision \
		--body-ref "human-decision:$answer" --payload '{"answer":"B"}' >/dev/null

	local out="$BATS_TEST_TMPDIR/answers-stdout" err="$BATS_TEST_TMPDIR/answers-stderr"
	local before
	before="$(cc_control_contents "$root")"

	# An already-answered prompt cannot be answered a second time.
	cc_refuse answer-question "$out" "$err" --command-id "$second" --answers "$prompt" \
		--by overseer --trace "$(cc_uuid)" --category architecture-decision \
		--body-ref "human-decision:$second" --payload '{"answer":"C"}'
	grep -Fq "is already answered and cannot be answered again" "$err"

	# A response record is not a prompt, so it can never be answered at all.
	cc_refuse answer-question "$out" "$err" --command-id "$second" --answers "$answer" \
		--by overseer --trace "$(cc_uuid)" --category architecture-decision \
		--body-ref "human-decision:$second" --payload '{"answer":"C"}'
	grep -Fq "record rather than a prompt" "$err"

	# A prompt cannot be raised against a mission this root never materialized,
	# against another worker's mission, or against a wrong queue item.
	cc_refuse raise-question "$out" "$err" --command-id "$(cc_uuid)" --worker worker-dev \
		--mission "$(cc_uuid)" --queue-item QI-4 --trace "$(cc_uuid)" \
		--category architecture-decision --body-ref "note:x" --payload '{"q":1}'
	grep -Fq "which this control root has never materialized" "$err"
	cc_refuse raise-question "$out" "$err" --command-id "$(cc_uuid)" --worker worker-test \
		--mission "$mission" --queue-item QI-4 --trace "$(cc_uuid)" \
		--category architecture-decision --body-ref "note:x" --payload '{"q":1}'
	grep -Fq "which belongs to a different worker or queue item" "$err"
	cc_refuse raise-question "$out" "$err" --command-id "$(cc_uuid)" --worker worker-dev \
		--mission "$mission" --queue-item QI-99 --trace "$(cc_uuid)" \
		--category architecture-decision --body-ref "note:x" --payload '{"q":1}'
	grep -Fq "which belongs to a different worker or queue item" "$err"

	[ "$before" = "$(cc_control_contents "$root")" ]

	# A prompt left pending on a mission that then ends is no longer answerable:
	# an answer to a finished mission would be uncorrelated input.
	local stranded
	stranded="$(cc_uuid)"
	"$CONTROL_BIN" raise-question --command-id "$stranded" --worker worker-dev \
		--mission "$mission" --queue-item QI-4 --trace "$(cc_uuid)" \
		--category filesystem-access --body-ref "note:stranded" --payload '{"q":2}' >/dev/null
	cc_emit --state completed --worker worker-dev --mission "$mission" --queue-item QI-4 \
		--trace "$trace" --sequence 3 --evidence "test:RUN-1"
	cc_refuse answer-question "$out" "$err" --command-id "$(cc_uuid)" --answers "$stranded" \
		--by overseer --trace "$(cc_uuid)" --category filesystem-access \
		--body-ref "human-decision:x" --payload '{"answer":"no"}'
	grep -Fq "is no longer active" "$err"

	# The fold refuses the same things when a committed record reaches it, so a
	# hand-written event cannot resolve a prompt the CLI would have refused.
	run python3 -c '
import sys

sys.path.insert(0, sys.argv[1])
import cockpit_control


class Event:
    def __init__(self, revision):
        self.revision = revision
        self.event_id = "event-%d" % revision
        self.path = type("P", (), {"name": "e%d.json" % revision})()
        self.record = {"timestamp": "2026-01-01T00:00:0%dZ" % revision}


def dialog(**overrides):
    record = {
        "command_id": overrides.pop("command_id"),
        "kind": overrides.pop("kind"),
        "mission_id": "m", "worker_id": "w", "queue_item_id": "q",
        "trace_id": "t", "parent_trace_id": None, "answers_command_id": None,
        "category": "c", "body_refs": ["note:x"], "raised_at": "2026-01-01T00:00:00Z",
        "schema_version": 1, "record_type": "mission-dialog",
    }
    record.update(overrides)
    return record


dialogs = {}
assert cockpit_control.apply_mission_dialog(
    dialogs, dialog(command_id="p", kind="question"), Event(1)
) == (True, cockpit_control.MISSION_FOLD_RAISED)
# An access-prompt response can never resolve an ordinary question.
assert cockpit_control.apply_mission_dialog(
    dialogs,
    dialog(command_id="r1", kind="access-prompt-response", answers_command_id="p"),
    Event(2),
) == (False, cockpit_control.MISSION_FOLD_RETAINED_KIND)
# A reply correlated to another mission resolves nothing.
assert cockpit_control.apply_mission_dialog(
    dialogs,
    dialog(command_id="r2", kind="reply", answers_command_id="p", mission_id="other"),
    Event(3),
) == (False, cockpit_control.MISSION_FOLD_RETAINED_UNMATCHED)
# A reply to a prompt this root never raised resolves nothing.
assert cockpit_control.apply_mission_dialog(
    dialogs, dialog(command_id="r3", kind="reply", answers_command_id="absent"), Event(4)
) == (False, cockpit_control.MISSION_FOLD_RETAINED_UNKNOWN_PROMPT)
assert cockpit_control.apply_mission_dialog(
    dialogs, dialog(command_id="r4", kind="reply", answers_command_id="p"), Event(5)
) == (True, cockpit_control.MISSION_FOLD_ANSWERED)
assert dialogs["p"]["state"] == cockpit_control.MISSION_DIALOG_ANSWERED
# A second reply to the same prompt answers nothing.
assert cockpit_control.apply_mission_dialog(
    dialogs, dialog(command_id="r5", kind="reply", answers_command_id="p"), Event(6)
) == (False, cockpit_control.MISSION_FOLD_RETAINED_ANSWERED)
assert dialogs["p"]["answered_by_command_id"] == "r4"
print("the fold refuses every uncorrelated answer")
' "$MODULE_DIR"
	[ "$status" -eq 0 ]
	echo "$output" | grep -Fq "the fold refuses every uncorrelated answer"
}

@test "an access-prompt response follows the same correlated path as a question reply" {
	local root="$BATS_TEST_TMPDIR/mission-access-prompt"
	cc_mission_store "$root"
	local mission trace prompt answer prompt_trace
	mission="$(cc_uuid)"
	trace="$(cc_uuid)"
	prompt="$(cc_uuid)"
	answer="$(cc_uuid)"
	prompt_trace="$(cc_uuid)"
	cc_running worker-dev "$mission" QI-5 "$trace"

	run "$CONTROL_BIN" raise-question --kind access-prompt --command-id "$prompt" \
		--worker worker-dev --mission "$mission" --queue-item QI-5 --trace "$prompt_trace" \
		--parent-trace "$trace" --category filesystem-access \
		--body-ref "report:worker-dev/$mission/2" \
		--implementation-root "$root/impl" --runtime-boundary deploy:none \
		--payload '{"prompt":"allow write access to /srv/app?"}'
	[ "$status" -eq 0 ]
	echo "$output" | grep -Fq "command $prompt type mission-access-prompt mission $mission"
	echo "$output" | grep -Fq "dialog $prompt kind access-prompt state pending"
	echo "$output" | grep -Fq "boundaries $prompt control-root declared queue-root declared planning-root declared implementation-roots 1 runtime deploy:none"

	# The response kind is derived from the prompt, so an access prompt is always
	# resolved by an access-prompt response and never by an ordinary reply.
	run "$CONTROL_BIN" answer-question --command-id "$answer" --answers "$prompt" \
		--by overseer --trace "$(cc_uuid)" --category filesystem-access \
		--body-ref "human-decision:$answer" --payload '{"response":"granted for /srv/app only"}'
	[ "$status" -eq 0 ]
	echo "$output" | grep -Fq "command $answer type mission-access-prompt-response mission $mission"
	echo "$output" | grep -Fq "dialog $answer kind access-prompt-response state delivered"
	echo "$output" | grep -Fq "dialog $prompt kind access-prompt state answered"

	run grep -rl "allow write access\|granted for /srv/app only" "$root"
	[ "$status" -ne 0 ]

	run "$CONTROL_BIN" validate
	[ "$status" -eq 0 ]
}

@test "a cooperative cancellation is requested acknowledged and durably observable" {
	local root="$BATS_TEST_TMPDIR/mission-cancel"
	cc_mission_store "$root"
	local mission trace cancel cancel_trace
	mission="$(cc_uuid)"
	trace="$(cc_uuid)"
	cancel="$(cc_uuid)"
	cancel_trace="$(cc_uuid)"
	cc_running worker-dev "$mission" QI-6 "$trace"

	run "$CONTROL_BIN" cancel-mission --command-id "$cancel" --worker worker-dev \
		--mission "$mission" --queue-item QI-6 --trace "$cancel_trace" --parent-trace "$trace" \
		--reason "the queue item was superseded" --evidence "queue:QI-6" \
		--requested-at 2026-01-01T00:00:00.000000Z --acknowledge-within 600 \
		--payload '{"cancel":"cooperative"}'
	[ "$status" -eq 0 ]
	echo "$output" | grep -Fq "command $cancel type mission-cancel mission $mission queue-item QI-6 target worker/worker-dev trace $cancel_trace parent-trace $trace"
	echo "$output" | grep -Fq "cancellation-request $cancel mission $mission worker worker-dev queue-item QI-6 trace $cancel_trace parent-trace $trace requested 2026-01-01T00:00:00.000000Z deadline 2026-01-01T00:10:00.000000Z evidence-refs queue:QI-6"

	# Before the declared deadline and with no acknowledgement, the observation
	# is explicitly that the control plane is still waiting.
	run "$CONTROL_BIN" mission-status --command-id "$cancel" --as-of 2026-01-01T00:05:00.000000Z
	[ "$status" -eq 0 ]
	echo "$output" | grep -Fq "observation awaiting-acknowledgement reason - recovery - acknowledgement - by - at - command-status registered mission-state running"
	echo "$output" | grep -Fq "0 timed-out cancellation(s)"

	# The worker acknowledges through the ordinary US2 contract, and the
	# acknowledgement is what the observation reports from then on.
	cc_ack "$cancel" accepted worker-dev
	run "$CONTROL_BIN" mission-status --command-id "$cancel" --as-of 2026-01-01T00:05:00.000000Z
	[ "$status" -eq 0 ]
	echo "$output" | grep -Fq "observation acknowledged"
	echo "$output" | grep -Fq "acknowledgement accepted by worker-dev"
	echo "$output" | grep -Fq "command-status accepted mission-state running"

	# The worker then ends the mission through the ordinary lifecycle path and
	# its single mission slot is released.
	cc_emit --state cancelled --worker worker-dev --mission "$mission" --queue-item QI-6 \
		--trace "$trace" --sequence 3 --reason "acknowledged cooperative cancellation"
	run "$CONTROL_BIN" mission-status --as-of 2026-01-01T00:05:00.000000Z
	[ "$status" -eq 0 ]
	echo "$output" | grep -Fq "mission-state cancelled"
	echo "$output" | grep -Fq "slot worker-dev mission $mission queue-item QI-6 state released"

	# Every one of those answers survives losing all derived state and the
	# process that recorded it.
	rm -f "$root/ledger.json" "$root/events.jsonl"
	run "$CONTROL_BIN" mission-status --command-id "$cancel" --as-of 2026-01-01T00:05:00.000000Z
	[ "$status" -eq 0 ]
	echo "$output" | grep -Fq "observation acknowledged"
	run "$CONTROL_BIN" replay-ledger
	[ "$status" -eq 0 ]
	echo "$output" | grep -Fq "rebuilt ledger.json"
	run "$CONTROL_BIN" validate
	[ "$status" -eq 0 ]
}

@test "an unacknowledged cancellation times out at an explicit moment and never claims a failure" {
	local root="$BATS_TEST_TMPDIR/mission-cancel-timeout"
	cc_mission_store "$root"
	local mission trace cancel cancel_trace late
	mission="$(cc_uuid)"
	trace="$(cc_uuid)"
	cancel="$(cc_uuid)"
	cancel_trace="$(cc_uuid)"
	late="$(cc_uuid)"
	cc_running worker-dev "$mission" QI-7 "$trace"
	"$CONTROL_BIN" cancel-mission --command-id "$cancel" --worker worker-dev \
		--mission "$mission" --queue-item QI-7 --trace "$cancel_trace" \
		--reason "the queue item was superseded" \
		--requested-at 2026-01-01T00:00:00.000000Z \
		--acknowledge-by 2026-01-01T00:10:00.000000Z \
		--payload '{"cancel":"cooperative"}' >/dev/null

	local out="$BATS_TEST_TMPDIR/timeout-stdout" err="$BATS_TEST_TMPDIR/timeout-stderr"
	local code=0
	"$CONTROL_BIN" mission-status --as-of 2026-01-01T01:00:00.000000Z >"$out" 2>"$err" || code=$?
	# A timeout is an observation, never a verdict, so the query still succeeds.
	[ "$code" -eq 0 ]
	grep -Fq "1 timed-out cancellation(s)" "$out"
	grep -Fq "observation timed-out reason acknowledgement-deadline-expired recovery awaiting-bounded-recovery acknowledgement - by - at - command-status registered mission-state running" "$out"
	grep -Fq "is a timed-out observation (acknowledgement-deadline-expired); this is recoverable and is not a mission failure, and it awaits a bounded recovery action" "$err"

	# The timeout is distinct from failure in every direction: it never renames
	# the mission state, and it never uses the failure vocabulary itself.
	cc_absent "observation failed" "$out"
	cc_absent "mission-state failed" "$out"
	cc_absent "mission-state cancelled" "$out"
	run "$CONTROL_BIN" lifecycle-status --as-of 2026-01-01T01:00:00.000000Z
	[ "$status" -eq 0 ]
	echo "$output" | grep -Fq "state running sequence 2"
	cc_absent_output "state failed"

	# The observation is a pure function of committed evidence and the declared
	# moment: the same as-of always answers the same way, an earlier one is not
	# timed out, and nothing expires by itself.
	run "$CONTROL_BIN" mission-status --as-of 2026-01-01T01:00:00.000000Z
	[ "$status" -eq 0 ]
	echo "$output" | grep -Fq "observation timed-out"
	run "$CONTROL_BIN" mission-status --as-of 2026-01-01T00:09:59.999999Z
	[ "$status" -eq 0 ]
	echo "$output" | grep -Fq "observation awaiting-acknowledgement"
	run "$CONTROL_BIN" mission-status --as-of 2026-01-01T00:10:00.000000Z
	[ "$status" -eq 0 ]
	echo "$output" | grep -Fq "observation awaiting-acknowledgement"

	# A redelivery of the identical cancellation auto-commits a `duplicate`
	# acknowledgement.  That acknowledgement answers for the delivery and never
	# for the worker, so it must not be mistaken for cooperation: the overseer
	# would otherwise believe a silent worker acknowledged, which is this
	# story's worst failure mode.  The observation must still be the timeout.
	run "$CONTROL_BIN" cancel-mission --command-id "$cancel" --worker worker-dev \
		--mission "$mission" --queue-item QI-7 --trace "$cancel_trace" \
		--reason "the queue item was superseded" \
		--requested-at 2026-01-01T00:00:00.000000Z \
		--acknowledge-by 2026-01-01T00:10:00.000000Z \
		--payload '{"cancel":"cooperative"}'
	[ "$status" -eq 0 ]
	echo "$output" | grep -Fq "acknowledged duplicate"
	run "$CONTROL_BIN" command-status --command-id "$cancel"
	[ "$status" -eq 0 ]
	echo "$output" | grep -Fq "acknowledgement $cancel outcome duplicate"
	run "$CONTROL_BIN" mission-status --as-of 2026-01-01T01:00:00.000000Z
	[ "$status" -eq 0 ]
	echo "$output" | grep -Fq "observation timed-out reason acknowledgement-deadline-expired recovery awaiting-bounded-recovery acknowledgement - by - at -"
	echo "$output" | grep -Fq "1 timed-out cancellation(s)"
	cc_absent_output "observation acknowledged"

	# Committed evidence outranks the clock: a late acknowledgement is still an
	# acknowledgement, and the observation stops claiming a timeout.
	cc_ack "$cancel" rejected worker-dev --reason "the mission had already finished the step"
	run "$CONTROL_BIN" mission-status --as-of 2026-01-01T01:00:00.000000Z
	[ "$status" -eq 0 ]
	echo "$output" | grep -Fq "observation acknowledged"
	echo "$output" | grep -Fq "0 timed-out cancellation(s)"

	# A second cancellation with an unrepresentable or malformed window is
	# refused with a diagnostic instead of an uncaught traceback.
	local before
	before="$(cc_control_contents "$root")"
	cc_refuse cancel-mission "$out" "$err" --command-id "$late" --worker worker-dev \
		--mission "$mission" --queue-item QI-7 --trace "$(cc_uuid)" --reason "again" \
		--requested-at 9999-12-31T23:59:59.999999Z --acknowledge-within 600 \
		--payload '{"cancel":"late"}'
	grep -Fq -e "--acknowledge-within produces a cancellation acknowledgement deadline outside the representable timestamp range" "$err"
	cc_refuse cancel-mission "$out" "$err" --command-id "$late" --worker worker-dev \
		--mission "$mission" --queue-item QI-7 --trace "$(cc_uuid)" --reason "again" \
		--acknowledge-within 1e400 --payload '{"cancel":"late"}'
	grep -Fq -e "--acknowledge-within must be a positive number of seconds" "$err"
	cc_refuse cancel-mission "$out" "$err" --command-id "$late" --worker worker-dev \
		--mission "$mission" --queue-item QI-7 --trace "$(cc_uuid)" --reason "again" \
		--payload '{"cancel":"late"}'
	grep -Fq "requires an explicit acknowledgement deadline; pass --acknowledge-by or --acknowledge-within" "$err"
	cc_refuse cancel-mission "$out" "$err" --command-id "$late" --worker worker-dev \
		--mission "$mission" --queue-item QI-7 --trace "$(cc_uuid)" --reason "again" \
		--requested-at 2026-01-01T00:00:00.000000Z \
		--acknowledge-by 2025-01-01T00:00:00.000000Z --payload '{"cancel":"late"}'
	grep -Fq "requires acknowledge_deadline_at after requested_at" "$err"
	[ "$before" = "$(cc_control_contents "$root")" ]
}

@test "replacement terminates the prior mission as replaced and leaves exactly one active mission" {
	local root="$BATS_TEST_TMPDIR/mission-replace"
	cc_mission_store "$root"
	local mission trace cancel replacement replacement_mission replacement_trace new_trace
	mission="$(cc_uuid)"
	trace="$(cc_uuid)"
	cancel="$(cc_uuid)"
	replacement="$(cc_uuid)"
	replacement_mission="$(cc_uuid)"
	replacement_trace="$(cc_uuid)"
	new_trace="$(cc_uuid)"
	cc_running worker-dev "$mission" QI-8 "$trace"

	# The obsolete mission is cancelled cooperatively and the worker acknowledges.
	"$CONTROL_BIN" cancel-mission --command-id "$cancel" --worker worker-dev \
		--mission "$mission" --queue-item QI-8 --trace "$(cc_uuid)" \
		--reason "the queue item was superseded" \
		--requested-at 2026-01-01T00:00:00.000000Z --acknowledge-within 600 \
		--payload '{"cancel":"cooperative"}' >/dev/null
	cc_ack "$cancel" accepted worker-dev
	run "$CONTROL_BIN" mission-status --command-id "$cancel" --as-of 2026-01-01T00:01:00.000000Z
	[ "$status" -eq 0 ]
	echo "$output" | grep -Fq "observation acknowledged"

	run "$CONTROL_BIN" replace-mission --command-id "$replacement" --worker worker-dev \
		--mission "$mission" --replacement-mission "$replacement_mission" --queue-item QI-8 \
		--trace "$replacement_trace" --reason "the queue item was superseded" \
		--evidence "queue:QI-8" --payload '{"replace":"now"}'
	[ "$status" -eq 0 ]
	echo "$output" | grep -Fq "command $replacement type mission-replace mission $mission queue-item QI-8 target worker/worker-dev trace $replacement_trace"
	echo "$output" | grep -Fq "mission replacement $replacement replacing mission $mission with $replacement_mission on worker worker-dev is recorded (requested)"
	cc_apply_replacement "$replacement" worker-dev
	run "$CONTROL_BIN" mission-status
	[ "$status" -eq 0 ]
	echo "$output" | grep -Fq "slot worker-dev mission $replacement_mission queue-item QI-8 state reserved command $replacement replaces $mission conflicts 0"

	# The prior mission ends in the existing `replaced` lifecycle state, linked
	# to the mission that superseded it, and cannot be reopened.
	run "$CONTROL_BIN" lifecycle-status --as-of 2026-01-01T00:01:00.000000Z
	[ "$status" -eq 0 ]
	echo "$output" | grep -Fq "$mission worker worker-dev queue-item QI-8 trace $trace state replaced sequence 3 observation terminal"

	# The worker accepts the mission its single slot now holds.
	cc_emit --state accepted --worker worker-dev --mission "$replacement_mission" \
		--queue-item QI-8 --trace "$new_trace" --sequence 1 --fresh-for 3600
	echo "$output" | grep -Fq "advanced the materialized mission state"

	run python3 -c '
import json
import sys
from pathlib import Path

sys.path.insert(0, sys.argv[2])
import cockpit_control

root = Path(sys.argv[1])
mission, replacement_mission, replacement = sys.argv[3], sys.argv[4], sys.argv[5]
ledger = json.loads((root / cockpit_control.LEDGER_NAME).read_text())
missions = ledger[cockpit_control.LEDGER_WORKER_MISSIONS_FIELD]

prior = missions[mission]["lifecycle"]
assert prior["state"] == cockpit_control.LIFECYCLE_REPLACED, prior
assert prior["superseded_by_mission_id"] == replacement_mission, prior
assert prior["worker_id"] == "worker-dev" and prior["queue_item_id"] == "QI-8"
assert prior["sequence"] == 3, prior
assert prior["heartbeat_at"] is None and prior["fresh_until"] is None
assert prior["reason"] == "the queue item was superseded", prior
assert prior["evidence_refs"] == ["queue:QI-8"], prior

active = sorted(
    key for key, slot in missions.items()
    if slot["lifecycle"]["state"] in cockpit_control.WORKER_LIFECYCLE_ACTIVE_STATES
)
assert active == [replacement_mission], active

slots = ledger[cockpit_control.LEDGER_MISSION_SLOTS_FIELD]
assert sorted(slots) == ["worker-dev"], slots
slot = slots["worker-dev"]
assert slot["mission_id"] == replacement_mission and slot["replaces"] == mission, slot
assert slot["state"] == cockpit_control.MISSION_SLOT_ACTIVE, slot
assert slot["command_id"] == replacement and slot["conflicts"] == [], slot
print("exactly one active mission remains on the worker slot")
' "$root" "$MODULE_DIR" "$mission" "$replacement_mission" "$replacement"
	[ "$status" -eq 0 ]
	echo "$output" | grep -Fq "exactly one active mission remains on the worker slot"

	# A replaced mission is terminal, so nothing can reopen it.
	run "$CONTROL_BIN" record-lifecycle --state running --worker worker-dev \
		--mission "$mission" --queue-item QI-8 --trace "$trace" --sequence 9 --fresh-for 60
	[ "$status" -eq 0 ]
	echo "$output" | grep -Fq "retained for audit only (retained-terminal-state"

	run "$CONTROL_BIN" replay-ledger
	[ "$status" -eq 0 ]
	echo "$output" | grep -Fq "projection is current"
	run "$CONTROL_BIN" validate
	[ "$status" -eq 0 ]
	run "$CONTROL_BIN" preflight
	[ "$status" -eq 0 ]
	echo "$output" | grep -Fq "preflight ready"
}

@test "a replacement that would create a second active slot is refused and the conflict is recorded" {
	local root="$BATS_TEST_TMPDIR/mission-second-slot"
	cc_mission_store "$root"
	local held other held_trace other_trace replacement replacement_mission
	held="$(cc_uuid)"
	other="$(cc_uuid)"
	held_trace="$(cc_uuid)"
	other_trace="$(cc_uuid)"
	replacement="$(cc_uuid)"
	replacement_mission="$(cc_uuid)"
	cc_running worker-dev "$held" QI-9 "$held_trace"

	# A second lifecycle claim never takes the worker's single slot; it is
	# retained as a durable conflict instead.
	cc_emit --state accepted --worker worker-dev --mission "$other" --queue-item QI-10 \
		--trace "$other_trace" --sequence 1 --fresh-for 3600
	run "$CONTROL_BIN" mission-status
	[ "$status" -eq 0 ]
	echo "$output" | grep -Fq "slot worker-dev mission $held queue-item QI-9 state active"
	echo "$output" | grep -Fq "mission-conflict worker-dev mission $other reason second-active-slot source lifecycle command -"

	# Replacing that other valid active mission would give the worker two active
	# slots, so the command is refused and the conflict is recorded durably.
	local out="$BATS_TEST_TMPDIR/second-stdout" err="$BATS_TEST_TMPDIR/second-stderr"
	local code=0
	"$CONTROL_BIN" replace-mission --command-id "$replacement" --worker worker-dev \
		--mission "$other" --replacement-mission "$replacement_mission" --queue-item QI-10 \
		--trace "$(cc_uuid)" --reason "obsolete" --payload '{"replace":"second"}' \
		>"$out" 2>"$err" || code=$?
	[ "$code" -ne 0 ]
	grep -Fq "mission replacement $replacement replacing mission $other with $replacement_mission on worker worker-dev is refused; the mission slot conflict is recorded (conflict-recorded)" "$out"
	grep -Fq "mission-conflict worker-dev mission $replacement_mission reason second-active-slot source replacement command $replacement" "$out"
	grep -Fq "would leave worker worker-dev holding more than one active mission slot; the conflict is recorded and no mission state changed" "$err"
	cc_no_traceback "$err"

	# No mission state moved: the held mission still owns the slot, the other
	# mission is untouched, and the refused replacement mission does not exist.
	run python3 -c '
import json
import sys
from pathlib import Path

sys.path.insert(0, sys.argv[2])
import cockpit_control

root = Path(sys.argv[1])
held, other, replacement_mission, replacement = sys.argv[3], sys.argv[4], sys.argv[5], sys.argv[6]
ledger = json.loads((root / cockpit_control.LEDGER_NAME).read_text())
missions = ledger[cockpit_control.LEDGER_WORKER_MISSIONS_FIELD]
assert missions[held]["lifecycle"]["state"] == cockpit_control.LIFECYCLE_RUNNING
assert missions[other]["lifecycle"]["state"] == cockpit_control.LIFECYCLE_ACCEPTED
assert replacement_mission not in missions, missions

slot = ledger[cockpit_control.LEDGER_MISSION_SLOTS_FIELD]["worker-dev"]
assert slot["mission_id"] == held and slot["state"] == cockpit_control.MISSION_SLOT_ACTIVE, slot
reasons = [(c["reason"], c["source"], c["mission_id"], c["command_id"]) for c in slot["conflicts"]]
assert reasons == [
    (cockpit_control.MISSION_SLOT_CONFLICT_SECOND_ACTIVE, "lifecycle", other, None),
    (cockpit_control.MISSION_SLOT_CONFLICT_SECOND_ACTIVE, "replacement", replacement_mission, replacement),
], reasons

# The refused command is still registered evidence of the attempt.
commands = ledger[cockpit_control.LEDGER_COMMANDS_FIELD]
assert commands[replacement]["envelope"]["command_type"] == cockpit_control.COMMAND_TYPE_MISSION_REPLACE
print("the second active slot was refused and recorded")
' "$root" "$MODULE_DIR" "$held" "$other" "$replacement_mission" "$replacement"
	[ "$status" -eq 0 ]
	echo "$output" | grep -Fq "the second active slot was refused and recorded"

	# The conflict survives losing every derived byte.
	rm -f "$root/ledger.json" "$root/events.jsonl"
	run "$CONTROL_BIN" mission-status
	[ "$status" -eq 0 ]
	echo "$output" | grep -Fq "2 recorded conflict(s)"
	echo "$output" | grep -Fq "mission-conflict worker-dev mission $replacement_mission reason second-active-slot source replacement command $replacement"
	run "$CONTROL_BIN" replay-ledger
	[ "$status" -eq 0 ]
	run "$CONTROL_BIN" validate
	[ "$status" -eq 0 ]
}

@test "a replacement that reuses an existing mission ID is refused and the conflict is recorded" {
	local root="$BATS_TEST_TMPDIR/mission-reused-id"
	cc_mission_store "$root"
	local prior current other reserved_prior reserved held_replacement other_replacement
	prior="$(cc_uuid)"
	current="$(cc_uuid)"
	other="$(cc_uuid)"
	reserved_prior="$(cc_uuid)"
	reserved="$(cc_uuid)"
	held_replacement="$(cc_uuid)"
	other_replacement="$(cc_uuid)"
	cc_running worker-dev "$prior" QI-20 "$(cc_uuid)"
	cc_running worker-qa "$other" QI-21 "$(cc_uuid)"
	cc_running worker-ops "$reserved_prior" QI-22 "$(cc_uuid)"

	# worker-dev already replaced one mission, so `$prior` is terminal on its own
	# worker and `$current` is the mission its single slot now holds.
	"$CONTROL_BIN" replace-mission --command-id "$held_replacement" --worker worker-dev \
		--mission "$prior" --replacement-mission "$current" --queue-item QI-20 \
		--trace "$(cc_uuid)" --reason "the queue item was superseded" \
		--payload '{"replace":"first"}' >/dev/null
	cc_apply_replacement "$held_replacement" worker-dev
	cc_emit --state accepted --worker worker-dev --mission "$current" --queue-item QI-20 \
		--trace "$(cc_uuid)" --sequence 1 --fresh-for 3600

	# worker-ops holds a reserved slot for a mission that has not emitted any
	# lifecycle event yet, so its ID exists only in the slot projection.
	"$CONTROL_BIN" replace-mission --command-id "$other_replacement" --worker worker-ops \
		--mission "$reserved_prior" --replacement-mission "$reserved" --queue-item QI-22 \
		--trace "$(cc_uuid)" --reason "the queue item was superseded" \
		--payload '{"replace":"reserved"}' >/dev/null
	cc_apply_replacement "$other_replacement" worker-ops
	run "$CONTROL_BIN" mission-status
	[ "$status" -eq 0 ]
	echo "$output" | grep -Fq "slot worker-dev mission $current queue-item QI-20 state active"
	echo "$output" | grep -Fq "slot worker-qa mission $other queue-item QI-21 state active"
	echo "$output" | grep -Fq "slot worker-ops mission $reserved queue-item QI-22 state reserved"

	# A replacement must create one new mission.  Every way of naming a mission
	# ID that already exists is refused and durably recorded, because reusing one
	# would leave two slots holding the same mission: the prior mission's own
	# terminal ID, another worker's claimed active mission, and another worker's
	# reserved-but-not-yet-materialized mission.
	local out="$BATS_TEST_TMPDIR/reused-stdout" err="$BATS_TEST_TMPDIR/reused-stderr"
	local attempt code reuse_terminal reuse_active reuse_reserved
	reuse_terminal="$(cc_uuid)"
	reuse_active="$(cc_uuid)"
	reuse_reserved="$(cc_uuid)"
	set -- "$reuse_terminal:$prior" "$reuse_active:$other" "$reuse_reserved:$reserved"
	for attempt in "$@"; do
		local command_id="${attempt%%:*}" reused="${attempt##*:}"
		code=0
		"$CONTROL_BIN" replace-mission --command-id "$command_id" --worker worker-dev \
			--mission "$current" --replacement-mission "$reused" --queue-item QI-20 \
			--trace "$(cc_uuid)" --reason "obsolete" --payload "{\"replace\":\"$command_id\"}" \
			>"$out" 2>"$err" || code=$?
		[ "$code" -ne 0 ]
		grep -Fq "mission replacement $command_id replacing mission $current with $reused on worker worker-dev is refused; the mission slot conflict is recorded (conflict-recorded)" "$out"
		grep -Fq "mission-conflict worker-dev mission $reused reason reused-mission-id source replacement command $command_id" "$out"
		# The refusal names the reason it was actually recorded for.
		grep -Fq "is refused because mission ID $reused is already in use and a replacement must create one new mission; the conflict is recorded and no mission state changed" "$err"
		cc_absent "holding more than one active mission slot" "$err"
		cc_no_traceback "$err"
		# The worker's one slot still holds the mission it held before.
		grep -Fq "slot worker-dev mission $current queue-item QI-20 state active" "$out"
		cc_absent "slot worker-dev mission $reused" "$out"
	done

	# No mission bled between slots: every claimed slot still holds a distinct
	# mission, `$current` never moved, and not one reused ID was materialized.
	run python3 -c '
import json
import sys
from pathlib import Path

sys.path.insert(0, sys.argv[2])
import cockpit_control

root = Path(sys.argv[1])
prior, current, other, reserved = sys.argv[3:7]
held_replacement = sys.argv[7]
reuse_terminal, reuse_active, reuse_reserved = sys.argv[8:11]
ledger = json.loads((root / cockpit_control.LEDGER_NAME).read_text())
missions = ledger[cockpit_control.LEDGER_WORKER_MISSIONS_FIELD]
slots = ledger[cockpit_control.LEDGER_MISSION_SLOTS_FIELD]

# One mission ID is held by at most one slot; a reused ID would break this.
claimed = [
    (worker, slot["mission_id"]) for worker, slot in sorted(slots.items())
    if slot["state"] in cockpit_control.MISSION_SLOT_CLAIMED_STATES
]
held = [mission_id for _worker, mission_id in claimed]
assert sorted(held) == sorted(set(held)), claimed
assert claimed == [
    ("worker-dev", current), ("worker-ops", reserved), ("worker-qa", other)
], claimed

dev = slots["worker-dev"]
assert dev["state"] == cockpit_control.MISSION_SLOT_ACTIVE, dev
assert dev["replaces"] == prior and dev["command_id"] == held_replacement, dev
recorded = [
    (conflict["mission_id"], conflict["reason"], conflict["source"], conflict["command_id"])
    for conflict in dev["conflicts"]
]
assert recorded == [
    (prior, cockpit_control.MISSION_SLOT_CONFLICT_REUSED, "replacement", reuse_terminal),
    (other, cockpit_control.MISSION_SLOT_CONFLICT_REUSED, "replacement", reuse_active),
    (reserved, cockpit_control.MISSION_SLOT_CONFLICT_REUSED, "replacement", reuse_reserved),
], recorded

# Nothing the refused replacements named moved one step of mission state.
assert missions[prior]["lifecycle"]["state"] == cockpit_control.LIFECYCLE_REPLACED
assert missions[prior]["lifecycle"]["superseded_by_mission_id"] == current
assert missions[current]["lifecycle"]["state"] == cockpit_control.LIFECYCLE_ACCEPTED
assert missions[current]["lifecycle"]["superseded_by_mission_id"] is None
assert missions[other]["lifecycle"]["state"] == cockpit_control.LIFECYCLE_RUNNING
assert missions[other]["lifecycle"]["worker_id"] == "worker-qa"
assert reserved not in missions, missions

# Each refused attempt is still registered evidence that it was attempted.
commands = ledger[cockpit_control.LEDGER_COMMANDS_FIELD]
for command_id in (reuse_terminal, reuse_active, reuse_reserved):
    envelope = commands[command_id]["envelope"]
    assert envelope["command_type"] == cockpit_control.COMMAND_TYPE_MISSION_REPLACE, envelope
print("every reused mission ID was refused and no mission bled between slots")
' "$root" "$MODULE_DIR" "$prior" "$current" "$other" "$reserved" "$held_replacement" \
		"$reuse_terminal" "$reuse_active" "$reuse_reserved"
	[ "$status" -eq 0 ]
	echo "$output" | grep -Fq "every reused mission ID was refused and no mission bled between slots"

	# The refusals survive losing every derived byte.
	rm -f "$root/ledger.json" "$root/events.jsonl"
	run "$CONTROL_BIN" mission-status
	[ "$status" -eq 0 ]
	echo "$output" | grep -Fq "3 recorded conflict(s)"
	echo "$output" | grep -Fq "mission-conflict worker-dev mission $other reason reused-mission-id source replacement command $reuse_active"
	echo "$output" | grep -Fq "slot worker-dev mission $current queue-item QI-20 state active"
	run "$CONTROL_BIN" replay-ledger
	[ "$status" -eq 0 ]
	run "$CONTROL_BIN" validate
	[ "$status" -eq 0 ]
}

@test "concurrent replacement attempts resolve deterministically to exactly one active slot" {
	local root="$BATS_TEST_TMPDIR/mission-replace-race"
	cc_mission_store "$root"
	local mission trace
	mission="$(cc_uuid)"
	trace="$(cc_uuid)"
	cc_running worker-dev "$mission" QI-11 "$trace"

	run python3 -c '
import json
import subprocess
import sys
from pathlib import Path
from uuid import uuid4

sys.path.insert(0, sys.argv[2])
import cockpit_control

root = Path(sys.argv[1])
control_bin = sys.argv[3]
mission = sys.argv[4]

environment = dict(__import__("os").environ)
environment["COCKPIT_CONTROL_ROOT"] = str(root)
# Contention is resolved by the control lock and the deterministic fold, so the
# only timing this test needs is a bound generous enough not to expire.
environment["COCKPIT_CONTROL_LOCK_TIMEOUT_SECONDS"] = "60"

attempts = []
for index in range(4):
    command_id, replacement_mission = str(uuid4()), str(uuid4())
    attempts.append((command_id, replacement_mission, [
        control_bin, "replace-mission", "--command-id", command_id, "--worker", "worker-dev",
        "--mission", mission, "--replacement-mission", replacement_mission,
        "--queue-item", "QI-11", "--trace", str(uuid4()), "--reason", "obsolete",
        "--payload", json.dumps({"replace": index}),
    ]))

processes = [
    subprocess.Popen(
        argv, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=environment
    )
    for _command_id, _replacement, argv in attempts
]
results = []
for process in processes:
    stdout, stderr = process.communicate(timeout=180)
    results.append((process.returncode, stdout, stderr))
for code, stdout, stderr in results:
    assert "Traceback" not in stderr, stderr

assert all(code == 0 for code, _, _ in results), results
assert cockpit_control.read_mission_state(root).worker_slots["worker-dev"]["mission_id"] == mission
# Requests can race, but application receipts serialize on the control lock.
# Only the first cooperative application can reserve a replacement slot.
winner = attempts[0]
for command_id, _, _ in attempts:
    digest = cockpit_control.read_command_slots(root)[command_id]["envelope"]["payload_digest"]
    for outcome in ("accepted", "applied"):
        cockpit_control.acknowledge_command(root, cockpit_control.build_command_acknowledgement(
            command_id, digest, outcome, "worker-dev", result_refs=["test:cooperative-replacement"],
        ))

control_id = json.loads((root / cockpit_control.CONTROL_METADATA_NAME).read_text())["control_id"]
state = cockpit_control.fold_mission_state(
    cockpit_control.read_committed_events(root, control_id).events
)
slot = state.worker_slots["worker-dev"]
assert slot["mission_id"] == winner[1], (slot, winner)
assert slot["state"] == cockpit_control.MISSION_SLOT_RESERVED, slot
assert slot["replaces"] == mission and slot["command_id"] == winner[0], slot
assert len(slot["conflicts"]) == 3, slot
assert all(
    conflict["reason"] == cockpit_control.MISSION_SLOT_CONFLICT_UNMATCHED
    for conflict in slot["conflicts"]
), slot

assert state.missions[mission]["lifecycle"]["state"] == cockpit_control.LIFECYCLE_REPLACED
assert state.missions[mission]["lifecycle"]["superseded_by_mission_id"] == winner[1]
for _command_id, replacement_mission, _argv in attempts:
    if replacement_mission != winner[1]:
        assert replacement_mission not in state.missions, replacement_mission
active = [
    key for key, entry in state.missions.items()
    if entry["lifecycle"]["state"] in cockpit_control.WORKER_LIFECYCLE_ACTIVE_STATES
]
assert active == [], active
print("exactly one concurrent replacement claimed the single mission slot")
' "$root" "$MODULE_DIR" "$CONTROL_BIN" "$mission"
	[ "$status" -eq 0 ]
	echo "$output" | grep -Fq "exactly one concurrent replacement claimed the single mission slot"

	run "$CONTROL_BIN" replay-ledger
	[ "$status" -eq 0 ]
	run "$CONTROL_BIN" validate
	[ "$status" -eq 0 ]
	run "$CONTROL_BIN" preflight
	[ "$status" -eq 0 ]
	echo "$output" | grep -Fq "preflight ready"
}

@test "every managed mission command redelivers idempotently" {
	local root="$BATS_TEST_TMPDIR/mission-redelivery"
	cc_mission_store "$root"
	local mission trace prompt answer access response cancel replacement replacement_mission
	mission="$(cc_uuid)"
	trace="$(cc_uuid)"
	prompt="$(cc_uuid)"
	answer="$(cc_uuid)"
	access="$(cc_uuid)"
	response="$(cc_uuid)"
	cancel="$(cc_uuid)"
	replacement="$(cc_uuid)"
	replacement_mission="$(cc_uuid)"
	cc_running worker-dev "$mission" QI-12 "$trace"

	local prompt_trace answer_trace access_trace response_trace cancel_trace replace_trace
	prompt_trace="$(cc_uuid)"
	answer_trace="$(cc_uuid)"
	access_trace="$(cc_uuid)"
	response_trace="$(cc_uuid)"
	cancel_trace="$(cc_uuid)"
	replace_trace="$(cc_uuid)"

	# Every managed delivery is issued twice with the same command ID and the
	# same payload; the second must return the stored result and apply nothing.
	local first second
	for first in 1 2; do
		run "$CONTROL_BIN" raise-question --command-id "$prompt" --worker worker-dev \
			--mission "$mission" --queue-item QI-12 --trace "$prompt_trace" \
			--category architecture-decision --body-ref "note:prompt" --payload '{"q":1}'
		[ "$status" -eq 0 ]
		run "$CONTROL_BIN" answer-question --command-id "$answer" --answers "$prompt" \
			--by overseer --trace "$answer_trace" --category architecture-decision \
			--body-ref "note:answer" --payload '{"a":1}'
		[ "$status" -eq 0 ]
		run "$CONTROL_BIN" raise-question --kind access-prompt --command-id "$access" \
			--worker worker-dev --mission "$mission" --queue-item QI-12 --trace "$access_trace" \
			--category filesystem-access --body-ref "note:access" --payload '{"q":2}'
		[ "$status" -eq 0 ]
		run "$CONTROL_BIN" answer-question --command-id "$response" --answers "$access" \
			--by overseer --trace "$response_trace" --category filesystem-access \
			--body-ref "note:response" --payload '{"a":2}'
		[ "$status" -eq 0 ]
		run "$CONTROL_BIN" cancel-mission --command-id "$cancel" --worker worker-dev \
			--mission "$mission" --queue-item QI-12 --trace "$cancel_trace" --reason "obsolete" \
			--requested-at 2026-01-01T00:00:00.000000Z --acknowledge-within 600 \
			--payload '{"cancel":1}'
		[ "$status" -eq 0 ]
		run "$CONTROL_BIN" replace-mission --command-id "$replacement" --worker worker-dev \
			--mission "$mission" --replacement-mission "$replacement_mission" \
			--queue-item QI-12 --trace "$replace_trace" --reason "obsolete" \
			--payload '{"replace":1}'
		[ "$status" -eq 0 ]
		if [ "$first" = "2" ]; then
			echo "$output" | grep -Fq "acknowledged duplicate"
			echo "$output" | grep -Fq "is a redelivery; the stored mission record is returned without applying it again"
		fi
	done

	run python3 -c '
import json
import sys
from pathlib import Path

sys.path.insert(0, sys.argv[2])
import cockpit_control

root = Path(sys.argv[1])
prompt, answer, access, response, cancel, replacement = sys.argv[3:9]
replacement_mission, mission = sys.argv[9], sys.argv[10]
ledger = json.loads((root / cockpit_control.LEDGER_NAME).read_text())

dialogs = ledger[cockpit_control.LEDGER_MISSION_DIALOGS_FIELD]
assert sorted(dialogs) == sorted([prompt, answer, access, response]), sorted(dialogs)
assert dialogs[prompt]["state"] == cockpit_control.MISSION_DIALOG_ANSWERED
assert dialogs[prompt]["answered_by_command_id"] == answer
assert dialogs[access]["answered_by_command_id"] == response
cancellations = ledger[cockpit_control.LEDGER_MISSION_CANCELLATIONS_FIELD]
assert sorted(cancellations) == [cancel], sorted(cancellations)

slot = ledger[cockpit_control.LEDGER_MISSION_SLOTS_FIELD]["worker-dev"]
assert slot["mission_id"] == mission, slot
assert slot["state"] == cockpit_control.MISSION_SLOT_ACTIVE and slot["conflicts"] == [], slot

# Each managed command was registered exactly once and each redelivery is a
# duplicate acknowledgement rather than a second application.
commands = ledger[cockpit_control.LEDGER_COMMANDS_FIELD]
for command_id in (prompt, answer, access, response, cancel, replacement):
    entry = commands[command_id]
    assert len(entry["deliveries"]) == 1, entry
    assert entry["conflicts"] == [], entry
    outcomes = [item["acknowledgement"]["outcome"] for item in entry["acknowledgements"]]
    assert outcomes == [cockpit_control.COMMAND_DUPLICATE], outcomes
print("every managed command redelivered exactly once")
' "$root" "$MODULE_DIR" "$prompt" "$answer" "$access" "$response" "$cancel" "$replacement" \
		"$replacement_mission" "$mission"
	[ "$status" -eq 0 ]
	echo "$output" | grep -Fq "every managed command redelivered exactly once"

	# A third delivery reusing the command ID with a different envelope is a
	# durable command conflict, not a redelivery: nothing was stored, so the
	# managed half must not claim a stored mission record was returned again.
	local conflict_out="$BATS_TEST_TMPDIR/redelivery-conflict-stdout"
	local conflict_err="$BATS_TEST_TMPDIR/redelivery-conflict-stderr"
	local code=0
	"$CONTROL_BIN" cancel-mission --command-id "$cancel" --worker worker-dev \
		--mission "$mission" --queue-item QI-12 --trace "$(cc_uuid)" --reason "obsolete" \
		--requested-at 2026-01-01T00:00:00.000000Z --acknowledge-within 600 \
		--payload '{"cancel":1}' >"$conflict_out" 2>"$conflict_err" || code=$?
	[ "$code" -ne 0 ]
	grep -Fq "command $cancel is refused as a envelope-conflict" "$conflict_out"
	grep -Fq "is not applied because its command envelope was refused as a durable conflict; no mission record was stored" "$conflict_out"
	cc_absent "is a redelivery; the stored mission record is returned without applying it again" "$conflict_out"
	cc_no_traceback "$conflict_err"

	run "$CONTROL_BIN" validate
	[ "$status" -eq 0 ]
}

@test "malformed unversioned and future-versioned mission records fail closed when read back" {
	local root="$BATS_TEST_TMPDIR/mission-schema"
	cc_mission_store "$root"

	run python3 -c '
import sys
from uuid import uuid4

sys.path.insert(0, sys.argv[1])
import cockpit_control
from cockpit_control import (
    ControlStoreError,
    validate_mission_cancellation,
    validate_mission_dialog,
    validate_mission_replacement,
)

command_id, mission, trace = str(uuid4()), str(uuid4()), str(uuid4())
timestamp = "2026-09-04T00:00:00Z"
dialog = {
    "schema_version": 1, "record_type": "mission-dialog", "command_id": command_id,
    "kind": "question", "mission_id": mission, "worker_id": "worker-dev",
    "queue_item_id": "QI-1", "trace_id": trace, "parent_trace_id": None,
    "answers_command_id": None, "category": "architecture-decision",
    "body_refs": ["report:worker-dev/mission/1"], "raised_at": timestamp,
}
cancellation = {
    "schema_version": 1, "record_type": "mission-cancellation", "command_id": command_id,
    "mission_id": mission, "worker_id": "worker-dev", "queue_item_id": "QI-1",
    "trace_id": trace, "parent_trace_id": None, "reason": "superseded",
    "evidence_refs": [], "requested_at": timestamp,
    "acknowledge_deadline_at": "2026-09-04T00:10:00Z",
}
replacement = {
    "schema_version": 1, "record_type": "mission-replacement", "command_id": command_id,
    "worker_id": "worker-dev", "queue_item_id": "QI-1", "replaced_mission_id": mission,
    "replacement_mission_id": str(uuid4()), "trace_id": trace, "parent_trace_id": None,
    "reason": "superseded", "evidence_refs": [], "requested_at": timestamp,
}
records = (
    (dialog, validate_mission_dialog),
    (cancellation, validate_mission_cancellation),
    (replacement, validate_mission_replacement),
)
for record, validator in records:
    validator(dict(record))


def refused(record, validator, fragment):
    try:
        validator(record)
    except ControlStoreError as error:
        assert fragment in str(error), (fragment, str(error))
        return
    raise AssertionError("accepted %s" % (fragment,))


for version, fragment in (
    (2, "unsupported future schema_version"),
    (0, "unsupported schema_version"),
):
    for record, validator in records:
        refused(dict(record, schema_version=version), validator, fragment)
for record, validator in records:
    broken = dict(record)
    del broken["schema_version"]
    refused(broken, validator, "requires integer schema_version")
    refused(dict(record, invented="x"), validator, "declares unknown field(s) invented")
    refused(dict(record, record_type="other"), validator, "record_type")
    for field in record:
        if field == "schema_version":
            continue
        broken = dict(record)
        del broken[field]
        refused(broken, validator, "requires %s" % field)
    refused(dict(record, worker_id="worker dev"), validator, "printable non-whitespace ASCII")
    refused(dict(record, queue_item_id="QI 1"), validator, "printable non-whitespace ASCII")
    refused(dict(record, trace_id="not-a-uuid"), validator, "requires UUID trace_id")
    refused(
        dict(record, parent_trace_id=record["trace_id"]),
        validator,
        "requires parent_trace_id to name a different trace",
    )

refused(dict(dialog, kind="shout"), validate_mission_dialog, "declares unknown kind")
refused(dict(dialog, category="a b"), validate_mission_dialog, "printable non-whitespace ASCII")
refused(dict(dialog, body_refs=[]), validate_mission_dialog, "at least one dialog body reference")
refused(dict(dialog, body_refs=["untyped"]), validate_mission_dialog, "typed")
refused(
    dict(dialog, answers_command_id=str(uuid4())),
    validate_mission_dialog,
    "must not declare answers_command_id for prompt kind",
)
refused(
    dict(dialog, kind="reply", answers_command_id=None),
    validate_mission_dialog,
    "requires non-empty answers_command_id",
)
refused(
    dict(dialog, kind="reply", answers_command_id=command_id),
    validate_mission_dialog,
    "requires answers_command_id to name a different command",
)
refused(
    dict(cancellation, acknowledge_deadline_at=timestamp),
    validate_mission_cancellation,
    "requires acknowledge_deadline_at after requested_at",
)
refused(
    dict(cancellation, reason="   "),
    validate_mission_cancellation,
    "requires non-empty reason",
)
refused(
    dict(cancellation, evidence_refs=["queue:A", "queue:A"]),
    validate_mission_cancellation,
    "duplicates evidence reference",
)
refused(
    dict(replacement, replacement_mission_id=mission),
    validate_mission_replacement,
    "requires replacement_mission_id to name a different mission",
)
refused(
    dict(replacement, reason=""),
    validate_mission_replacement,
    "requires non-empty reason",
)
print("every malformed mission record was refused")
' "$MODULE_DIR"
	[ "$status" -eq 0 ]
	echo "$output" | grep -Fq "every malformed mission record was refused"

	# The same refusal happens when a hand-written event is read back: a
	# committed file can never be understood as half a mission contract.
	local mission trace prompt
	mission="$(cc_uuid)"
	trace="$(cc_uuid)"
	prompt="$(cc_uuid)"
	cc_running worker-dev "$mission" QI-13 "$trace"
	"$CONTROL_BIN" raise-question --command-id "$prompt" --worker worker-dev \
		--mission "$mission" --queue-item QI-13 --trace "$(cc_uuid)" \
		--category architecture-decision --body-ref "note:x" --payload '{"q":1}' >/dev/null

	local out="$BATS_TEST_TMPDIR/mission-schema-stdout"
	local err="$BATS_TEST_TMPDIR/mission-schema-stderr"
	local subcommand mutation saved="$BATS_TEST_TMPDIR/saved-event.json"
	for mutation in future-version foreign-command foreign-type two-records; do
		run python3 -c '
import json
import sys
from pathlib import Path

sys.path.insert(0, sys.argv[2])
import cockpit_control

root = Path(sys.argv[1])
mutation = sys.argv[3]
events = sorted((root / cockpit_control.EVENTS_DIR_NAME).iterdir())
path = events[-1]
record = json.loads(path.read_text())
Path(sys.argv[4]).write_text(path.read_text())
dialog = record["payload"][cockpit_control.MISSION_DIALOG_PAYLOAD_FIELD]
if mutation == "future-version":
    dialog["schema_version"] = 2
elif mutation == "foreign-command":
    dialog["command_id"] = "11111111-2222-3333-4444-555555555555"
elif mutation == "foreign-type":
    record["payload"][cockpit_control.COMMAND_ENVELOPE_PAYLOAD_FIELD]["command_type"] = "dispatch"
else:
    record["payload"][cockpit_control.MISSION_CANCELLATION_PAYLOAD_FIELD] = dialog
path.write_text(json.dumps(record, indent=2, sort_keys=True) + "\n")
' "$root" "$MODULE_DIR" "$mutation" "$saved"
		[ "$status" -eq 0 ]

		local before
		before="$(cc_control_contents "$root")"
		for subcommand in mission-status command-status list-events validate replay-ledger; do
			local code=0
			"$CONTROL_BIN" "$subcommand" >"$out" 2>"$err" || code=$?
			[ "$code" -ne 0 ]
			head -n 1 "$err" | grep -q "^cockpit-control: "
			cc_no_traceback "$err"
		done
		[ "$before" = "$(cc_control_contents "$root")" ]

		# Restore the committed event before the next mutation.
		run python3 -c '
import sys
from pathlib import Path

sys.path.insert(0, sys.argv[2])
import cockpit_control

root = Path(sys.argv[1])
events = sorted((root / cockpit_control.EVENTS_DIR_NAME).iterdir())
events[-1].write_text(Path(sys.argv[3]).read_text())
' "$root" "$MODULE_DIR" "$saved"
		[ "$status" -eq 0 ]
	done

	run "$CONTROL_BIN" validate
	[ "$status" -eq 0 ]
}

@test "worker and operator controlled strings cannot forge a mission record boundary" {
	local root="$BATS_TEST_TMPDIR/mission-forgery"
	cc_mission_store "$root"
	local mission trace prompt cancel
	mission="$(cc_uuid)"
	trace="$(cc_uuid)"
	prompt="$(cc_uuid)"
	cancel="$(cc_uuid)"
	cc_running worker-dev "$mission" QI-14 "$trace"

	local out="$BATS_TEST_TMPDIR/forgery-stdout" err="$BATS_TEST_TMPDIR/forgery-stderr"
	local before forged
	before="$(cc_control_contents "$root")"
	forged="$(printf 'worker-dev\ndialog %s kind question state pending' "$(cc_uuid)")"

	# Every identifier-like field rendered into a parseable position refuses a
	# value that could forge a whole additional mission record.
	cc_refuse raise-question "$out" "$err" --command-id "$(cc_uuid)" --worker "$forged" \
		--mission "$mission" --queue-item QI-14 --trace "$(cc_uuid)" \
		--category architecture-decision --body-ref "note:x" --payload '{"q":1}'
	grep -Fq "could forge a record boundary" "$err"
	cc_refuse raise-question "$out" "$err" --command-id "$(cc_uuid)" --worker worker-dev \
		--mission "$mission" --queue-item "$forged" --trace "$(cc_uuid)" \
		--category architecture-decision --body-ref "note:x" --payload '{"q":1}'
	grep -Fq "could forge a record boundary" "$err"
	cc_refuse raise-question "$out" "$err" --command-id "$(cc_uuid)" --worker worker-dev \
		--mission "$mission" --queue-item QI-14 --trace "$(cc_uuid)" \
		--category "$forged" --body-ref "note:x" --payload '{"q":1}'
	grep -Fq "could forge a record boundary" "$err"
	cc_refuse raise-question "$out" "$err" --command-id "$(cc_uuid)" --worker worker-dev \
		--mission "$mission" --queue-item QI-14 --trace "$(cc_uuid)" \
		--category architecture-decision --body-ref "note: x" --payload '{"q":1}'
	grep -Fq "must not contain whitespace" "$err"
	cc_refuse replace-mission "$out" "$err" --command-id "$(cc_uuid)" --worker "$forged" \
		--mission "$mission" --replacement-mission "$(cc_uuid)" --queue-item QI-14 \
		--trace "$(cc_uuid)" --reason "x" --payload '{"r":1}'
	grep -Fq "could forge a record boundary" "$err"
	[ "$before" = "$(cc_control_contents "$root")" ]

	# Free text is accepted where it is genuinely prose, and is then never
	# rendered into any parseable stdout position.
	"$CONTROL_BIN" raise-question --command-id "$prompt" --worker worker-dev \
		--mission "$mission" --queue-item QI-14 --trace "$(cc_uuid)" \
		--category architecture-decision --body-ref "note:x" \
		--payload '{"question":"the token is s3cr3t-please-do-not-store"}' >/dev/null
	"$CONTROL_BIN" cancel-mission --command-id "$cancel" --worker worker-dev \
		--mission "$mission" --queue-item QI-14 --trace "$(cc_uuid)" \
		--reason "$(printf 'obsolete\ncancellation %s mission forged observation acknowledged' "$(cc_uuid)")" \
		--requested-at 2026-01-01T00:00:00.000000Z --acknowledge-within 600 \
		--payload '{"cancel":1}' >/dev/null
	# A replacement carries the same kind of free text, and it lands in the
	# terminating `replaced` lifecycle record rather than in any rendered line.
	"$CONTROL_BIN" replace-mission --command-id "$(cc_uuid)" --worker worker-dev \
		--mission "$mission" --replacement-mission "$(cc_uuid)" --queue-item QI-14 \
		--trace "$(cc_uuid)" \
		--reason "$(printf 'obsolete\nslot worker-dev mission forged observation acknowledged')" \
		--payload '{"replace":1}' >/dev/null

	local subcommand
	for subcommand in mission-status command-status lifecycle-status list-events; do
		run "$CONTROL_BIN" "$subcommand"
		[ "$status" -eq 0 ]
		cc_absent_output "mission forged observation acknowledged"
		cc_absent_output "s3cr3t-please-do-not-store"
	done

	# The prompt body is not persisted anywhere at all, so the secret it quoted
	# cannot leak from a committed record either.
	run grep -rl "s3cr3t-please-do-not-store" "$root"
	[ "$status" -ne 0 ]

	# Every emitted mission line is exactly one record: nothing a caller supplied
	# added a line to the payload.
	run "$CONTROL_BIN" mission-status
	[ "$status" -eq 0 ]
	run python3 -c '
import sys

prefixes = ("cockpit-control:", "dialog ", "cancellation ", "replacement ", "slot ", "mission-conflict ")
lines = [line for line in sys.stdin.read().splitlines() if line]
assert lines, lines
for line in lines:
    assert line.startswith(prefixes), line
print("%d mission record line(s) are all well formed" % len(lines))
' <<<"$output"
	[ "$status" -eq 0 ]
	echo "$output" | grep -Fq "mission record line(s) are all well formed"
}

@test "the managed mission dry runs report their decisions and change no state" {
	local root="$BATS_TEST_TMPDIR/mission-dry-run"
	cc_mission_store "$root"
	local mission trace prompt
	mission="$(cc_uuid)"
	trace="$(cc_uuid)"
	prompt="$(cc_uuid)"
	cc_running worker-dev "$mission" QI-15 "$trace"
	"$CONTROL_BIN" raise-question --command-id "$prompt" --worker worker-dev \
		--mission "$mission" --queue-item QI-15 --trace "$(cc_uuid)" \
		--category architecture-decision --body-ref "note:x" --payload '{"q":1}' >/dev/null

	local before
	before="$(cc_control_contents "$root")"

	run "$CONTROL_BIN" raise-question --dry-run --command-id "$(cc_uuid)" --worker worker-dev \
		--mission "$mission" --queue-item QI-15 --trace "$(cc_uuid)" \
		--category filesystem-access --body-ref "note:y" --payload '{"q":2}'
	[ "$status" -eq 0 ]
	echo "$output" | grep -Fq "no state changed"
	echo "$output" | grep -Fq "would be recorded (raised)"

	run "$CONTROL_BIN" answer-question --dry-run --command-id "$(cc_uuid)" --answers "$prompt" \
		--by overseer --trace "$(cc_uuid)" --category architecture-decision \
		--body-ref "note:answer" --payload '{"a":1}'
	[ "$status" -eq 0 ]
	echo "$output" | grep -Fq "would be recorded (answered)"

	run "$CONTROL_BIN" cancel-mission --dry-run --command-id "$(cc_uuid)" --worker worker-dev \
		--mission "$mission" --queue-item QI-15 --trace "$(cc_uuid)" --reason "obsolete" \
		--requested-at 2026-01-01T00:00:00.000000Z --acknowledge-within 600 \
		--payload '{"cancel":1}'
	[ "$status" -eq 0 ]
	echo "$output" | grep -Fq "would be recorded (requested)"

	run "$CONTROL_BIN" replace-mission --dry-run --command-id "$(cc_uuid)" --worker worker-dev \
		--mission "$mission" --replacement-mission "$(cc_uuid)" --queue-item QI-15 \
		--trace "$(cc_uuid)" --reason "obsolete" --payload '{"r":1}'
	[ "$status" -eq 0 ]
	echo "$output" | grep -Fq "would be recorded (requested)"

	run "$CONTROL_BIN" mission-status --as-of 2026-01-01T00:00:00.000000Z
	[ "$status" -eq 0 ]

	# Not one dry run, and not the read-only query, changed a single byte.
	[ "$before" = "$(cc_control_contents "$root")" ]
	run "$CONTROL_BIN" list-events
	[ "$status" -eq 0 ]
	echo "$output" | grep -Fq "3 committed events in $root (latest revision 3)"
	run "$CONTROL_BIN" validate
	[ "$status" -eq 0 ]
}

@test "every mission answer cancellation and replacement outcome survives a cold restart" {
	local root="$BATS_TEST_TMPDIR/mission-cold-restart"
	cc_mission_store "$root"
	local mission trace prompt answer cancel replacement replacement_mission
	mission="$(cc_uuid)"
	trace="$(cc_uuid)"
	prompt="$(cc_uuid)"
	answer="$(cc_uuid)"
	cancel="$(cc_uuid)"
	replacement="$(cc_uuid)"
	replacement_mission="$(cc_uuid)"
	cc_running worker-dev "$mission" QI-16 "$trace"

	"$CONTROL_BIN" raise-question --command-id "$prompt" --worker worker-dev \
		--mission "$mission" --queue-item QI-16 --trace "$(cc_uuid)" \
		--category architecture-decision --body-ref "note:prompt" --payload '{"q":1}' >/dev/null
	"$CONTROL_BIN" answer-question --command-id "$answer" --answers "$prompt" --by overseer \
		--trace "$(cc_uuid)" --category architecture-decision --body-ref "note:answer" \
		--payload '{"a":1}' >/dev/null
	"$CONTROL_BIN" cancel-mission --command-id "$cancel" --worker worker-dev \
		--mission "$mission" --queue-item QI-16 --trace "$(cc_uuid)" --reason "obsolete" \
		--requested-at 2026-01-01T00:00:00.000000Z --acknowledge-within 600 \
		--payload '{"cancel":1}' >/dev/null
	"$CONTROL_BIN" replace-mission --command-id "$replacement" --worker worker-dev \
		--mission "$mission" --replacement-mission "$replacement_mission" --queue-item QI-16 \
		--trace "$(cc_uuid)" --reason "obsolete" --payload '{"r":1}' >/dev/null
	cc_apply_replacement "$replacement" worker-dev

	# Destroy every derived byte: the projection and the compatibility view.
	rm -f "$root/ledger.json" "$root/events.jsonl"

	run "$CONTROL_BIN" mission-status --as-of 2026-01-01T01:00:00.000000Z
	[ "$status" -eq 0 ]
	echo "$output" | grep -Fq "dialog $prompt kind question state answered"
	echo "$output" | grep -Fq "answered-by $answer"
	echo "$output" | grep -Fq "dialog $answer kind reply state delivered"
	echo "$output" | grep -Fq "cancellation $cancel mission $mission worker worker-dev"
	echo "$output" | grep -Fq "observation timed-out reason acknowledgement-deadline-expired"
	echo "$output" | grep -Fq "slot worker-dev mission $replacement_mission queue-item QI-16 state reserved command $replacement replaces $mission"
	echo "$output" | grep -Fq "mission-state replaced"

	run "$CONTROL_BIN" lifecycle-status --as-of 2026-01-01T01:00:00.000000Z
	[ "$status" -eq 0 ]
	echo "$output" | grep -Fq "state replaced sequence 3 observation terminal"

	# The rebuilt projection is byte-identical to the one that was destroyed.
	run "$CONTROL_BIN" replay-ledger
	[ "$status" -eq 0 ]
	echo "$output" | grep -Fq "rebuilt ledger.json"
	run "$CONTROL_BIN" replay-ledger
	[ "$status" -eq 0 ]
	echo "$output" | grep -Fq "projection is current"
	run "$CONTROL_BIN" validate
	[ "$status" -eq 0 ]
	run "$CONTROL_BIN" preflight
	[ "$status" -eq 0 ]
	echo "$output" | grep -Fq "preflight ready"
}

@test "a hand-edited mission projection is refused as derived corruption and replayed" {
	local root="$BATS_TEST_TMPDIR/mission-derived-corruption"
	cc_mission_store "$root"
	local mission trace prompt
	mission="$(cc_uuid)"
	trace="$(cc_uuid)"
	prompt="$(cc_uuid)"
	cc_running worker-dev "$mission" QI-17 "$trace"
	"$CONTROL_BIN" raise-question --command-id "$prompt" --worker worker-dev \
		--mission "$mission" --queue-item QI-17 --trace "$(cc_uuid)" \
		--category architecture-decision --body-ref "note:x" --payload '{"q":1}' >/dev/null

	# A ledger claiming the prompt was answered is derived corruption, not
	# authority, and the rebuild from committed events replaces it.
	run python3 -c '
import json
import sys
from pathlib import Path

sys.path.insert(0, sys.argv[2])
import cockpit_control

root = Path(sys.argv[1])
path = root / cockpit_control.LEDGER_NAME
ledger = json.loads(path.read_text())
ledger[cockpit_control.LEDGER_MISSION_DIALOGS_FIELD][sys.argv[3]]["state"] = "answered"
path.write_text(json.dumps(ledger, indent=2, sort_keys=True) + "\n")
' "$root" "$MODULE_DIR" "$prompt"
	[ "$status" -eq 0 ]

	run "$CONTROL_BIN" validate
	[ "$status" -ne 0 ]
	echo "$output" | grep -Fq "requires non-empty answered_by_command_id"

	run "$CONTROL_BIN" mission-status
	[ "$status" -eq 0 ]
	echo "$output" | grep -Fq "dialog $prompt kind question state pending"

	run "$CONTROL_BIN" replay-ledger
	[ "$status" -eq 0 ]
	echo "$output" | grep -Fq "rebuilt ledger.json"
	run "$CONTROL_BIN" validate
	[ "$status" -eq 0 ]

	# Every new derived field is required, so deleting one is corruption too.
	local field
	for field in mission_dialogs mission_cancellations mission_slots; do
		run python3 -c '
import json
import sys
from pathlib import Path

sys.path.insert(0, sys.argv[2])
import cockpit_control

root = Path(sys.argv[1])
path = root / cockpit_control.LEDGER_NAME
ledger = json.loads(path.read_text())
del ledger[sys.argv[3]]
path.write_text(json.dumps(ledger, indent=2, sort_keys=True) + "\n")
' "$root" "$MODULE_DIR" "$field"
		[ "$status" -eq 0 ]
		run "$CONTROL_BIN" validate
		[ "$status" -ne 0 ]
		echo "$output" | grep -Fq "ledger.json requires $field"
		run "$CONTROL_BIN" replay-ledger
		[ "$status" -eq 0 ]
		echo "$output" | grep -Fq "rebuilt ledger.json"
		run "$CONTROL_BIN" validate
		[ "$status" -eq 0 ]
	done
}

@test "a lifecycle event cannot claim a mission slot another claim already holds" {
	# Cross-story regression (TH3.E2.US1 + US3).  A replacement reserves the
	# worker's one slot for a mission ID that has emitted no lifecycle event
	# yet, so `apply_worker_lifecycle` has nothing materialized to correlate the
	# first event against.  Without the slot-claim check a second worker could
	# accept the reserved mission and hold it at the same time as the
	# reservation -- two claimed slots naming one mission, with no conflict
	# recorded anywhere -- and the reserving worker could re-point its own
	# reservation at a queue item the replacement never declared.
	local root="$BATS_TEST_TMPDIR/mission-slot-claim"
	cc_mission_store "$root"
	local prior reserved replacement
	prior="$(cc_uuid)"
	reserved="$(cc_uuid)"
	replacement="$(cc_uuid)"
	cc_running worker-dev "$prior" QI-30 "$(cc_uuid)"
	"$CONTROL_BIN" replace-mission --command-id "$replacement" --worker worker-dev \
		--mission "$prior" --replacement-mission "$reserved" --queue-item QI-30 \
		--trace "$(cc_uuid)" --reason "the queue item was superseded" \
		--payload '{"replace":"reserved"}' >/dev/null
	cc_apply_replacement "$replacement" worker-dev
	run "$CONTROL_BIN" mission-status
	[ "$status" -eq 0 ]
	echo "$output" | grep -Fq "slot worker-dev mission $reserved queue-item QI-30 state reserved"

	# Another worker may not materialize the mission the reservation holds.
	run "$CONTROL_BIN" record-lifecycle --state accepted --worker worker-test \
		--mission "$reserved" --queue-item QI-30 --trace "$(cc_uuid)" --sequence 1 \
		--fresh-for 3600
	[ "$status" -eq 0 ]
	echo "$output" | grep -Fq "is retained for audit only (retained-unmatched-correlation"
	cc_absent_output "advanced the materialized mission state"

	# Neither may the reserving worker accept it against another queue item.
	run "$CONTROL_BIN" record-lifecycle --state accepted --worker worker-dev \
		--mission "$reserved" --queue-item QI-99 --trace "$(cc_uuid)" --sequence 1 \
		--fresh-for 3600
	[ "$status" -eq 0 ]
	echo "$output" | grep -Fq "is retained for audit only (retained-unmatched-correlation"
	cc_absent_output "advanced the materialized mission state"

	# Both refusals are durable, queryable slot conflicts rather than messages.
	run "$CONTROL_BIN" mission-status
	[ "$status" -eq 0 ]
	echo "$output" | grep -Fq "2 recorded conflict(s)"
	echo "$output" | grep -Fq "mission-conflict worker-test mission $reserved reason reused-mission-id source lifecycle command -"
	echo "$output" | grep -Fq "mission-conflict worker-dev mission $reserved reason unmatched-mission-slot source lifecycle command -"
	echo "$output" | grep -Fq "slot worker-dev mission $reserved queue-item QI-30 state reserved"
	cc_absent_output "slot worker-test mission $reserved"

	# The worker the replacement actually reserved the slot for still accepts it
	# on the queue item that replacement declared, and the slot becomes active.
	run "$CONTROL_BIN" record-lifecycle --state accepted --worker worker-dev \
		--mission "$reserved" --queue-item QI-30 --trace "$(cc_uuid)" --sequence 1 \
		--fresh-for 3600
	[ "$status" -eq 0 ]
	echo "$output" | grep -Fq "advanced the materialized mission state"
	run "$CONTROL_BIN" mission-status
	[ "$status" -eq 0 ]
	echo "$output" | grep -Fq "slot worker-dev mission $reserved queue-item QI-30 state active"

	run python3 -c '
import json
import sys
from pathlib import Path

sys.path.insert(0, sys.argv[2])
import cockpit_control

root = Path(sys.argv[1])
prior, reserved = sys.argv[3:5]
ledger = json.loads((root / cockpit_control.LEDGER_NAME).read_text())
missions = ledger[cockpit_control.LEDGER_WORKER_MISSIONS_FIELD]
slots = ledger[cockpit_control.LEDGER_MISSION_SLOTS_FIELD]

# One mission ID is held by at most one slot, whichever half of the protocol
# claimed it: the lifecycle path may not break the invariant the replacement
# path already enforces.
claimed = [
    (worker, slot["mission_id"]) for worker, slot in sorted(slots.items())
    if slot["state"] in cockpit_control.MISSION_SLOT_CLAIMED_STATES
]
held = [mission_id for _worker, mission_id in claimed]
assert sorted(held) == sorted(set(held)), claimed
assert claimed == [("worker-dev", reserved)], claimed

# The reservation kept the queue item the replacement declared, and the worker
# that never owned it holds nothing at all.
dev = slots["worker-dev"]
assert dev["queue_item_id"] == "QI-30", dev
assert dev["state"] == cockpit_control.MISSION_SLOT_ACTIVE, dev
assert dev["replaces"] == prior, dev
test = slots["worker-test"]
assert test["state"] == cockpit_control.MISSION_SLOT_UNCLAIMED, test
assert test["mission_id"] is None and test["queue_item_id"] is None, test

recorded = sorted(
    (worker, conflict["mission_id"], conflict["reason"], conflict["source"])
    for worker, slot in slots.items()
    for conflict in slot["conflicts"]
)
assert recorded == [
    ("worker-dev", reserved, cockpit_control.MISSION_SLOT_CONFLICT_UNMATCHED, "lifecycle"),
    ("worker-test", reserved, cockpit_control.MISSION_SLOT_CONFLICT_REUSED, "lifecycle"),
], recorded

# The refused events materialized no mission at all, and the one mission that
# did materialize belongs to the worker and queue item that owned the slot.
assert sorted(missions) == sorted([prior, reserved]), sorted(missions)
assert missions[prior]["lifecycle"]["state"] == cockpit_control.LIFECYCLE_REPLACED
assert missions[prior]["lifecycle"]["superseded_by_mission_id"] == reserved
current = missions[reserved]["lifecycle"]
assert current["state"] == cockpit_control.LIFECYCLE_ACCEPTED, current
assert current["worker_id"] == "worker-dev", current
assert current["queue_item_id"] == "QI-30", current
print("no lifecycle event bled a mission across the single slot")
' "$root" "$MODULE_DIR" "$prior" "$reserved"
	[ "$status" -eq 0 ]
	echo "$output" | grep -Fq "no lifecycle event bled a mission across the single slot"

	# The refusals are re-derived from committed events after losing every
	# derived byte, and replay rebuilds the same projection.
	rm -f "$root/ledger.json" "$root/events.jsonl"
	run "$CONTROL_BIN" mission-status
	[ "$status" -eq 0 ]
	echo "$output" | grep -Fq "2 recorded conflict(s)"
	echo "$output" | grep -Fq "slot worker-dev mission $reserved queue-item QI-30 state active"
	run "$CONTROL_BIN" replay-ledger
	[ "$status" -eq 0 ]
	run "$CONTROL_BIN" validate
	[ "$status" -eq 0 ]
	run "$CONTROL_BIN" preflight
	[ "$status" -eq 0 ]
	echo "$output" | grep -Fq "preflight ready"
}

@test "a prompt whose mission ended is observed orphaned and never counted as answerable" {
	# Cross-story regression (TH3.E2.US1 + US3).  `answer-question` fails closed
	# on a mission that is no longer active, so a prompt left pending when its
	# mission completes or is replaced can never be answered.  Reporting it as a
	# pending prompt that "is answerable only through cockpit-control
	# answer-question" told the operator to run a command that cannot succeed and
	# left the pending count permanently inflated.
	local root="$BATS_TEST_TMPDIR/mission-orphaned-prompt"
	cc_mission_store "$root"
	local finished replaced replacement trace_a trace_b prompt_a prompt_b replace_id
	finished="$(cc_uuid)"
	replaced="$(cc_uuid)"
	replacement="$(cc_uuid)"
	trace_a="$(cc_uuid)"
	trace_b="$(cc_uuid)"
	prompt_a="$(cc_uuid)"
	prompt_b="$(cc_uuid)"
	replace_id="$(cc_uuid)"
	cc_running worker-dev "$finished" QI-31 "$trace_a"
	cc_running worker-qa "$replaced" QI-32 "$trace_b"
	"$CONTROL_BIN" raise-question --command-id "$prompt_a" --worker worker-dev \
		--mission "$finished" --queue-item QI-31 --trace "$(cc_uuid)" \
		--category architecture-decision --body-ref "report:worker-dev/$finished/2" \
		--payload '{"question":"which adapter?"}' >/dev/null
	"$CONTROL_BIN" raise-question --command-id "$prompt_b" --kind access-prompt \
		--worker worker-qa --mission "$replaced" --queue-item QI-32 --trace "$(cc_uuid)" \
		--category filesystem-access --body-ref "report:worker-qa/$replaced/2" \
		--payload '{"question":"may I write?"}' >/dev/null

	# While both missions are active both prompts are answerable.
	run "$CONTROL_BIN" mission-status
	[ "$status" -eq 0 ]
	echo "$output" | grep -Fq "2 pending prompt(s)"
	echo "$output" | grep -Fq "0 orphaned prompt(s)"
	echo "$output" | grep -Fq "dialog $prompt_a kind question state pending"
	[ "$(printf '%s\n' "$output" | grep -c "observation answerable")" -eq 2 ]
	cc_absent_output "observation orphaned"

	# One mission completes and the other is replaced, and neither prompt was
	# ever answered.
	cc_emit --state completed --worker worker-dev --mission "$finished" --queue-item QI-31 \
		--trace "$trace_a" --sequence 3 --evidence "test:RUN-1"
	"$CONTROL_BIN" replace-mission --command-id "$replace_id" --worker worker-qa \
		--mission "$replaced" --replacement-mission "$replacement" --queue-item QI-32 \
		--trace "$trace_b" --reason "the queue item was superseded" \
		--payload '{"replace":"orphan"}' >/dev/null
	cc_apply_replacement "$replace_id" worker-qa

	run "$CONTROL_BIN" mission-status
	[ "$status" -eq 0 ]
	echo "$output" | grep -Fq "0 pending prompt(s)"
	echo "$output" | grep -Fq "2 orphaned prompt(s)"
	echo "$output" | grep -Fq "dialog $prompt_a kind question state pending"
	[ "$(printf '%s\n' "$output" | grep -c "observation orphaned")" -eq 2 ]
	cc_absent_output "observation answerable"
	# The surface no longer claims an unanswerable prompt is answerable, and it
	# says the recoverable reason instead.
	cc_absent_output "it is answerable only through"
	echo "$output" | grep -Fq "mission $finished on worker-dev has an orphaned question ($prompt_a) (mission-no-longer-active); it is not answerable through cockpit-control answer-question and awaits a bounded recovery action"
	echo "$output" | grep -Fq "mission $replaced on worker-qa has an orphaned access-prompt ($prompt_b) (mission-no-longer-active); it is not answerable through cockpit-control answer-question and awaits a bounded recovery action"

	# The observation agrees with what the protocol actually allows: both
	# answers are refused, fail closed, and change nothing.
	local out="$BATS_TEST_TMPDIR/orphan-stdout" err="$BATS_TEST_TMPDIR/orphan-stderr"
	local before
	before="$(cc_control_contents "$root")"
	cc_refuse answer-question "$out" "$err" --command-id "$(cc_uuid)" --answers "$prompt_a" \
		--by overseer --trace "$(cc_uuid)" --category architecture-decision \
		--body-ref "human-decision:x" --payload '{"answer":"B"}'
	grep -Fq "is no longer active" "$err"
	cc_refuse answer-question "$out" "$err" --command-id "$(cc_uuid)" --answers "$prompt_b" \
		--by overseer --trace "$(cc_uuid)" --category filesystem-access \
		--body-ref "human-decision:x" --payload '{"answer":"no"}'
	grep -Fq "is no longer active" "$err"
	[ "$before" = "$(cc_control_contents "$root")" ]

	# An orphaned prompt is an observation, not a state: the committed record and
	# the derived projection both still say `pending`, so nothing was rewritten.
	run python3 -c '
import json
import sys
from pathlib import Path

sys.path.insert(0, sys.argv[2])
import cockpit_control

root = Path(sys.argv[1])
prompt_a, prompt_b = sys.argv[3:5]
ledger = json.loads((root / cockpit_control.LEDGER_NAME).read_text())
dialogs = ledger[cockpit_control.LEDGER_MISSION_DIALOGS_FIELD]
for command_id in (prompt_a, prompt_b):
    entry = dialogs[command_id]
    assert entry["state"] == cockpit_control.MISSION_DIALOG_PENDING, entry
    assert entry["answered_by_command_id"] is None, entry
    assert entry["answered_at"] is None, entry

report = cockpit_control.observe_mission_control(root)
observed = {
    entry["dialog"]["command_id"]: report.dialog_observation(entry)
    for entry in report.dialogs
}
assert observed == {
    prompt_a: cockpit_control.MISSION_DIALOG_OBSERVATION_ORPHANED,
    prompt_b: cockpit_control.MISSION_DIALOG_OBSERVATION_ORPHANED,
}, observed
assert report.pending_prompts == (), report.pending_prompts
assert len(report.orphaned_prompts) == 2, report.orphaned_prompts
print("an orphaned prompt is an observation and never a rewritten state")
' "$root" "$MODULE_DIR" "$prompt_a" "$prompt_b"
	[ "$status" -eq 0 ]
	echo "$output" | grep -Fq "an orphaned prompt is an observation and never a rewritten state"

	# The observation is derived from committed events, so it survives losing
	# every derived byte, and replay rebuilds the same projection.
	rm -f "$root/ledger.json" "$root/events.jsonl"
	run "$CONTROL_BIN" mission-status
	[ "$status" -eq 0 ]
	echo "$output" | grep -Fq "0 pending prompt(s)"
	echo "$output" | grep -Fq "2 orphaned prompt(s)"
	run "$CONTROL_BIN" replay-ledger
	[ "$status" -eq 0 ]
	run "$CONTROL_BIN" validate
	[ "$status" -eq 0 ]
}
