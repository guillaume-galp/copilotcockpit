#!/usr/bin/env bash
# uninstall.sh — remove copilotcockpit's managed user-scoped install.
#
# This only removes artefacts owned by this project:
#   * ~/.copilot/skills/<managed-role>/SKILL.md and empty role dirs
#   * ~/.agents/skills/<managed-role>/SKILL.md and empty role dirs
#   * ~/.local/bin/cockpit-wake
#   * ~/.local/bin/cockpit-protocol
#   * ~/.local/bin/cockpit-protocol.go
#   * ~/.local/bin/cockpit-overseer
#   * ~/.local/bin/cockpit-trace
#   * ~/.local/bin/cockpit-queue
#   * ~/.local/bin/cockpit-control
#   * ~/.local/bin/cockpit_control.py
#
# It never removes unrelated skills, backup files, parent skills directories, or
# repository-scoped Codex overlays under a checkout.
set -euo pipefail

cc_roles="copilotcockpit-dev e2e-cockpit e2e-operator setup-e2e-cockpit setup-e2e-runbook worker-dev worker-fix worker-test"
cc_dry_run=0
cc_target_copilot=1
cc_target_codex=1
cc_target_bin=1

cc_log() { printf 'uninstall.sh: %s\n' "$*" >&2; }

usage() {
	cat <<'EOF'
Usage: uninstall.sh [--dry-run] [--copilot-only] [--codex-only] [--keep-cockpit-tools]

Remove copilotcockpit's managed user-scoped install:
  ~/.copilot/skills/<role>/SKILL.md
  ~/.agents/skills/<role>/SKILL.md
  ~/.local/bin/cockpit-wake
  ~/.local/bin/cockpit-protocol
  ~/.local/bin/cockpit-protocol.go
  ~/.local/bin/cockpit-overseer
  ~/.local/bin/cockpit-trace
  ~/.local/bin/cockpit-queue
  ~/.local/bin/cockpit-control
  ~/.local/bin/cockpit_control.py

Options:
  --dry-run             Describe removals; change nothing.
  --copilot-only        Remove only legacy Copilot skills and cockpit tools.
  --codex-only          Remove only Codex user skills.
  --keep-cockpit-tools  Leave ~/.local/bin/cockpit-* tools in place.
  -h, --help            Show this help and exit.
EOF
}

cc_remove_file() {
	local path="$1"
	if [[ -e "$path" || -L "$path" ]]; then
		if [[ "$cc_dry_run" -ne 0 ]]; then
			cc_log "would remove $path"
		else
			rm -f "$path"
			cc_log "removed $path"
		fi
	else
		cc_log "already absent: $path"
	fi
}

cc_remove_empty_dir() {
	local path="$1"
	if [[ -d "$path" ]]; then
		if [[ "$cc_dry_run" -ne 0 ]]; then
			cc_log "would remove $path if empty"
		else
			rmdir "$path" 2>/dev/null || true
		fi
	fi
}

cc_uninstall_skills() {
	local root="$1" role role_dir
	for role in $cc_roles; do
		role_dir="$root/$role"
		cc_remove_file "$role_dir/SKILL.md"
		cc_remove_empty_dir "$role_dir"
	done
}

while [[ $# -gt 0 ]]; do
	case "$1" in
	--dry-run)
		cc_dry_run=1
		shift
		;;
	--copilot-only)
		cc_target_copilot=1
		cc_target_codex=0
		cc_target_bin=1
		shift
		;;
	--codex-only)
		cc_target_copilot=0
		cc_target_codex=1
		cc_target_bin=0
		shift
		;;
	--keep-cockpit-tools|--keep-cockpit-wake)
		cc_target_bin=0
		shift
		;;
	-h | --help)
		usage
		exit 0
		;;
	*)
		cc_log "unknown option: $1"
		usage >&2
		exit 2
		;;
	esac
done

if [[ "$cc_target_copilot" -ne 0 ]]; then
	cc_log "removing legacy Copilot skills from $HOME/.copilot/skills"
	cc_uninstall_skills "$HOME/.copilot/skills"
fi

if [[ "$cc_target_codex" -ne 0 ]]; then
	cc_log "removing Codex user skills from $HOME/.agents/skills"
	cc_uninstall_skills "$HOME/.agents/skills"
fi

if [[ "$cc_target_bin" -ne 0 ]]; then
	cc_remove_file "$HOME/.local/bin/cockpit-wake"
	cc_remove_file "$HOME/.local/bin/cockpit-protocol"
	cc_remove_file "$HOME/.local/bin/cockpit-protocol.go"
	cc_remove_file "$HOME/.local/bin/cockpit-overseer"
	cc_remove_file "$HOME/.local/bin/cockpit-trace"
	cc_remove_file "$HOME/.local/bin/cockpit-queue"
	cc_remove_file "$HOME/.local/bin/cockpit-control"
	cc_remove_file "$HOME/.local/bin/cockpit_control.py"
fi

cc_log "uninstall complete"
