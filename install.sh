#!/usr/bin/env bash
# install.sh — cold-install wrapper for copilotcockpit (ADR-007, TH1-E2-US4).
#
# A tiny, auditable fetch-verify-extract-run wrapper and NOTHING else: download
# the latest release tarball + its `.sha256` from the GitHub Releases redirect,
# VERIFY the checksum (abort on mismatch), extract, then `exec` the trusted
# installer. All real install logic stays in bootstrap.sh (ADR-007, architecture
# §8) so this wrapper remains the minimal, reviewable trust boundary.
#
# Usage (cold install — no clone needed):
#   bash <(curl -fsSL https://github.com/<org>/copilotcockpit/releases/latest/download/install.sh)
#
# Portability (portability-cheatsheet.md): curl -fsSL + tar -xzf; sha256 via
# `sha256sum` (Linux) OR `shasum -a 256` (macOS), auto-detected; bash 3.2 safe;
# mktemp with an explicit template + trap cleanup; printf, never echo -e/-n.
set -euo pipefail

# Test seam (internal, for E5 / TH1-E2-US4 local verification): set
# CC_RELEASE_BASE_URL to a base URL — including `file://<dir>` — to fetch from a
# local fixture instead of the live GitHub Releases CDN. CC_RELEASE_REPO
# overrides the `<org>/<repo>` slug. Neither is part of the documented one-liner.
cc_repo="${CC_RELEASE_REPO:-copilotcockpit/copilotcockpit}"
cc_base="${CC_RELEASE_BASE_URL:-https://github.com/${cc_repo}/releases/latest/download}"
cc_tarball="copilotcockpit.tar.gz"

cc_err() { printf 'install.sh: %s\n' "$*" >&2; }

cc_sha256_of() {
	if command -v sha256sum >/dev/null 2>&1; then
		sha256sum "$1" | awk '{print $1; exit}'
	elif command -v shasum >/dev/null 2>&1; then
		shasum -a 256 "$1" | awk '{print $1; exit}'
	else
		cc_err "no sha256 tool found (need sha256sum or shasum)"
		return 1
	fi
}

# Fetch <url> into <dest>; honour GH_TOKEN as a bearer header if set (AC7).
cc_fetch() {
	if [[ -n "${GH_TOKEN:-}" ]]; then
		curl -fsSL -H "Authorization: Bearer ${GH_TOKEN}" "$1" -o "$2"
	else
		curl -fsSL "$1" -o "$2"
	fi
}

# Download + verify happen inside a temp dir; on any failure it is wiped, leaving
# nothing installed (AC6 atomic). On success we extract into the CWD and exec.
cc_tmp="$(mktemp -d "${TMPDIR:-/tmp}/cc-install.XXXXXX")"
trap 'rm -rf "$cc_tmp"' EXIT

cc_fetch "$cc_base/$cc_tarball" "$cc_tmp/$cc_tarball" ||
	{ cc_err "download failed: $cc_base/$cc_tarball"; exit 1; }
cc_fetch "$cc_base/$cc_tarball.sha256" "$cc_tmp/$cc_tarball.sha256" ||
	{ cc_err "download failed: $cc_base/$cc_tarball.sha256"; exit 1; }

cc_expected="$(awk '{print $1; exit}' "$cc_tmp/$cc_tarball.sha256")"
cc_actual="$(cc_sha256_of "$cc_tmp/$cc_tarball")"
if [[ -z "$cc_expected" || "$cc_expected" != "$cc_actual" ]]; then
	cc_err "checksum verification failed — aborting (nothing installed)"
	cc_err "  expected: $cc_expected"
	cc_err "  actual:   $cc_actual"
	exit 1
fi

tar -xzf "$cc_tmp/$cc_tarball" -C .
[[ -x ./copilotcockpit/bootstrap.sh ]] ||
	{ cc_err "tarball missing copilotcockpit/bootstrap.sh"; exit 1; }

# Clean the temp dir now and clear the trap so it does not fire after exec.
rm -rf "$cc_tmp"
trap - EXIT

exec ./copilotcockpit/bootstrap.sh global "$@"
