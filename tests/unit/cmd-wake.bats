#!/usr/bin/env bats
# tests/unit/cmd-wake.bats — Category-1 unit tests for bin/cockpit-wake.

load helper

setup() {
	cc_setup_fake_home
	mkdir -p "$BATS_TEST_TMPDIR/bin" "$BATS_TEST_TMPDIR/tmux"
	export PATH="$BATS_TEST_TMPDIR/bin:$PATH"

	cat >"$BATS_TEST_TMPDIR/bin/at" <<'EOF'
#!/usr/bin/env bash
cat >/dev/null
printf 'job 123 at someday\n' >&2
EOF
	chmod +x "$BATS_TEST_TMPDIR/bin/at"

	cat >"$BATS_TEST_TMPDIR/bin/atrm" <<'EOF'
#!/usr/bin/env bash
exit 0
EOF
	chmod +x "$BATS_TEST_TMPDIR/bin/atrm"

	cat >"$BATS_TEST_TMPDIR/bin/notify-send" <<'EOF'
#!/usr/bin/env bash
exit 0
EOF
	chmod +x "$BATS_TEST_TMPDIR/bin/notify-send"

	cat >"$BATS_TEST_TMPDIR/bin/tmux" <<EOF
#!/usr/bin/env bash
printf '%s\n' "\$*" >> "$BATS_TEST_TMPDIR/tmux/calls.log"
exit 0
EOF
	chmod +x "$BATS_TEST_TMPDIR/bin/tmux"
}

@test "fire pastes the wake message and presses Enter in the target pane" {
	run "$BATS_TEST_DIRNAME/../../bin/cockpit-wake" schedule \
		--once "23:59 2099-01-01" \
		-s cockpit-a \
		-w overseer \
		-m "Wake up and run the loop" \
		--label "loop"
	[ "$status" -eq 0 ]
	id="$(printf '%s\n' "$output" | sed -nE 's/.*id=(wake-[0-9]+).*/\1/p' | head -n1)"
	[ -n "$id" ]

	run "$BATS_TEST_DIRNAME/../../bin/cockpit-wake" fire "$id"
	[ "$status" -eq 0 ]

	grep -q "load-buffer .*/${id}.msg" "$BATS_TEST_TMPDIR/tmux/calls.log"
	grep -q "paste-buffer -t cockpit-a:overseer" "$BATS_TEST_TMPDIR/tmux/calls.log"
	grep -q "send-keys -t cockpit-a:overseer Enter" "$BATS_TEST_TMPDIR/tmux/calls.log"
	! grep -q 'send-keys -t cockpit-a:overseer "" Enter' "$BATS_TEST_TMPDIR/tmux/calls.log"
}
