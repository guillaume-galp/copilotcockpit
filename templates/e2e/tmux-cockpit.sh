#!/usr/bin/env bash
# tmux-cockpit.sh — Remote E2E cockpit builder (PLACEHOLDER).
#
# This is a runnable placeholder scaffolded by copilotcockpit. It builds a minimal
# tmux workspace skeleton so the cockpit is usable immediately, and is REGENERATED
# in full by the /setup-e2e-cockpit skill once your app's topology is discovered
# (k8s context, namespace, backend service, pod labels, log sources).
#
# Windows it builds (generic skeleton):
#   overseer    current window — orchestrator
#   logs        placeholder log pane (replace with real backend/service log tails)
#   workers     placeholder window for worker-test / worker-dev / worker-fix
#
# Usage:
#   ./tmux-cockpit.sh            # reuse current $TMUX session or create one named $APP_NAME
#   ./tmux-cockpit.sh myname     # explicit session name
#   ./tmux-cockpit.sh --help     # show this help
#
# Requirements: tmux. (kubectl / docker added by /setup-e2e-cockpit when relevant.)
#
# Portable shell: targets bash 3.2 (macOS system bash). No GNU-only flags, no
# associative arrays, no mapfile. Uses printf, not echo -e/-n.

set -euo pipefail

print_usage() {
  sed -n '2,20p' "$0" | sed -E 's/^# ?//'
}

case "${1:-}" in
  -h|--help)
    print_usage
    exit 0
    ;;
esac

# ── CONFIGURE ─────────────────────────────────────────────────────────────────
# Fill these in for your app. The /setup-e2e-cockpit skill will do this for you
# automatically — run it with:  /setup-e2e-cockpit
#
# Defaults are deterministic Tier-1 values (ADR-004); override via env vars.

APP_NAME="${APP_NAME:-app}"               # tmux session name and worker prompts
BACKEND_PORT="${BACKEND_PORT:-8000}"      # backend / health port
FRONTEND_PORT="${FRONTEND_PORT:-5173}"    # frontend dev-server port
HEALTH_PATH="${HEALTH_PATH:-/health}"     # backend health endpoint path

# ── CONFIGURE (topology — filled in by /setup-e2e-cockpit) ────────────────────
# K8S_CONTEXT=""        # e.g. arn:aws:eks:...:cluster/...
# NAMESPACE=""          # k8s namespace
# BACKEND_SVC=""        # port-forward target service
# BACKEND_LABEL=""      # pod selector for log tails, e.g. app=backend
# CHROMIUM_PORT="9222"  # CDP port if a browser container is used
# ──────────────────────────────────────────────────────────────────────────────

# ── Session ───────────────────────────────────────────────────────────────────
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"

if [ -n "${TMUX:-}" ]; then
  SESSION="$(tmux display-message -p '#S')"
else
  SESSION="${1:-$APP_NAME}"
  if ! tmux has-session -t "$SESSION" 2>/dev/null; then
    printf "Creating tmux session '%s'...\n" "$SESSION"
    tmux new-session -d -s "$SESSION" -n overseer -c "$PROJECT_DIR"
  fi
fi

# ── Helpers ───────────────────────────────────────────────────────────────────
window_exists() {
  tmux list-windows -t "$SESSION" -F '#{window_name}' 2>/dev/null | grep -qx "$1"
}

make_window() {
  name="$1"
  if window_exists "$name"; then
    printf "  [skip] window '%s' already exists\n" "$name"
    return 0
  fi
  tmux new-window -t "${SESSION}:" -n "$name" -c "$PROJECT_DIR"
  printf "  [done] window '%s'\n" "$name"
}

# ── Window: overseer ──────────────────────────────────────────────────────────
if [ -n "${TMUX:-}" ]; then
  CURRENT_WIN="$(tmux display-message -p '#W')"
  if [ "$CURRENT_WIN" != "overseer" ]; then
    tmux rename-window "overseer"
  fi
fi

# ── Window: logs (PLACEHOLDER) ────────────────────────────────────────────────
# Replace this with real backend/service log tails via /setup-e2e-cockpit.
if ! window_exists "logs"; then
  make_window "logs"
  tmux send-keys -t "${SESSION}:logs" \
    "printf '[logs placeholder] configure real log tails via /setup-e2e-cockpit\n'" Enter
fi

# ── Window: workers (PLACEHOLDER) ─────────────────────────────────────────────
# /setup-e2e-cockpit splits this into worker-test / worker-dev / worker-fix panes
# and primes each with its global skill + repo overlay.
make_window "workers"

# ── Summary ───────────────────────────────────────────────────────────────────
printf "\n"
printf "==================================================================\n"
printf "  %s cockpit (placeholder) ready\n" "$APP_NAME"
printf "------------------------------------------------------------------\n"
tmux list-windows -t "$SESSION" -F '  #{window_index}  #{window_name}'
printf "------------------------------------------------------------------\n"
printf "  Backend health: curl -sk http://localhost:%s%s\n" "$BACKEND_PORT" "$HEALTH_PATH"
printf "  Run (governed): ./run-audit.sh --scope \"@smoke\"\n"
printf "  Next step:      run /setup-e2e-cockpit to complete the topology\n"
printf "==================================================================\n"
