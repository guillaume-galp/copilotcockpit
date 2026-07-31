#!/usr/bin/env bats
# tests/unit/uninstall.bats — uninstall.sh coverage under an isolated HOME.

load helper

setup() {
	cc_setup_fake_home
}

@test "uninstall: removes managed Copilot and Codex skills plus cockpit tools" {
	run "$CC_BOOTSTRAP" global
	[ "$status" -eq 0 ]
	run "$CC_BOOTSTRAP" codex-global
	[ "$status" -eq 0 ]

	mkdir -p "$HOME/.copilot/skills/unrelated" "$HOME/.agents/skills/unrelated"
	printf 'keep\n' > "$HOME/.copilot/skills/unrelated/SKILL.md"
	printf 'keep\n' > "$HOME/.agents/skills/unrelated/SKILL.md"

	run "$CC_UNINSTALL"
	[ "$status" -eq 0 ]

	[ ! -e "$HOME/.copilot/skills/worker-dev/SKILL.md" ]
	[ ! -e "$HOME/.agents/skills/worker-dev/SKILL.md" ]
	[ ! -e "$HOME/.local/bin/cockpit-wake" ]
	[ ! -e "$HOME/.local/bin/cockpit-protocol" ]
	[ ! -e "$HOME/.local/bin/cockpit-protocol.go" ]
	[ ! -e "$HOME/.local/bin/cockpit-overseer" ]
	[ ! -e "$HOME/.local/bin/cockpit-trace" ]
	[ ! -e "$HOME/.local/bin/cockpit-queue" ]
	[ -f "$HOME/.copilot/skills/unrelated/SKILL.md" ]
	[ -f "$HOME/.agents/skills/unrelated/SKILL.md" ]
}

@test "uninstall --dry-run removes nothing" {
	run "$CC_BOOTSTRAP" global
	[ "$status" -eq 0 ]
	run "$CC_BOOTSTRAP" codex-global
	[ "$status" -eq 0 ]

	run "$CC_UNINSTALL" --dry-run
	[ "$status" -eq 0 ]
	echo "$output" | grep -q "would remove"

	[ -f "$HOME/.copilot/skills/worker-dev/SKILL.md" ]
	[ -f "$HOME/.agents/skills/worker-dev/SKILL.md" ]
	[ -f "$HOME/.local/bin/cockpit-wake" ]
	[ -f "$HOME/.local/bin/cockpit-protocol" ]
	[ -f "$HOME/.local/bin/cockpit-protocol.go" ]
	[ -f "$HOME/.local/bin/cockpit-overseer" ]
	[ -f "$HOME/.local/bin/cockpit-trace" ]
	[ -f "$HOME/.local/bin/cockpit-queue" ]
}

@test "uninstall --codex-only leaves Copilot skills and cockpit tools" {
	run "$CC_BOOTSTRAP" global
	[ "$status" -eq 0 ]
	run "$CC_BOOTSTRAP" codex-global
	[ "$status" -eq 0 ]

	run "$CC_UNINSTALL" --codex-only
	[ "$status" -eq 0 ]

	[ -f "$HOME/.copilot/skills/worker-dev/SKILL.md" ]
	[ ! -e "$HOME/.agents/skills/worker-dev/SKILL.md" ]
	[ -f "$HOME/.local/bin/cockpit-wake" ]
	[ -f "$HOME/.local/bin/cockpit-protocol" ]
	[ -f "$HOME/.local/bin/cockpit-protocol.go" ]
	[ -f "$HOME/.local/bin/cockpit-overseer" ]
	[ -f "$HOME/.local/bin/cockpit-trace" ]
	[ -f "$HOME/.local/bin/cockpit-queue" ]
}

@test "uninstall: unknown option exits 2" {
	run "$CC_UNINSTALL" --bogus
	[ "$status" -eq 2 ]
	echo "$output" | grep -q "unknown option"
}
