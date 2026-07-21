#!/usr/bin/env bats
# tests/unit/install.bats — install.sh idempotent update behavior.

load helper

setup() {
	cc_setup_fake_home
}

cc_sha256_of() {
	if command -v sha256sum >/dev/null 2>&1; then
		sha256sum "$1" | awk '{print $1; exit}'
	else
		shasum -a 256 "$1" | awk '{print $1; exit}'
	fi
}

cc_make_release_fixture() {
	local fixture="$BATS_TEST_TMPDIR/release-$RANDOM"
	mkdir -p "$fixture"
	tar -czf "$fixture/copilotcockpit.tar.gz" -C "$(dirname "$CC_REPO_ROOT")" "$(basename "$CC_REPO_ROOT")"
	local sum
	sum="$(cc_sha256_of "$fixture/copilotcockpit.tar.gz")"
	printf '%s  %s\n' "$sum" "copilotcockpit.tar.gz" > "$fixture/copilotcockpit.tar.gz.sha256"
	printf '%s' "$fixture"
}

@test "install.sh: reruns are idempotent and do not extract into caller CWD" {
	local fixture
	fixture="$(cc_make_release_fixture)"
	local work="$BATS_TEST_TMPDIR/work"
	mkdir -p "$work"
	cd "$work"

	run env HOME="$HOME" CC_RELEASE_BASE_URL="file://$fixture" bash "$CC_REPO_ROOT/install.sh"
	[ "$status" -eq 0 ]
	[ ! -d "$work/copilotcockpit" ]
	[ -f "$HOME/.copilot/skills/worker-dev/SKILL.md" ]
	[ -f "$HOME/.agents/skills/worker-dev/SKILL.md" ]
	[ "$(cc_count_backups "$HOME")" -eq 0 ]

	run env HOME="$HOME" CC_RELEASE_BASE_URL="file://$fixture" bash "$CC_REPO_ROOT/install.sh"
	[ "$status" -eq 0 ]
	[ ! -d "$work/copilotcockpit" ]
	echo "$output" | grep -q "already current"
	[ "$(cc_count_backups "$HOME")" -eq 0 ]
}
