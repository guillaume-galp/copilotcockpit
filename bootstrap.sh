#!/usr/bin/env bash
# bootstrap.sh — single self-documenting entry point for copilotcockpit.
#
# A thin dispatcher (ADR-003): it resolves its own location, sources the shared
# lib/common.sh, then routes `$1` to the matching lib/cmd-<verb>.sh. All real
# work lives in the cmd-*.sh files; this file stays pure routing.
set -euo pipefail

# --- AC3: resolve own directory portably (relative / absolute / symlink) -----
# No `readlink -f` (absent on BSD/macOS). Prefer python3 realpath; fall back to
# a pure-POSIX cd/pwd -P which copes with relative and absolute invocation.
_cc_self="$0"
if command -v python3 >/dev/null 2>&1; then
	_cc_self="$(python3 -c 'import os,sys; print(os.path.realpath(sys.argv[1]))' "$0")"
fi
CC_ROOT="$(cd "$(dirname "$_cc_self")" && pwd -P)"

# shellcheck source=lib/common.sh
. "$CC_ROOT/lib/common.sh"

usage() {
	cat <<'EOF'
Usage: bootstrap.sh <command> [options]

Commands:
  global [--link] [--dry-run] [--from-release <ref>]
                          Install/update skills + cockpit-wake into your home.
  e2e <dir> [--update] [--no-git] [--yes] [--dry-run]
                          Scaffold or refresh an e2e/ sub-repo in <dir>.
  doctor                  Verify prerequisites and report install/drift state.

Options:
  -h, --help              Show this help and exit.
EOF
}

# --- AC6: a global --dry-run (before or after the verb) sets shared DRY_RUN ---
# Consume a leading global --dry-run so the verb is still found in $1, and also
# honour --dry-run passed after the verb.
[[ "${1:-}" == "--dry-run" ]] && { DRY_RUN=1; shift; }
for _arg in "$@"; do
	[[ "$_arg" == "--dry-run" ]] && DRY_RUN=1
done
export DRY_RUN

cmd="${1:-}"
case "$cmd" in
"" | -h | --help)
	usage
	exit 0
	;;
esac

shift # drop the verb; forward the rest to the subcommand
case "$cmd" in
global) exec "$CC_ROOT/lib/cmd-global.sh" "$@" ;;
e2e) exec "$CC_ROOT/lib/cmd-e2e.sh" "$@" ;;
doctor) exec "$CC_ROOT/lib/cmd-doctor.sh" "$@" ;;
*)
	log_error "unknown command: $cmd"
	usage >&2
	exit 2
	;;
esac
