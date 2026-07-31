#!/usr/bin/env bats
# tests/unit/cmd-queue.bats — Category-1 unit tests for bin/cockpit-queue.

load helper

setup() {
	cc_setup_fake_home
	export COCKPIT_QUEUE_ROOT="$BATS_TEST_TMPDIR/queue"
}

@test "queue root must be explicit to avoid crossing cockpit scopes" {
	unset COCKPIT_QUEUE_ROOT

	run "$BATS_TEST_DIRNAME/../../bin/cockpit-queue" list
	[ "$status" -ne 0 ]
	echo "$output" | grep -q "COCKPIT_QUEUE_ROOT is required"
}

@test "enqueue accepts build-method requests and persists item plus event" {
	run "$BATS_TEST_DIRNAME/../../bin/cockpit-queue" enqueue \
		--id QI-test-1 \
		--text "Please build this /the-copilot-build-method feature" \
		--actor overseer
	[ "$status" -eq 0 ]
	[ "$output" = "QI-test-1" ]
	[ -f "$COCKPIT_QUEUE_ROOT/items/QI-test-1.yaml" ]
	[ -f "$COCKPIT_QUEUE_ROOT/events.jsonl" ]
	grep -q '"state": "queued"' "$COCKPIT_QUEUE_ROOT/items/QI-test-1.yaml"
	grep -q '"type": "item-created"' "$COCKPIT_QUEUE_ROOT/events.jsonl"
}

@test "enqueue rejects non-build requests without creating active item" {
	run "$BATS_TEST_DIRNAME/../../bin/cockpit-queue" enqueue \
		--id QI-nope \
		--text "What is the current status?" \
		--actor overseer
	[ "$status" -ne 0 ]
	[ ! -f "$COCKPIT_QUEUE_ROOT/items/QI-nope.yaml" ]
	grep -q '"type": "request-rejected"' "$COCKPIT_QUEUE_ROOT/events.jsonl"
}

@test "start-next respects FIFO and rejects second active item" {
	"$BATS_TEST_DIRNAME/../../bin/cockpit-queue" enqueue --id QI-a --text "A /the-copilot-build-method" >/dev/null
	"$BATS_TEST_DIRNAME/../../bin/cockpit-queue" enqueue --id QI-b --text "B /the-copilot-build-method" >/dev/null

	run "$BATS_TEST_DIRNAME/../../bin/cockpit-queue" start-next
	[ "$status" -eq 0 ]
	[ "$output" = "QI-a" ]
	grep -q '"state": "shaping"' "$COCKPIT_QUEUE_ROOT/items/QI-a.yaml"

	run "$BATS_TEST_DIRNAME/../../bin/cockpit-queue" start-next
	[ "$status" -ne 0 ]
	echo "$output" | grep -q "active item exists: QI-a"
}

@test "pause blocks start-next until resume" {
	"$BATS_TEST_DIRNAME/../../bin/cockpit-queue" enqueue --id QI-a --text "A /the-copilot-build-method" >/dev/null
	"$BATS_TEST_DIRNAME/../../bin/cockpit-queue" pause >/dev/null

	run "$BATS_TEST_DIRNAME/../../bin/cockpit-queue" start-next
	[ "$status" -ne 0 ]
	echo "$output" | grep -q "queue is paused"

	"$BATS_TEST_DIRNAME/../../bin/cockpit-queue" resume >/dev/null
	run "$BATS_TEST_DIRNAME/../../bin/cockpit-queue" start-next
	[ "$status" -eq 0 ]
}

@test "pause does not block enqueue intake" {
	"$BATS_TEST_DIRNAME/../../bin/cockpit-queue" pause >/dev/null

	run "$BATS_TEST_DIRNAME/../../bin/cockpit-queue" enqueue \
		--id QI-a \
		--text "A /the-copilot-build-method" \
		--actor overseer
	[ "$status" -eq 0 ]
	[ "$output" = "QI-a" ]
	grep -q '"state": "queued"' "$COCKPIT_QUEUE_ROOT/items/QI-a.yaml"
}

@test "multiple active items fail before starting or clearing work" {
	"$BATS_TEST_DIRNAME/../../bin/cockpit-queue" enqueue --id QI-a --text "A /the-copilot-build-method" >/dev/null
	"$BATS_TEST_DIRNAME/../../bin/cockpit-queue" enqueue --id QI-b --text "B /the-copilot-build-method" >/dev/null
	"$BATS_TEST_DIRNAME/../../bin/cockpit-queue" transition QI-a shaping --reason "simulate active one" >/dev/null
	"$BATS_TEST_DIRNAME/../../bin/cockpit-queue" transition QI-b shaping --reason "simulate active two" >/dev/null

	run "$BATS_TEST_DIRNAME/../../bin/cockpit-queue" start-next
	[ "$status" -ne 0 ]
	echo "$output" | grep -q "multiple active items detected: QI-a, QI-b"

	run "$BATS_TEST_DIRNAME/../../bin/cockpit-queue" clear-current --waiver "human recovery"
	[ "$status" -ne 0 ]
	echo "$output" | grep -q "multiple active items detected: QI-a, QI-b"
}

@test "clear-current requires delivered state and E2E evidence or waiver" {
	"$BATS_TEST_DIRNAME/../../bin/cockpit-queue" enqueue --id QI-a --text "A /the-copilot-build-method" >/dev/null
	"$BATS_TEST_DIRNAME/../../bin/cockpit-queue" start-next >/dev/null

	run "$BATS_TEST_DIRNAME/../../bin/cockpit-queue" clear-current
	[ "$status" -ne 0 ]
	echo "$output" | grep -q "must be delivered"

	"$BATS_TEST_DIRNAME/../../bin/cockpit-queue" transition QI-a delivered --reason "local delivery done" >/dev/null
	run "$BATS_TEST_DIRNAME/../../bin/cockpit-queue" clear-current
	[ "$status" -ne 0 ]
	echo "$output" | grep -q "requires --e2e-run or --waiver"

	run "$BATS_TEST_DIRNAME/../../bin/cockpit-queue" clear-current --e2e-run RUN-1
	[ "$status" -eq 0 ]
	grep -q '"state": "cleared"' "$COCKPIT_QUEUE_ROOT/items/QI-a.yaml"
	grep -q '"e2e_run": "RUN-1"' "$COCKPIT_QUEUE_ROOT/items/QI-a.yaml"
}
