#!/usr/bin/env bats
# tests/unit/cmd-trace.bats — Category-1 unit tests for bin/cockpit-trace.

load helper

setup() {
	cc_setup_fake_home
}

write_archive_fixture() {
	local archive_dir="$HOME/.config/cockpit-overseer/archive"
	mkdir -p "$archive_dir"
	cat > "$archive_dir/index.jsonl" <<'EOF'
{"action":"dispatch","brief_raw":"TASK: launch root mission","brief_bytes":86,"brief_lines":4,"estimated_tokens":22,"label":"root","line_count":4,"mode":"n/a","pane_before":"","pane_before_hash":"","pane_before_lines":0,"parent_trace_id":"","ref":"","run_id":"","session":"ulysses","summary":"dispatch","text":"TRACE-ID: 11111111-1111-1111-1111-111111111111\n\nTASK: launch root mission\n","timestamp":"2026-06-19T16:00:00Z","tool":"cockpit-overseer","trace_id":"11111111-1111-1111-1111-111111111111","window":"worker-dev"}
{"action":"loop","aic_used":"","estimated_tokens":18,"line_count":3,"mode":"normal","parent_trace_id":"","pane_hash":"hash1","run_id":"","session":"ulysses","summary":"working","text":"TRACE-ID: 11111111-1111-1111-1111-111111111111\nWORKER-DEV DONE\n  trace_id: 11111111-1111-1111-1111-111111111111\n","timestamp":"2026-06-19T16:01:00Z","tool":"cockpit-overseer","trace_id":"11111111-1111-1111-1111-111111111111","window":"worker-dev"}
{"action":"dispatch","brief_raw":"TASK: child mission","brief_bytes":75,"brief_lines":5,"estimated_tokens":19,"label":"child","line_count":5,"mode":"n/a","pane_before":"TRACE-ID: 11111111-1111-1111-1111-111111111111\nWORKER-DEV DONE\n  trace_id: 11111111-1111-1111-1111-111111111111\n","pane_before_hash":"hash2","pane_before_lines":3,"parent_trace_id":"11111111-1111-1111-1111-111111111111","ref":"","run_id":"","session":"ulysses","summary":"dispatch","text":"TRACE-ID: 22222222-2222-2222-2222-222222222222\nPARENT-TRACE-ID: 11111111-1111-1111-1111-111111111111\n\nTASK: child mission\n","timestamp":"2026-06-19T16:02:00Z","tool":"cockpit-overseer","trace_id":"22222222-2222-2222-2222-222222222222","window":"worker-fix"}
EOF
}

@test "cockpit-trace show prints one trace thread" {
	write_archive_fixture

	run "$BATS_TEST_DIRNAME/../../bin/cockpit-trace" show 11111111-1111-1111-1111-111111111111
	[ "$status" -eq 0 ]
	echo "$output" | grep -q "TRACE 11111111-1111-1111-1111-111111111111"
	echo "$output" | grep -q "worker-dev"
	echo "$output" | grep -q "launch root mission"
	! echo "$output" | grep -q "child mission"
}

@test "cockpit-trace tree stitches child traces" {
	write_archive_fixture

	run "$BATS_TEST_DIRNAME/../../bin/cockpit-trace" tree 11111111-1111-1111-1111-111111111111
	[ "$status" -eq 0 ]
	echo "$output" | grep -q "TRACE FAMILY 11111111-1111-1111-1111-111111111111"
	echo "$output" | grep -q "worker-fix"
	echo "$output" | grep -q "child mission"
}
