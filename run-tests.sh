#!/usr/bin/env bash
# run-tests.sh — copilotcockpit developer test dispatcher (ADR-008, architecture §9).
#
# Usage: ./run-tests.sh <unit|template|skills|integration|all>
#
# Categories (architecture §9):
#   1 unit        — bats per-command unit tests   tests/unit/*.bats
#   2 template    — template integrity            tests/template/check-template.sh
#   3 skills      — SKILL.md lint                  tests/skills/lint-skills.sh
#   4 integration — dry-run smoke (bats)           tests/integration/smoke.bats
#
# Only Category 1 (unit) exists today; categories 2-4 arrive in TH1-E5-US2. The
# dispatcher is designed so those suites *auto-wire* the moment their files land:
# each category checks for its file(s) and runs them if present, otherwise prints
# a clear "not yet implemented / skipped" notice WITHOUT falsely passing.
#
# Contributor-only: requires `bats` for unit/integration. `--help` never hard-fails
# even when bats is absent. Portability (TH1-E6-US1): bash 3.2-safe, no GNU-only
# flags, no associative arrays / mapfile.
set -euo pipefail

# --- Resolve own directory portably (no readlink -f) -------------------------
_rt_self="$0"
if command -v python3 >/dev/null 2>&1; then
	_rt_self="$(python3 -c 'import os,sys; print(os.path.realpath(sys.argv[1]))' "$0")"
fi
ROOT="$(cd "$(dirname "$_rt_self")" && pwd -P)"

UNIT_DIR="$ROOT/tests/unit"
TEMPLATE_SCRIPT="$ROOT/tests/template/check-template.sh"
SKILLS_SCRIPT="$ROOT/tests/skills/lint-skills.sh"
INTEGRATION_SUITE="$ROOT/tests/integration/smoke.bats"

usage() {
	cat <<'EOF'
Usage: ./run-tests.sh <unit|template|skills|integration|all>

Developer test categories (ADR-008, architecture §9):
  unit         Category 1 — bats unit tests for each lib/cmd-*.sh (tests/unit/)
  template     Category 2 — template integrity   (tests/template/check-template.sh)
  skills       Category 3 — SKILL.md lint         (tests/skills/lint-skills.sh)
  integration  Category 4 — dry-run smoke (bats)  (tests/integration/smoke.bats)
  all          Run every category that is present; skip (loudly) any not yet built.

Contributor-only: `bats` is required for the unit and integration categories.
Install it, then put it on PATH, e.g.:
  git clone --depth 1 https://github.com/bats-core/bats-core && \
    ./bats-core/install.sh "$HOME/.local"   # then: export PATH="$HOME/.local/bin:$PATH"
EOF
}

log() { printf '%s\n' "$*" >&2; }

# require_bats — return 0 if bats is on PATH; otherwise print actionable guidance
# and return 1. Callers decide whether a missing bats is a skip or a failure.
require_bats() {
	if command -v bats >/dev/null 2>&1; then
		return 0
	fi
	log "run-tests: 'bats' not found on PATH — cannot run this category."
	log "  Install it (contributor-only dependency), e.g.:"
	log "    git clone --depth 1 https://github.com/bats-core/bats-core"
	log "    ./bats-core/install.sh \"\$HOME/.local\""
	log "    export PATH=\"\$HOME/.local/bin:\$PATH\""
	log "  (or: npm install -g bats  /  apt-get install -y bats)"
	return 1
}

# run_unit — Category 1. Runs every tests/unit/*.bats. Fails if bats is absent.
run_unit() {
	require_bats || return 1
	# bash 3.2-safe glob expansion check (no nullglob reliance).
	local found=0 f
	for f in "$UNIT_DIR"/*.bats; do
		[[ -e "$f" ]] && found=1
	done
	if [[ "$found" -eq 0 ]]; then
		log "run-tests: no unit suites found in $UNIT_DIR"
		return 1
	fi
	log "== Category 1: unit (bats) =="
	bats "$UNIT_DIR"/*.bats
}

# run_template — Category 2. Auto-wires when tests/template/check-template.sh lands.
run_template() {
	if [[ ! -f "$TEMPLATE_SCRIPT" ]]; then
		log "== Category 2: template — SKIPPED (not yet implemented; arrives in TH1-E5-US2) =="
		return 0
	fi
	log "== Category 2: template integrity =="
	bash "$TEMPLATE_SCRIPT"
}

# run_skills — Category 3. Auto-wires when tests/skills/lint-skills.sh lands.
run_skills() {
	if [[ ! -f "$SKILLS_SCRIPT" ]]; then
		log "== Category 3: skills — SKIPPED (not yet implemented; arrives in TH1-E5-US2) =="
		return 0
	fi
	log "== Category 3: skills lint =="
	bash "$SKILLS_SCRIPT"
}

# run_integration — Category 4. Auto-wires when tests/integration/smoke.bats lands.
run_integration() {
	if [[ ! -f "$INTEGRATION_SUITE" ]]; then
		log "== Category 4: integration — SKIPPED (not yet implemented; arrives in TH1-E5-US2) =="
		return 0
	fi
	require_bats || return 1
	log "== Category 4: integration smoke (bats) =="
	bats "$INTEGRATION_SUITE"
}

main() {
	local category="${1:-}"
	case "$category" in
	"" | -h | --help)
		usage
		# --help must never hard-fail, even when bats is absent.
		[[ "$category" == "" ]] && exit 2
		exit 0
		;;
	unit) run_unit ;;
	template) run_template ;;
	skills) run_skills ;;
	integration) run_integration ;;
	all)
		# Run every category; a single failure makes the whole run non-zero, but
		# we run them all first so the developer sees every result in one pass.
		local rc=0
		run_unit || rc=1
		run_template || rc=1
		run_skills || rc=1
		run_integration || rc=1
		if [[ "$rc" -eq 0 ]]; then
			log "== all present categories passed =="
		else
			log "== one or more categories FAILED =="
		fi
		return "$rc"
		;;
	*)
		log "run-tests: unknown category: $category"
		usage >&2
		exit 2
		;;
	esac
}

main "$@"
