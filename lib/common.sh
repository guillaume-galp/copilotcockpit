#!/usr/bin/env bash
# lib/common.sh — shared helpers for copilotcockpit bootstrap subcommands.
#
# Sourceable library of cross-cutting concerns (ADR-003): logging, OS detection,
# portable shims, backup-before-overwrite installs, and --dry-run support. Every
# lib/cmd-*.sh inherits this so the behaviour is implemented exactly once
# (NFR-1 idempotency, NFR-2 cross-platform).
#
# Portability follows TH1-E6-US1 (portability-cheatsheet.md):
#   - never `sed -i`, `readlink -f`, `date -d`; route divergent ops via python3
#     or temp-file + mv.
#   - use printf, not echo -e/-n.
#   - target bash 3.2 (macOS system bash): no associative arrays, no ${var^^},
#     no mapfile.
#
# Usage:  source /path/to/lib/common.sh
#
# This file deliberately does NOT enable `set -euo pipefail`: it is sourced into
# callers that own their own shell options. Helpers are written to be safe under
# `set -euo pipefail`.

# --- AC6: idempotency guard --------------------------------------------------
# Re-sourcing in the same shell must be a safe no-op (no redefinition errors).
[[ -n "${_CC_COMMON_SH:-}" ]] && return 0
_CC_COMMON_SH=1

# --- DRY_RUN flag (AC5) ------------------------------------------------------
# Callers set DRY_RUN=1 when they parse `--dry-run`. Default off.
: "${DRY_RUN:=0}"

# --- Logging helpers (AC2) ---------------------------------------------------
# All log_* helpers write to STDERR with a consistent "cc:" prefix so that
# stdout stays reserved for machine-readable command output.
_CC_PREFIX="cc:"

log_info() { printf '%s [info]  %s\n' "$_CC_PREFIX" "$*" >&2; }
log_warn() { printf '%s [warn]  %s\n' "$_CC_PREFIX" "$*" >&2; }
log_error() { printf '%s [error] %s\n' "$_CC_PREFIX" "$*" >&2; }
log_ok() { printf '%s [ok]    %s\n' "$_CC_PREFIX" "$*" >&2; }

# --- OS detection (AC3) ------------------------------------------------------
# Returns "linux" or "macos". Uses `uname -s` only (POSIX, no GNU-only flags).
detect_os() {
	local kernel
	kernel=$(uname -s 2>/dev/null || printf 'unknown')
	case "$kernel" in
	Linux*) printf 'linux\n' ;;
	Darwin*) printf 'macos\n' ;;
	*)
		log_warn "unrecognised kernel '$kernel'; defaulting to linux"
		printf 'linux\n'
		;;
	esac
}

# --- Portable shims (AC3) ----------------------------------------------------

# cc_timestamp — a portable, sortable UTC timestamp safe for filenames.
# `date -u +FORMAT` (with no parsing/arithmetic) is portable across GNU & BSD.
# We avoid colons so the value is filename-safe for backups (<dst>.bak-<ts>).
cc_timestamp() {
	date -u +%Y%m%dT%H%M%SZ
}

# cc_realpath <path> — canonical absolute path WITHOUT `readlink -f`
# (absent on BSD/macOS). Prefer python3; fall back to a pure-POSIX cd/pwd -P.
cc_realpath() {
	local target="$1"
	if command -v python3 >/dev/null 2>&1; then
		python3 -c 'import os,sys; print(os.path.realpath(sys.argv[1]))' "$target"
		return $?
	fi
	# Pure-shell fallback (no python3). Resolves directory via `pwd -P`.
	if [[ -d "$target" ]]; then
		(cd "$target" 2>/dev/null && pwd -P)
	else
		local dir base
		dir=$(dirname "$target")
		base=$(basename "$target")
		dir=$(cd "$dir" 2>/dev/null && pwd -P) || return 1
		if [[ "$base" == "/" ]]; then
			printf '%s\n' "$dir"
		else
			printf '%s/%s\n' "$dir" "$base"
		fi
	fi
}

# cc_files_identical <a> <b> — 0 if both exist and bytes match, else non-zero.
cc_files_identical() {
	[[ -f "$1" && -f "$2" ]] && cmp -s "$1" "$2"
}

# --- cc_sha256_file <file> — print the hex sha256 of <file>, portable. --------
# Linux ships `sha256sum`; macOS/BSD ships `shasum -a 256` (and sometimes both).
# Detect whichever is available (portability-cheatsheet.md doctrine: never assume
# a single tool name). Prints ONLY the 64-char hash, nothing else.
cc_sha256_file() {
	local f="$1"
	if [[ ! -f "$f" ]]; then
		log_error "cc_sha256_file: not a file: $f"
		return 1
	fi
	if command -v sha256sum >/dev/null 2>&1; then
		sha256sum "$f" | awk '{print $1; exit}'
	elif command -v shasum >/dev/null 2>&1; then
		shasum -a 256 "$f" | awk '{print $1; exit}'
	else
		log_error "cc_sha256_file: no sha256 tool found (need sha256sum or shasum)"
		return 1
	fi
}

# --- cc_sha256_verify <file> <expected-hex> — 0 if match, else error+non-zero -
# The expected hash is the first field of a `.sha256` companion file. On
# mismatch this logs both hashes and returns non-zero so callers can abort
# (ADR-007 integrity; tamper-evident cold install).
cc_sha256_verify() {
	local f="$1" expected="$2" actual
	if [[ -z "$expected" ]]; then
		log_error "cc_sha256_verify: empty expected checksum for $f"
		return 1
	fi
	actual="$(cc_sha256_file "$f")" || return 1
	if [[ "$actual" != "$expected" ]]; then
		log_error "checksum mismatch for $f"
		log_error "  expected: $expected"
		log_error "  actual:   $actual"
		return 1
	fi
	return 0
}

# --- cc_run: dry-run-aware command runner (AC5) ------------------------------
# Prints the command (when DRY_RUN=1) instead of executing it. When executing,
# the command is run as given. Arguments are passed through verbatim.
cc_run() {
	if [[ "${DRY_RUN:-0}" != "0" ]]; then
		log_info "would run: $*"
		return 0
	fi
	"$@"
}

# --- cc_install_file: idempotent copy with backup (AC4) ----------------------
# cc_install_file <src> <dst>
#   * identical dst         -> log "already current", exit 0, no backup, no write
#   * differing existing dst-> back up to <dst>.bak-<ts>, then overwrite
#   * missing dst           -> create parent dir, copy
#   * DRY_RUN=1             -> print "would copy ..." guidance, change nothing
cc_install_file() {
	local src="$1" dst="$2"

	if [[ -z "$src" || -z "$dst" ]]; then
		log_error "cc_install_file: usage: cc_install_file <src> <dst>"
		return 2
	fi
	if [[ ! -f "$src" ]]; then
		log_error "cc_install_file: source not found: $src"
		return 1
	fi

	# Identical-file no-op (AC4 / BDD scenario 1).
	if cc_files_identical "$src" "$dst"; then
		log_ok "already current: $dst"
		return 0
	fi

	local dst_dir
	dst_dir=$(dirname "$dst")

	# Dry-run: describe intended action, touch nothing (AC4 / BDD scenario 3).
	if [[ "${DRY_RUN:-0}" != "0" ]]; then
		if [[ -e "$dst" ]]; then
			log_info "would copy $src -> $dst (existing file would be backed up first)"
		else
			log_info "would copy $src -> $dst"
		fi
		return 0
	fi

	cc_install_file_mkdir "$dst_dir" || return 1

	# Back up a differing, existing destination before overwriting
	# (AC4 / BDD scenario 2).
	if [[ -e "$dst" ]]; then
		local backup
		backup="${dst}.bak-$(cc_timestamp)"
		if ! cp -p "$dst" "$backup"; then
			log_error "cc_install_file: failed to back up $dst -> $backup"
			return 1
		fi
		log_info "backed up $dst -> $backup"
	fi

	if ! cp "$src" "$dst"; then
		log_error "cc_install_file: failed to copy $src -> $dst"
		return 1
	fi
	log_ok "installed $dst"
	return 0
}

# Internal: ensure a directory exists (split out to keep cc_install_file lean).
cc_install_file_mkdir() {
	local dir="$1"
	[[ -d "$dir" ]] && return 0
	if ! mkdir -p "$dir"; then
		log_error "cc_install_file: failed to create directory $dir"
		return 1
	fi
	return 0
}
