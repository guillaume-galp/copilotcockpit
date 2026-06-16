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
# All 8 managed skills are now REQUIRED sources vendored in this repo: the 7
# harness skills plus copilotcockpit-dev (the 8th, ADR-008), whose source landed
# in TH1-E5-US3. A missing REQUIRED source is fatal (AC9). CC_PENDING_ROLES is
# retained (now empty) so the enumeration shape stays stable for any future
# known-pending source; an empty list contributes nothing to CC_ALL_ROLES.
CC_HARNESS_ROLES="copilotcockpit-dev e2e-cockpit e2e-operator setup-e2e-cockpit setup-e2e-runbook worker-dev worker-fix worker-test"
CC_PENDING_ROLES=""
CC_ALL_ROLES="$CC_HARNESS_ROLES $CC_PENDING_ROLES"

usage() {
	cat <<'EOF'
Usage: bootstrap.sh global [--link] [--dry-run] [--from-release <ref>]

Install/update the managed skills and cockpit-wake into your home:
  ~/.copilot/skills/<role>/SKILL.md   (8 managed roles)
  ~/.local/bin/cockpit-wake           (+x)

Options:
  --link                Symlink each artefact back to the repo instead of copying.
  --dry-run             Describe every action; change nothing.
  --from-release <ref>  Cold-install from a GitHub release instead of local files.
                        <ref> is 'latest' or a tag like 'v1.2.3'. The tarball and
                        its .sha256 are downloaded and verified before extraction.
  -h, --help            Show this help and exit.
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

# --- Cold-install plumbing (AC3/AC4/AC6, ADR-007, architecture §8) ------------
#
# Test seam (intentional, undocumented in user help): set CC_RELEASE_BASE_URL to
# a base URL — including a `file://<dir>` — to fetch the tarball + `.sha256` from
# a local fixture instead of the live GitHub Releases CDN. This lets E5 integration
# tests and TH1-E2-US4's local verification exercise the verify/extract/run/abort
# path with NO published release. CC_RELEASE_REPO overrides the `<org>/<repo>` slug.

# cc_curl_download <url> <dest> — fetch <url> into <dest> with curl -fsSL.
# Honours GH_TOKEN as a bearer auth header when present (AC7: rate-limit / private
# mitigation); the documented one-liner needs no token because the redirect is
# public. Non-zero on any HTTP/transport failure (curl -f).
cc_curl_download() {
	local url="$1" dest="$2"
	if [[ -n "${GH_TOKEN:-}" ]]; then
		curl -fsSL -H "Authorization: Bearer ${GH_TOKEN}" "$url" -o "$dest"
	else
		curl -fsSL "$url" -o "$dest"
	fi
}

# cc_install_from_release <ref> [passthrough-args...] — fetch+verify+extract a
# release tarball, then run the NORMAL install pass (E2-US3) from the extracted
# directory (AC3/AC4). Atomic (AC6): everything happens in a mktemp dir that is
# cleaned on ANY exit; the checksum is verified BEFORE extraction, so a bad
# download or tamper leaves NO partial install.
cc_install_from_release() {
	local ref="$1"
	shift

	local repo="${CC_RELEASE_REPO:-copilotcockpit/copilotcockpit}"
	local tarball base
	case "$ref" in
	latest)
		# Stable unversioned alias via the redirect — no API call (AC7, ADR-007).
		tarball="copilotcockpit.tar.gz"
		base="https://github.com/${repo}/releases/latest/download"
		;;
	v[0-9]*.[0-9]*.[0-9]*)
		# Pin to an explicit tag's version-stamped asset (architecture §8).
		tarball="copilotcockpit-${ref}.tar.gz"
		base="https://github.com/${repo}/releases/download/${ref}"
		;;
	*)
		log_error "global: --from-release ref must be 'latest' or 'vX.Y.Z' (got: $ref)"
		return 2
		;;
	esac
	# Test seam: override the base URL (e.g. file://) for local/E5 testing.
	base="${CC_RELEASE_BASE_URL:-$base}"

	local tmp
	tmp="$(mktemp -d "${TMPDIR:-/tmp}/cc-release.XXXXXX")" || {
		log_error "global: failed to create temp working directory"
		return 1
	}
	# Atomicity (AC6/NFR-1): wipe the temp dir on ANY exit. Use a script-global
	# (not the function-local `tmp`, which is out of scope when the EXIT trap
	# fires) with a :- default so the trap is safe under `set -u`. The install
	# pass below copies into HOME then returns, so nothing partial is left behind.
	_CC_RELEASE_TMP="$tmp"
	trap 'rm -rf "${_CC_RELEASE_TMP:-}"' EXIT

	local tb="$tmp/$tarball" sha="$tmp/$tarball.sha256"

	log_info "fetching release '$ref' from $base"
	if ! cc_curl_download "$base/$tarball" "$tb"; then
		log_error "global: download failed: $base/$tarball"
		return 1
	fi
	if ! cc_curl_download "$base/$tarball.sha256" "$sha"; then
		log_error "global: download failed: $base/$tarball.sha256"
		return 1
	fi

	# Verify BEFORE extracting (AC3/AC6) — tamper-evident, abort on mismatch.
	local expected
	expected="$(awk '{print $1; exit}' "$sha")"
	if ! cc_sha256_verify "$tb" "$expected"; then
		log_error "global: checksum verification failed — nothing was installed"
		return 1
	fi
	log_ok "checksum verified: $tarball"

	if ! tar -xzf "$tb" -C "$tmp"; then
		log_error "global: failed to extract $tb"
		return 1
	fi
	local extracted="$tmp/copilotcockpit"
	if [[ ! -x "$extracted/bootstrap.sh" ]]; then
		log_error "global: extracted tarball missing copilotcockpit/bootstrap.sh"
		return 1
	fi

	# AC4: run the ordinary local install pass from the extracted dir. We run
	# (not exec) so the EXIT trap above still cleans the temp dir afterwards.
	log_info "running global install from extracted release ($ref)"
	"$extracted/bootstrap.sh" global "$@"
}

main() {
	_CC_LINK_MODE=0
	local from_release=""
	# Passthrough args forwarded to the inner install pass when --from-release is
	# used (AC4). bash 3.2-safe indexed array; expanded with the empty-array guard.
	local -a pass=()

	# --- Parse options (AC1/AC6/AC7) -----------------------------------------
	while [[ $# -gt 0 ]]; do
		case "$1" in
		--from-release)
			from_release="${2:-}"
			if [[ -z "$from_release" ]]; then
				log_error "global: --from-release requires a <ref> (latest | vX.Y.Z)"
				usage >&2
				return 2
			fi
			shift 2
			;;
		--from-release=*)
			from_release="${1#*=}"
			if [[ -z "$from_release" ]]; then
				log_error "global: --from-release requires a <ref> (latest | vX.Y.Z)"
				usage >&2
				return 2
			fi
			shift
			;;
		--link)
			_CC_LINK_MODE=1
			pass+=("--link")
			shift
			;;
		--dry-run)
			# bootstrap.sh already sets DRY_RUN; honour it here too for robustness.
			DRY_RUN=1
			pass+=("--dry-run")
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

	# --- Cold-install path (AC3/AC4/AC6) -------------------------------------
	# Entered ONLY when --from-release is explicitly given. With no flag we fall
	# through to the local-files install pass below and make NO network call
	# (AC5, ADR-007 default).
	if [[ -n "$from_release" ]]; then
		cc_install_from_release "$from_release" ${pass[@]+"${pass[@]}"}
		return $?
	fi

	local skills_root="$CC_ROOT/skills"
	local cw_src="$CC_ROOT/bin/cockpit-wake"
	local skills_dst_root="$HOME/.copilot/skills"
	local home_bin="$HOME/.local/bin"
	local cw_dst="$home_bin/cockpit-wake"

	# --- Preflight: all REQUIRED sources must exist BEFORE any write (AC9) ----
	# Validate up front so a missing required source never leaves a partial,
	# silent install. All 8 managed skills are required (copilotcockpit-dev
	# included since TH1-E5-US3); CC_PENDING_ROLES is empty.
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
			# Defensive only: with CC_PENDING_ROLES now empty, every role is a
			# required source already verified in preflight, so this is dead
			# code unless a future known-pending source is reintroduced.
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
