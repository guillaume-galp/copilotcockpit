#!/usr/bin/env bash
# lib/cmd-codex.sh — `bootstrap.sh codex-global|codex-repo`:
# install/update the managed skills into:
#   * ~/.agents/skills/<role>/SKILL.md   (codex-global)
#   * .agents/skills/<role>/SKILL.md      (codex-repo)
#
# Mirrors global installation semantics where practical (backup-before-overwrite,
# idempotency, dry-run, --link). Relative symlinks are used for repo overlays so
# the project can move safely after bootstrap.
#
# Portability (TH1-E6-US1): bash 3.2-safe; no readlink -f, no sed -i, no
# GNU-only assumptions.
set -euo pipefail

# --- Resolve own directory portably and source shared helpers ----------------
_cc_self="$0"
if command -v python3 >/dev/null 2>&1; then
	_cc_self="$(python3 -c 'import os,sys; print(os.path.realpath(sys.argv[1]))' "$0")"
fi
CC_ROOT="$(cd "$(dirname "$_cc_self")/.." && pwd -P)"

# shellcheck source=lib/common.sh
. "$CC_ROOT/lib/common.sh"

CC_HARNESS_ROLES="copilotcockpit-dev e2e-cockpit e2e-operator setup-e2e-cockpit setup-e2e-runbook worker-dev worker-fix worker-test"
CC_TARGET="${CC_TARGET:-user}" # or repo

usage() {
	cat <<'EOF'
Usage: bootstrap.sh codex-global [--link] [--dry-run]
       bootstrap.sh codex-repo [--link] [--dry-run]

Install or link the managed skills for the Codex runtime:
  codex-global: ~/.agents/skills/<role>/SKILL.md
  codex-repo:   .agents/skills/<role>/SKILL.md

Options:
  --link      Symlink each skill instead of copying.
  --dry-run   Describe every action; write nothing.
  -h, --help  Show this help and exit.
EOF
}

# cc_link_relative <src-relative> <dst> — symlink dst -> src-relative.
# This helper writes `src_relative` exactly as a relative string so links remain
# valid when the workspace root moves.
cc_link_relative() {
	local src_rel="$1" dst="$2"
	local dst_dir abs_src

	if [[ -z "$src_rel" || -z "$dst" ]]; then
		log_error "cc_link_relative: usage: cc_link_relative <src-relative> <dst>"
		return 2
	fi
	abs_src="$CC_ROOT/$src_rel"
	if [[ ! -f "$abs_src" ]]; then
		log_error "cc_link_relative: source not found: $abs_src"
		return 1
	fi

	if [[ -L "$dst" ]] && [[ "$(cc_realpath "$dst" 2>/dev/null)" == "$abs_src" ]]; then
		log_ok "already current (symlink): $dst"
		return 0
	fi

	if [[ "${DRY_RUN:-0}" != "0" ]]; then
		if [[ -e "$dst" || -L "$dst" ]]; then
			log_info "would link $src_rel -> $dst (existing path would be replaced)"
		else
			log_info "would link $src_rel -> $dst"
		fi
		return 0
	fi

	dst_dir="$(dirname "$dst")"
	cc_install_file_mkdir "$dst_dir" || return 1
	if [[ -f "$dst" && ! -L "$dst" ]]; then
		local backup
		backup="${dst}.bak-$(cc_timestamp)"
		if ! cp -p "$dst" "$backup"; then
			log_error "cc_link_relative: failed to back up $dst -> $backup"
			return 1
		fi
		log_info "backed up $dst -> $backup"
	fi
	if ! ln -sfn "$src_rel" "$dst"; then
		log_error "cc_link_relative: failed to symlink $src_rel -> $dst"
		return 1
	fi
	log_ok "linked $dst -> $src_rel"
	return 0
}

cc_place_user_skill() {
	local src="$1" dst="$2" mode="$3"
	if [[ "$mode" == "link" ]]; then
		cc_link_file "$src" "$dst"
	else
		cc_install_file "$src" "$dst"
	fi
}

cc_place_repo_skill() {
	local src_abs="$1" src_rel="$2" dst="$3" mode="$4"
	if [[ "$mode" == "link" ]]; then
		cc_link_relative "$src_rel" "$dst"
	else
		cc_install_file "$src_abs" "$dst"
	fi
}

main() {
	local mode="copy"
	local expect_files=0
	_CC_LINK_MODE=0

	while [[ $# -gt 0 ]]; do
		case "$1" in
		--link)
			_CC_LINK_MODE=1
			shift
			;;
		--dry-run)
			DRY_RUN=1
			shift
			;;
		-h | --help)
			usage
			return 0
			;;
		--*)
			log_error "codex: unknown option: $1"
			usage >&2
			return 2
			;;
		*)
			log_error "codex: unexpected argument: $1"
			usage >&2
			return 2
			;;
		esac
	done
	export DRY_RUN

	case "$CC_TARGET" in
	user | repo) ;;
	*)
		log_error "codex: internal bad target: $CC_TARGET"
		return 2
		;;
	esac

	if [[ "$_CC_LINK_MODE" -eq 1 ]]; then
		mode="link"
	fi

	local skills_root="$CC_ROOT/skills"
	if [[ ! -d "$skills_root" ]]; then
		log_error "codex: skills source root missing: $skills_root"
		return 1
	fi

	for role in $CC_HARNESS_ROLES; do
		if [[ ! -f "$skills_root/$role/SKILL.md" ]]; then
			log_error "required skill source missing: $skills_root/$role/SKILL.md"
			expect_files=1
		fi
	done

	if [[ "$CC_TARGET" == "repo" ]]; then
		if [[ ! -f "$CC_ROOT/AGENTS.md" ]]; then
			log_error "codex-repo: required file missing: $CC_ROOT/AGENTS.md"
			expect_files=1
		fi
		if [[ ! -f "$CC_ROOT/.codex/config.toml" ]]; then
			log_error "codex-repo: required file missing: $CC_ROOT/.codex/config.toml"
			expect_files=1
		fi
	fi
	if [[ "$expect_files" -ne 0 ]]; then
		log_error "aborting: required source(s) missing — nothing was installed"
		return 1
	fi

	local role src dst
	local skills_root_abs skill_rel
	skills_root_abs="$skills_root"
	log_info "codex ${CC_TARGET}: mode: $mode, dry-run: ${DRY_RUN}"

	case "$CC_TARGET" in
	user)
		local home_root="$HOME/.agents/skills"
		for role in $CC_HARNESS_ROLES; do
			src="$skills_root_abs/$role/SKILL.md"
			dst="$home_root/$role/SKILL.md"
			cc_place_user_skill "$src" "$dst" "$mode" || return 1
		done
		;;
	repo)
		local repo_root="$CC_ROOT/.agents/skills"
		for role in $CC_HARNESS_ROLES; do
			src="$skills_root_abs/$role/SKILL.md"
			skill_rel="../../../skills/$role/SKILL.md"
			dst="$repo_root/$role/SKILL.md"
			cc_place_repo_skill "$src" "$skill_rel" "$dst" "$mode" || return 1
		done
		;;
	esac

	log_ok "codex ${CC_TARGET} install complete (mode: $mode)"
	return 0
}

main "$@"
