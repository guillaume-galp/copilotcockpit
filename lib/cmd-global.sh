#!/usr/bin/env bash
# lib/cmd-global.sh — `bootstrap.sh global`: install/update the 8 skills + cockpit-wake.
#
# Installs (and idempotently updates) the repo's managed artefacts into the
# user's home so one command makes a machine able to operate a cockpit
# (VP1 §5a, ADR-001, ADR-005, NFR-1, NFR-4):
#
#   * skills/<role>/SKILL.md -> ~/.copilot/skills/<role>/SKILL.md   (8 roles)
#   * bin/cockpit-wake       -> ~/.local/bin/cockpit-wake (+x)
#
# Modes:
#   (default)   copy with backup-before-overwrite (cc_install_file)
#   --link      symlink each artefact back to the repo (ADR-001, dev authoring)
#   --dry-run   describe every action, change nothing
#
# Source-of-truth: the repo (ADR-001/ADR-005). This command is the install AND
# the upgrade path. It NEVER touches unrelated skills already in
# ~/.copilot/skills/ — it only operates on the enumerated managed roles (AC4).
#
# Portability (TH1-E6-US1): no `readlink -f`, no `date -d`, no `sed -i`; bash 3.2
# features only; absolute repo paths resolved via cc_realpath.
set -euo pipefail

# --- Resolve own directory portably and source shared helpers ----------------
# This file is exec'd directly by bootstrap.sh (exec replaces the process, so the
# parent's sourced common.sh is gone): re-source it here.
_cc_self="$0"
if command -v python3 >/dev/null 2>&1; then
	_cc_self="$(python3 -c 'import os,sys; print(os.path.realpath(sys.argv[1]))' "$0")"
fi
CC_ROOT="$(cd "$(dirname "$_cc_self")/.." && pwd -P)"

# shellcheck source=lib/common.sh
. "$CC_ROOT/lib/common.sh"

# --- Managed role set (ADR-001 + ADR-008) ------------------------------------
# The 7 harness skills are REQUIRED sources; copilotcockpit-dev (8th, ADR-008) is
# a KNOWN-PENDING source delivered by TH1-E5-US3. The loop enumerates all 8 so the
# 8th installs automatically once its source lands — but its current absence is a
# warning, not a fatal error (AC2). A missing HARNESS source is fatal (AC9).
CC_HARNESS_ROLES="e2e-cockpit e2e-operator setup-e2e-cockpit setup-e2e-runbook worker-dev worker-fix worker-test"
CC_PENDING_ROLES="copilotcockpit-dev"
CC_ALL_ROLES="$CC_HARNESS_ROLES $CC_PENDING_ROLES"

usage() {
	cat <<'EOF'
Usage: bootstrap.sh global [--link] [--dry-run]

Install/update the managed skills and cockpit-wake into your home:
  ~/.copilot/skills/<role>/SKILL.md   (8 managed roles)
  ~/.local/bin/cockpit-wake           (+x)

Options:
  --link        Symlink each artefact back to the repo instead of copying.
  --dry-run     Describe every action; change nothing.
  -h, --help    Show this help and exit.
EOF
}

# --- cc_link_file <src> <dst> ------------------------------------------------
# Symlink dst -> absolute(src) (AC6). Mirrors cc_install_file semantics:
#   * dst already a symlink to the right target -> "already current", no change
#   * differing existing REGULAR file           -> backed up, then replaced
#   * DRY_RUN=1                                  -> "would link ...", no change
cc_link_file() {
	local src="$1" dst="$2"

	if [[ -z "$src" || -z "$dst" ]]; then
		log_error "cc_link_file: usage: cc_link_file <src> <dst>"
		return 2
	fi
	if [[ ! -f "$src" ]]; then
		log_error "cc_link_file: source not found: $src"
		return 1
	fi

	local abs
	abs="$(cc_realpath "$src")" || {
		log_error "cc_link_file: cannot resolve absolute path of $src"
		return 1
	}

	# Idempotency: already the correct symlink (AC8).
	if [[ -L "$dst" ]] && [[ "$(cc_realpath "$dst" 2>/dev/null)" == "$abs" ]]; then
		log_ok "already current (symlink): $dst"
		return 0
	fi

	# Dry-run: describe, touch nothing (AC7).
	if [[ "${DRY_RUN:-0}" != "0" ]]; then
		if [[ -e "$dst" || -L "$dst" ]]; then
			log_info "would link $abs -> $dst (existing path would be replaced)"
		else
			log_info "would link $abs -> $dst"
		fi
		return 0
	fi

	cc_install_file_mkdir "$(dirname "$dst")" || return 1

	# Back up a differing, existing REAL file before replacing it (no data loss).
	if [[ -f "$dst" && ! -L "$dst" ]]; then
		local backup
		backup="${dst}.bak-$(cc_timestamp)"
		if ! cp -p "$dst" "$backup"; then
			log_error "cc_link_file: failed to back up $dst -> $backup"
			return 1
		fi
		log_info "backed up $dst -> $backup"
	fi

	# `ln -sfn` is portable (GNU & BSD) and atomically replaces an existing link.
	if ! ln -sfn "$abs" "$dst"; then
		log_error "cc_link_file: failed to symlink $abs -> $dst"
		return 1
	fi
	log_ok "linked $dst -> $abs"
	return 0
}

# cc_place <src> <dst> — copy or symlink per the active mode.
cc_place() {
	if [[ "${_CC_LINK_MODE:-0}" -eq 1 ]]; then
		cc_link_file "$1" "$2"
	else
		cc_install_file "$1" "$2"
	fi
}

main() {
	_CC_LINK_MODE=0

	# --- Parse options (AC1/AC6/AC7) -----------------------------------------
	while [[ $# -gt 0 ]]; do
		case "$1" in
		--link)
			_CC_LINK_MODE=1
			shift
			;;
		--dry-run)
			# bootstrap.sh already sets DRY_RUN; honour it here too for robustness.
			DRY_RUN=1
			shift
			;;
		-h | --help)
			usage
			return 0
			;;
		*)
			log_error "global: unknown option: $1"
			usage >&2
			return 2
			;;
		esac
	done
	export DRY_RUN

	local skills_root="$CC_ROOT/skills"
	local cw_src="$CC_ROOT/bin/cockpit-wake"
	local skills_dst_root="$HOME/.copilot/skills"
	local home_bin="$HOME/.local/bin"
	local cw_dst="$home_bin/cockpit-wake"

	# --- Preflight: all REQUIRED sources must exist BEFORE any write (AC9) ----
	# Validate up front so a missing required source never leaves a partial,
	# silent install. copilotcockpit-dev (pending) is exempt by design (AC2).
	local missing=0 role
	for role in $CC_HARNESS_ROLES; do
		if [[ ! -f "$skills_root/$role/SKILL.md" ]]; then
			log_error "required skill source missing: $skills_root/$role/SKILL.md"
			missing=1
		fi
	done
	if [[ ! -f "$cw_src" ]]; then
		log_error "required source missing: $cw_src"
		missing=1
	fi
	if [[ "$missing" -ne 0 ]]; then
		log_error "aborting: required source(s) missing — nothing was installed"
		return 1
	fi

	local mode_label="copy"
	[[ "$_CC_LINK_MODE" -eq 1 ]] && mode_label="link"
	log_info "global install (mode: $mode_label, dry-run: ${DRY_RUN})"

	# --- Install the 8 managed skills (AC2/AC4) ------------------------------
	for role in $CC_ALL_ROLES; do
		local src="$skills_root/$role/SKILL.md"
		local dst="$skills_dst_root/$role/SKILL.md"
		if [[ ! -f "$src" ]]; then
			# Only pending roles reach here (harness verified in preflight).
			log_warn "skill source not yet vendored, skipping: $role (expected via TH1-E5-US3)"
			continue
		fi
		cc_place "$src" "$dst" || return 1
	done

	# --- Install cockpit-wake in the same idempotent pass (AC3) --------------
	cc_place "$cw_src" "$cw_dst" || return 1
	if [[ "$_CC_LINK_MODE" -ne 1 ]]; then
		# Copy mode: ensure the installed copy is executable (cc_run honours dry-run).
		cc_run chmod +x "$cw_dst" || return 1
	fi

	# --- PATH guidance (AC5): advise, never edit dotfiles --------------------
	case ":$PATH:" in
	*":$home_bin:"*)
		: # already on PATH
		;;
	*)
		log_warn "$home_bin is not on your PATH — cockpit-wake will not be found"
		printf '\nAdd it to your current shell:\n'
		printf '\n  export PATH="%s:$PATH"\n' "$home_bin"
		printf '\nTo persist, append that line to your shell rc, for example:\n'
		printf '  echo '\''export PATH="%s:$PATH"'\'' >> ~/.bashrc   # or ~/.zshrc\n\n' "$home_bin"
		;;
	esac

	log_ok "global install complete (mode: $mode_label)"
	return 0
}

main "$@"
