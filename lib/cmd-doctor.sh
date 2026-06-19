#!/usr/bin/env bash
# lib/cmd-doctor.sh — `bootstrap.sh doctor`: verify prerequisites and report drift.
#
# Reports, in human-readable grouped sections (AC6):
#   * Prerequisites — each tool found/missing with a resolved version (AC2).
#   * PATH          — whether ~/.local/bin is on PATH, with exact remediation (AC3).
#   * Skills        — install state / drift for each of the 8 skills (AC4).
#   * cockpit-wake  — install state / drift for the bin/cockpit-wake artefact (AC4).
#   * cockpit-overseer — install state / drift for the bin/cockpit-overseer artefact (AC4).
#   * cockpit-trace    — install state / drift for the bin/cockpit-trace artefact (AC4).
#
# Exit code (AC5): 0 when every HARD prerequisite (bash, git, node, python3) is
# present; non-zero ONLY when a hard prerequisite is missing. Missing optional
# deps (at, cron, docker, tmux, bats) are warnings, never failures.
#
# Portability (TH1-E6-US1): no `readlink -f`, no `date -d`, no `sed -i`; bash 3.2
# features only; version probes are guarded `<tool> --version` style with fallbacks.
set -euo pipefail

# --- Resolve own directory portably and source shared helpers ----------------
# This file may be exec'd directly by bootstrap.sh (exec replaces the process,
# so the parent's sourced common.sh is gone): re-source it here.
_cc_self="$0"
if command -v python3 >/dev/null 2>&1; then
	_cc_self="$(python3 -c 'import os,sys; print(os.path.realpath(sys.argv[1]))' "$0")"
fi
CC_ROOT="$(cd "$(dirname "$_cc_self")/.." && pwd -P)"

# shellcheck source=lib/common.sh
. "$CC_ROOT/lib/common.sh"

# --- output helpers (stdout: human-readable report) --------------------------
_section() { printf '\n== %s ==\n' "$1"; }
_row() { printf '  %-16s %s\n' "$1" "$2"; }

# Track whether any HARD prerequisite is missing (drives the exit code, AC5).
_HARD_MISSING=0

# cc_probe_version <tool> — best-effort, portable version string (single line).
# Returns empty when no version can be resolved (caller copes).
cc_probe_version() {
	local tool="$1" out=""
	case "$tool" in
	bash)
		# Prefer the running shell's version variable (no subprocess needed).
		out="${BASH_VERSION:-}"
		[[ -z "$out" ]] && out="$(bash --version 2>/dev/null | head -1 || true)"
		;;
	tmux)
		out="$(tmux -V 2>/dev/null | head -1 || true)"
		;;
	at | cron | cronie | crond | atd)
		# `at`/`cron` have no portable --version flag; report presence only.
		out=""
		;;
	*)
		out="$(command "$tool" --version 2>/dev/null | head -1 || true)"
		;;
	esac
	printf '%s' "$out"
}

# cc_check_tool <command> <label> <hard|optional> [alt-command ...]
# Prints a found/missing row. For HARD tools, a miss flips _HARD_MISSING.
cc_check_tool() {
	local cmd="$1" label="$2" kind="$3"
	shift 3
	local found_cmd=""
	if command -v "$cmd" >/dev/null 2>&1; then
		found_cmd="$cmd"
	else
		# Try alternative command names (e.g. crontab for cron).
		local alt
		for alt in "$@"; do
			if command -v "$alt" >/dev/null 2>&1; then
				found_cmd="$alt"
				break
			fi
		done
	fi

	if [[ -n "$found_cmd" ]]; then
		local ver
		ver="$(cc_probe_version "$found_cmd")"
		if [[ -n "$ver" ]]; then
			_row "$label" "found    ($ver)"
		else
			_row "$label" "found"
		fi
		return 0
	fi

	# Not found.
	if [[ "$kind" == "hard" ]]; then
		_row "$label" "MISSING  (required)"
		_HARD_MISSING=1
	else
		_row "$label" "missing  (optional)"
	fi
	return 0
}

# cc_drift_state <src> <installed> — echo one of:
#   "source missing"  — repo source not present (vendored later, AC4 note)
#   "not installed"   — source exists but nothing installed in home
#   "installed & current" — both present and byte-identical
#   "drifted"         — both present but differ
cc_drift_state() {
	local src="$1" installed="$2"
	if [[ ! -f "$src" ]]; then
		printf 'source missing'
		return 0
	fi
	if [[ ! -f "$installed" ]]; then
		printf 'not installed'
		return 0
	fi
	if cc_files_identical "$src" "$installed"; then
		printf 'installed & current'
	else
		printf 'drifted'
	fi
}

main() {
	local home_bin="$HOME/.local/bin"

	printf 'copilotcockpit doctor — %s\n' "$CC_ROOT"

	# --- Prerequisites (AC2) -------------------------------------------------
	_section "Prerequisites"
	cc_check_tool bash bash hard
	cc_check_tool git git hard
	cc_check_tool node node hard
	cc_check_tool npm npm optional
	cc_check_tool python3 python3 hard
	cc_check_tool docker docker optional
	cc_check_tool tmux tmux optional
	# cockpit-wake runtime deps (ADR-005). `cron` may surface as crontab/crond/cron.
	cc_check_tool at at optional
	cc_check_tool cron cron optional crontab crond cronie
	# Contributor-only test runner.
	cc_check_tool bats bats optional

	# --- PATH (AC3) ----------------------------------------------------------
	_section "PATH"
	case ":$PATH:" in
	*":$home_bin:"*)
		_row "$home_bin" "on PATH"
		;;
	*)
		_row "$home_bin" "NOT on PATH"
		printf '  remediation: add it to your shell rc, e.g.\n'
		printf '    export PATH="%s:$PATH"\n' "$home_bin"
		;;
	esac

	# --- Skills (AC4) --------------------------------------------------------
	_section "Skills"
	local skills_root="$CC_ROOT/skills"
	local installed_root="$HOME/.copilot/skills"
	if [[ ! -d "$skills_root" ]]; then
		printf '  (repo skills/ source absent — vendored in TH1-E2; reporting from home)\n'
	fi
	local role state
	for role in \
		e2e-cockpit e2e-operator setup-e2e-cockpit setup-e2e-runbook \
		worker-dev worker-fix worker-test copilotcockpit-dev; do
		state="$(cc_drift_state "$skills_root/$role/SKILL.md" "$installed_root/$role/SKILL.md")"
		_row "$role" "$state"
	done

	# --- cockpit-wake (AC4) --------------------------------------------------
	_section "cockpit-wake"
	state="$(cc_drift_state "$CC_ROOT/bin/cockpit-wake" "$home_bin/cockpit-wake")"
	_row "cockpit-wake" "$state"

	# --- cockpit-overseer (AC4) ----------------------------------------------
	_section "cockpit-overseer"
	state="$(cc_drift_state "$CC_ROOT/bin/cockpit-overseer" "$home_bin/cockpit-overseer")"
	_row "cockpit-overseer" "$state"

	# --- cockpit-trace (AC4) -------------------------------------------------
	_section "cockpit-trace"
	state="$(cc_drift_state "$CC_ROOT/bin/cockpit-trace" "$home_bin/cockpit-trace")"
	_row "cockpit-trace" "$state"

	# --- Verdict (AC5) -------------------------------------------------------
	_section "Result"
	if [[ "$_HARD_MISSING" -ne 0 ]]; then
		_row "status" "FAIL — a required prerequisite is missing"
		return 1
	fi
	_row "status" "OK — all required prerequisites present"
	return 0
}

main "$@"
