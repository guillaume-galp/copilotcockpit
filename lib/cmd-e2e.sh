#!/usr/bin/env bash
# lib/cmd-e2e.sh — `bootstrap.sh e2e <dir>`: scaffold a runnable e2e/ sub-repo.
#
# Drops a complete, runnable end-to-end smoke harness into a target project by
# copying templates/e2e/, tokenising the Tier-1 placeholders, atomically staging
# the result, `git init`-ing it as an independent sub-repo, and running
# `npm install` for the Playwright deps (VP1 §5b/§6; ADR-002, ADR-004; NFR-1/7).
#
# Flow (architecture §5):
#   1. validate <dir> is a directory
#   2. if <dir>/e2e exists and not --update  → refuse, point at --update (NFR-1)
#   3. resolve Tier-1 tokens  (.e2e-config.yaml → prompt → defaults)   (ADR-004)
#   4. copy templates/e2e/ → staging   (verbatim + token-substituted *.tmpl)
#   5. atomically move staging → <dir>/e2e/                            (§7)
#   6. git init + initial commit                                       (ADR-002)
#   7. npm install (tolerant of offline/missing npm)
#   8. print the /setup-e2e-cockpit → /setup-e2e-runbook handoff
#
# Portability (TH1-E6-US1): no `sed -i`, `readlink -f`, `date -d`; bash 3.2-safe;
# substitution is temp-file + mv; canonical paths via cc_realpath.
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

CC_TEMPLATE_ROOT="$CC_ROOT/templates/e2e"

usage() {
	cat <<'EOF'
Usage: bootstrap.sh e2e <dir> [--update] [--no-git] [--yes] [--dry-run]

Scaffold a runnable e2e/ smoke-test sub-repo into <dir>:
  <dir>/e2e/   copied from templates/e2e/, Tier-1 tokens substituted, git-init'd.

Options:
  --update    Refresh an existing e2e/ (framework files only). See TH1-E3-US6.
  --no-git    Do not `git init` the scaffolded e2e/ (parent owns the files).
  --yes       Non-interactive: take .e2e-config.yaml or defaults, never prompt.
  --dry-run   Print the scaffold file list + resolved tokens; write nothing.
  -h, --help  Show this help and exit.

Tier-1 tokens (resolution: .e2e-config.yaml → prompt → default), ADR-004:
  @@APP_NAME@@       default: basename of <dir>
  @@BACKEND_PORT@@   default: 8000
  @@FRONTEND_PORT@@  default: 5173
  @@HEALTH_PATH@@    default: /health
EOF
}

# --- Token substitution helpers (ADR-004) ------------------------------------

# _e2e_sed_escape <value> — escape a value for safe use as a sed replacement.
# Escapes backslash FIRST, then the delimiter `/` and the `&` back-reference, so
# values containing slashes (e.g. /health) substitute literally.
_e2e_sed_escape() {
	printf '%s' "$1" | sed -e 's/\\/\\\\/g' -e 's/[&/]/\\&/g'
}

# _e2e_substitute <src> <dst> — copy <src> to <dst> with the 4 Tier-1 tokens
# substituted. Portable (no `sed -i`): write to a temp file, then mv into place.
# Substituting the fixed token set is a harmless no-op on files that contain no
# tokens, so this is safe to run on every text file in the template.
_e2e_substitute() {
	local src="$1" dst="$2" tmp
	tmp="$(mktemp "${TMPDIR:-/tmp}/cc-e2e-sub.XXXXXX")" || return 1
	if ! sed \
		-e "s/@@APP_NAME@@/$_E2E_APP_NAME_ESC/g" \
		-e "s/@@BACKEND_PORT@@/$_E2E_BACKEND_PORT_ESC/g" \
		-e "s/@@FRONTEND_PORT@@/$_E2E_FRONTEND_PORT_ESC/g" \
		-e "s/@@HEALTH_PATH@@/$_E2E_HEALTH_PATH_ESC/g" \
		"$src" >"$tmp"; then
		rm -f "$tmp"
		return 1
	fi
	# Preserve the source's executable bit (run-*.sh, tmux-*.sh).
	if [[ -x "$src" ]]; then chmod +x "$tmp"; fi
	mv "$tmp" "$dst"
}

# --- Config-file token resolution (.e2e-config.yaml) -------------------------

# _e2e_config_get <key> <file> — print the value for a simple `key: value` line
# in a flat YAML file. Strips an inline `# comment`, surrounding quotes and
# whitespace. Returns non-zero (empty) when the key is absent.
_e2e_config_get() {
	local key="$1" file="$2" val
	[[ -f "$file" ]] || return 1
	val="$(sed -nE "s/^[[:space:]]*${key}[[:space:]]*:[[:space:]]*(.*)$/\1/p" "$file" | head -n1)"
	[[ -n "$val" ]] || return 1
	# Strip a trailing inline comment ( value # comment ).
	val="$(printf '%s' "$val" | sed -E 's/[[:space:]]+#.*$//')"
	# Strip surrounding single/double quotes.
	val="$(printf '%s' "$val" | sed -E "s/^[\"']//; s/[\"']$//")"
	# Strip trailing whitespace.
	val="$(printf '%s' "$val" | sed -E 's/[[:space:]]+$//')"
	[[ -n "$val" ]] || return 1
	printf '%s' "$val"
}

# _e2e_resolve_token <config-key> <prompt-label> <default> — first-hit-wins:
# .e2e-config.yaml → interactive prompt (TTY only, unless --yes) → default.
# Echoes the resolved value on stdout; all chatter goes to stderr.
_e2e_resolve_token() {
	local key="$1" label="$2" default="$3" val
	if val="$(_e2e_config_get "$key" "$_E2E_CONFIG_FILE")"; then
		log_info "token $key from .e2e-config.yaml: $val"
		printf '%s' "$val"
		return 0
	fi
	if [[ "$_E2E_YES" -eq 0 && -t 0 && -t 1 ]]; then
		local reply
		printf '%s [%s]: ' "$label" "$default" >&2
		IFS= read -r reply || reply=""
		if [[ -n "$reply" ]]; then
			printf '%s' "$reply"
			return 0
		fi
	fi
	printf '%s' "$default"
}

# --- Scaffold list/copy ------------------------------------------------------

# _e2e_each_file — print every regular file under the template root, one per
# line, as a path relative to CC_TEMPLATE_ROOT. Sorted for stable output.
_e2e_each_file() {
	(cd "$CC_TEMPLATE_ROOT" && find . -type f | sed -e 's#^\./##' | LC_ALL=C sort)
}

# _e2e_dest_rel <rel> — map a template-relative path to its scaffolded name,
# stripping a trailing `.tmpl` suffix.
_e2e_dest_rel() {
	case "$1" in
	*.tmpl) printf '%s' "${1%.tmpl}" ;;
	*) printf '%s' "$1" ;;
	esac
}

# _e2e_copy_tree <stage> — populate <stage> from the template, substituting
# tokens into every text file and stripping `.tmpl` suffixes. Returns non-zero
# on the first failure so the caller can abort and clean the staging dir.
_e2e_copy_tree() {
	local stage="$1" rel destrel src dst
	while IFS= read -r rel; do
		[[ -n "$rel" ]] || continue
		destrel="$(_e2e_dest_rel "$rel")"
		src="$CC_TEMPLATE_ROOT/$rel"
		dst="$stage/$destrel"
		mkdir -p "$(dirname "$dst")" || return 1
		_e2e_substitute "$src" "$dst" || {
			log_error "failed to write $destrel"
			return 1
		}
	done <<EOF
$(_e2e_each_file)
EOF
	return 0
}

# --- Optional fault-injection test seam (AC5 atomicity verification) ---------
# When CC_E2E_FAIL_AFTER_COPY=1, the scaffold aborts AFTER the staging tree is
# built but BEFORE the atomic move, to prove an interruption leaves no partial
# <dir>/e2e/ and the staging dir is cleaned by the EXIT trap. Test-only.

# --- npm install robustness test seam ----------------------------------------
# CC_SKIP_NPM=1 skips the real `npm install` (used by tests / offline envs). The
# scaffold still succeeds and prints the handoff. In normal operation npm install
# is attempted but a failure (missing npm / no network) is a WARNING, not fatal:
# the scaffold itself already succeeded.

_e2e_npm_install() {
	local dir="$1"
	if [[ "${CC_SKIP_NPM:-0}" != "0" ]]; then
		log_warn "npm install skipped (CC_SKIP_NPM set) — run 'npm install' in $dir before first test"
		return 0
	fi
	if ! command -v npm >/dev/null 2>&1; then
		log_warn "npm not found on PATH — skipping dependency install"
		log_warn "install Node.js, then run 'npm install' in $dir before the first test"
		return 0
	fi
	log_info "running npm install in $dir (Playwright deps)"
	if (cd "$dir" && npm install >&2); then
		log_ok "npm install complete"
	else
		log_warn "npm install failed (offline or registry error) — scaffold is otherwise complete"
		log_warn "re-run 'npm install' (and 'npx playwright install') in $dir when online"
	fi
	return 0
}

# --- git init + initial commit (ADR-002) -------------------------------------
_e2e_git_init() {
	local dir="$1"
	if ! command -v git >/dev/null 2>&1; then
		log_warn "git not found — skipping repo init (use --no-git to silence)"
		return 0
	fi
	(
		cd "$dir" || exit 1
		git init -q . || exit 1
		git add -A || exit 1
		# Use explicit author/committer envs so the commit succeeds even when the
		# machine has no global git identity configured.
		GIT_AUTHOR_NAME="${GIT_AUTHOR_NAME:-copilotcockpit}" \
			GIT_AUTHOR_EMAIL="${GIT_AUTHOR_EMAIL:-bootstrap@copilotcockpit.local}" \
			GIT_COMMITTER_NAME="${GIT_COMMITTER_NAME:-copilotcockpit}" \
			GIT_COMMITTER_EMAIL="${GIT_COMMITTER_EMAIL:-bootstrap@copilotcockpit.local}" \
			git commit -q -m "chore: scaffold e2e smoke harness (copilotcockpit)" || exit 1
	) || {
		log_warn "git init/commit failed in $dir — the scaffold files are intact"
		return 0
	}
	log_ok "git initialised e2e/ with an initial commit"
	return 0
}

# --- Handoff (AC7) -----------------------------------------------------------
_e2e_print_handoff() {
	local dir="$1"
	# Machine-relevant guidance on stdout; this is the command's payload.
	printf '\n'
	printf 'Smoke harness ready. Next: run /setup-e2e-cockpit then /setup-e2e-runbook\n'
	printf '\n'
	printf 'The e2e/ harness is its own git repo (ADR-002). To keep it out of the\n'
	printf 'parent project history, add this line to the parent .gitignore:\n'
	printf '\n'
	printf '  /e2e/\n'
	printf '\n'
	printf 'Scaffolded into: %s\n' "$dir"
}

# --- main --------------------------------------------------------------------
main() {
	local target="" do_update=0 no_git=0
	_E2E_YES=0

	# --- Parse options -------------------------------------------------------
	while [[ $# -gt 0 ]]; do
		case "$1" in
		--update) do_update=1; shift ;;
		--no-git) no_git=1; shift ;;
		--yes) _E2E_YES=1; shift ;;
		--dry-run) DRY_RUN=1; shift ;;
		-h | --help) usage; return 0 ;;
		--) shift; break ;;
		-*)
			log_error "e2e: unknown option: $1"
			usage >&2
			return 2
			;;
		*)
			if [[ -n "$target" ]]; then
				log_error "e2e: unexpected extra argument: $1"
				usage >&2
				return 2
			fi
			target="$1"
			shift
			;;
		esac
	done
	export DRY_RUN

	# --- AC1: <dir> is required ----------------------------------------------
	if [[ -z "$target" ]]; then
		log_error "e2e: missing <dir> argument"
		usage >&2
		return 2
	fi

	# --- AC2: <dir> must be an existing directory ----------------------------
	if [[ ! -d "$target" ]]; then
		log_error "e2e: not a directory: $target"
		return 1
	fi

	# Preflight: the template tree must exist (developer integrity check).
	if [[ ! -d "$CC_TEMPLATE_ROOT" ]]; then
		log_error "e2e: template tree missing: $CC_TEMPLATE_ROOT"
		return 1
	fi

	local target_abs dest
	target_abs="$(cc_realpath "$target")" || {
		log_error "e2e: cannot resolve path: $target"
		return 1
	}
	dest="$target_abs/e2e"

	# --- --update is a separate story (TH1-E3-US6) ---------------------------
	if [[ "$do_update" -eq 1 ]]; then
		log_error "e2e: --update (content-preserving refresh) is not yet implemented (TH1-E3-US6)"
		return 2
	fi

	# --- AC2: refuse to clobber an existing e2e/ -----------------------------
	if [[ -e "$dest" ]]; then
		log_error "e2e: $dest already exists — refusing to clobber"
		log_error "use 'bootstrap.sh e2e $target --update' to refresh an existing harness"
		return 1
	fi

	# --- AC3: resolve the 4 Tier-1 tokens (first-hit-wins) -------------------
	_E2E_CONFIG_FILE="$target_abs/.e2e-config.yaml"
	local def_app
	def_app="$(basename "$target_abs")"

	local app_name backend_port frontend_port health_path
	app_name="$(_e2e_resolve_token app_name "App name (@@APP_NAME@@)" "$def_app")"
	backend_port="$(_e2e_resolve_token backend_port "Backend port (@@BACKEND_PORT@@)" "8000")"
	frontend_port="$(_e2e_resolve_token frontend_port "Frontend port (@@FRONTEND_PORT@@)" "5173")"
	health_path="$(_e2e_resolve_token health_path "Health path (@@HEALTH_PATH@@)" "/health")"

	# Pre-escape once for the substitution pass (used inside _e2e_substitute).
	_E2E_APP_NAME_ESC="$(_e2e_sed_escape "$app_name")"
	_E2E_BACKEND_PORT_ESC="$(_e2e_sed_escape "$backend_port")"
	_E2E_FRONTEND_PORT_ESC="$(_e2e_sed_escape "$frontend_port")"
	_E2E_HEALTH_PATH_ESC="$(_e2e_sed_escape "$health_path")"

	# --- AC8: dry-run — print plan, write nothing ----------------------------
	if [[ "${DRY_RUN:-0}" != "0" ]]; then
		printf 'e2e dry-run — would scaffold into: %s\n' "$dest"
		printf '\nResolved tokens:\n'
		printf '  @@APP_NAME@@      = %s\n' "$app_name"
		printf '  @@BACKEND_PORT@@  = %s\n' "$backend_port"
		printf '  @@FRONTEND_PORT@@ = %s\n' "$frontend_port"
		printf '  @@HEALTH_PATH@@   = %s\n' "$health_path"
		printf '\nScaffold file list (%s files):\n' "$(_e2e_each_file | grep -c .)"
		local rel
		while IFS= read -r rel; do
			[[ -n "$rel" ]] || continue
			printf '  e2e/%s\n' "$(_e2e_dest_rel "$rel")"
		done <<EOF
$(_e2e_each_file)
EOF
		printf '\n(git init: %s, npm install: yes) — nothing written.\n' \
			"$([[ "$no_git" -eq 1 ]] && printf no || printf yes)"
		return 0
	fi

	# --- AC5: atomic staging -------------------------------------------------
	# Build into a sibling staging dir on the SAME filesystem as <dir>/e2e so the
	# final move is an atomic rename. An EXIT trap wipes the staging dir on ANY
	# failure/interrupt, so a partial run never leaves a half-written e2e/.
	local stage
	stage="$target_abs/.e2e-staging-$(cc_timestamp)-$$"
	_CC_E2E_STAGE="$stage"
	trap 'rm -rf "${_CC_E2E_STAGE:-}"' EXIT

	if ! mkdir -p "$stage"; then
		log_error "e2e: failed to create staging dir: $stage"
		return 1
	fi
	log_info "scaffolding into staging dir: $stage"

	# --- AC4: copy + tokenise into staging -----------------------------------
	if ! _e2e_copy_tree "$stage"; then
		log_error "e2e: scaffold copy failed — nothing was placed in $dest"
		return 1
	fi

	# Test seam (AC5): abort after the tree is staged, before the atomic move.
	if [[ "${CC_E2E_FAIL_AFTER_COPY:-0}" != "0" ]]; then
		log_error "e2e: CC_E2E_FAIL_AFTER_COPY set — aborting before move (test seam)"
		return 1
	fi

	# Self-audit: no unresolved sentinel tokens may survive (ADR-004 / AC4).
	if grep -rl '@@[A-Z_]\{1,\}@@' "$stage" >/dev/null 2>&1; then
		log_error "e2e: unresolved @@TOKEN@@ remained after substitution — aborting"
		grep -rn '@@[A-Z_]\{1,\}@@' "$stage" >&2 || true
		return 1
	fi

	# --- AC5: atomic move into place -----------------------------------------
	if ! mv "$stage" "$dest"; then
		log_error "e2e: failed to move staging into place: $dest"
		return 1
	fi
	# Staging consumed by the move; disarm the cleanup trap.
	_CC_E2E_STAGE=""
	trap - EXIT
	log_ok "scaffolded e2e/ into $dest"

	# --- AC6: git init + initial commit (unless --no-git) --------------------
	if [[ "$no_git" -eq 1 ]]; then
		log_info "--no-git: leaving e2e/ as a plain directory (parent owns the files)"
	else
		_e2e_git_init "$dest"
	fi

	# --- AC7: npm install (tolerant) + handoff -------------------------------
	_e2e_npm_install "$dest"
	_e2e_print_handoff "$dest"
	return 0
}

main "$@"
