#!/usr/bin/env bash
# tests/template/check-template.sh — Category 2: template integrity (ADR-008, §9).
#
# Asserts, against the REAL templates/e2e/ tree (exit 0 when all hold):
#   1. Every *.tmpl resolves cleanly: after substituting the 4 sanctioned Tier-1
#      tokens with TEST values, NO `@@...@@` token survives (an unsanctioned or
#      mistyped token is a bug). Fails naming the offending file.
#   2. package.json.tmpl is valid JSON post-substitution (python3 json.load).
#   3. MANIFEST.toml accounts for EVERY scaffolded path — each path (post-
#      tokenisation, `.tmpl`-stripped) matches at least one EXPLICIT manifest glob
#      (seed | framework | project). A path that only resolves via the safe
#      default is reported as orphaned (mirrors the E3-US4 coverage check, but
#      strict: the manifest must classify each path, not lean on the fallback).
#   4. run-audit.sh & run-playwright.sh pass `bash -n`.
#
# The 4 sanctioned tokens (Tier-1, lib/cmd-e2e.sh): @@APP_NAME@@ @@BACKEND_PORT@@
# @@FRONTEND_PORT@@ @@HEALTH_PATH@@.
#
# Portability (TH1-E6-US1): bash 3.2-safe; no `sed -i`, no `readlink -f`, no
# `grep -P`, no associative arrays / mapfile; uutils-safe coreutils only.
set -euo pipefail

# --- Resolve repo root portably (no readlink -f) -----------------------------
_ct_self="$0"
if command -v python3 >/dev/null 2>&1; then
	_ct_self="$(python3 -c 'import os,sys; print(os.path.realpath(sys.argv[1]))' "$0")"
fi
ROOT="$(cd "$(dirname "$_ct_self")/../.." && pwd -P)"

TEMPLATE_ROOT="$ROOT/templates/e2e"
MANIFEST="$TEMPLATE_ROOT/MANIFEST.toml"

# Test substitution values for the 4 sanctioned tokens.
T_APP_NAME="testapp"
T_BACKEND_PORT="8000"
T_FRONTEND_PORT="5173"
T_HEALTH_PATH="/health"

fails=0
fail() { printf 'check-template: FAIL: %s\n' "$*" >&2; fails=$((fails + 1)); }
ok() { printf 'check-template: ok:   %s\n' "$*"; }

if [[ ! -d "$TEMPLATE_ROOT" ]]; then
	printf 'check-template: FATAL: template root not found: %s\n' "$TEMPLATE_ROOT" >&2
	exit 1
fi
if [[ ! -f "$MANIFEST" ]]; then
	printf 'check-template: FATAL: manifest not found: %s\n' "$MANIFEST" >&2
	exit 1
fi

# _sub <src> — emit <src> with the 4 sanctioned tokens substituted (test values).
# Mirrors lib/cmd-e2e.sh _e2e_substitute (the health path contains `/`, so the
# replacement is escaped exactly as the scaffold does it).
_sed_escape() { printf '%s' "$1" | sed -e 's/\\/\\\\/g' -e 's/[&/]/\\&/g'; }
_sub() {
	local src="$1"
	sed \
		-e "s/@@APP_NAME@@/$(_sed_escape "$T_APP_NAME")/g" \
		-e "s/@@BACKEND_PORT@@/$(_sed_escape "$T_BACKEND_PORT")/g" \
		-e "s/@@FRONTEND_PORT@@/$(_sed_escape "$T_FRONTEND_PORT")/g" \
		-e "s/@@HEALTH_PATH@@/$(_sed_escape "$T_HEALTH_PATH")/g" \
		"$src"
}

# --- Check 1: no unresolved tokens after a TEST substitution ------------------
printf '== Check 1: *.tmpl token resolution ==\n'
_tmpl_count=0
while IFS= read -r tmpl; do
	[[ -n "$tmpl" ]] || continue
	_tmpl_count=$((_tmpl_count + 1))
	# Any surviving @@TOKEN@@ after substitution is an unsanctioned/typo'd token.
	survivors="$(_sub "$tmpl" | grep -o '@@[A-Za-z0-9_]*@@' | LC_ALL=C sort -u || true)"
	if [[ -n "$survivors" ]]; then
		fail "unresolved token(s) in ${tmpl#"$ROOT"/}: $(printf '%s' "$survivors" | tr '\n' ' ')"
	else
		ok "${tmpl#"$ROOT"/} — fully resolved"
	fi
done <<EOF
$(find "$TEMPLATE_ROOT" -type f -name '*.tmpl' | LC_ALL=C sort)
EOF
[[ "$_tmpl_count" -gt 0 ]] || fail "no *.tmpl files found under $TEMPLATE_ROOT"

# --- Check 2: package.json.tmpl is valid JSON post-substitution --------------
printf '\n== Check 2: package.json.tmpl is valid JSON post-substitution ==\n'
PKG_TMPL="$TEMPLATE_ROOT/package.json.tmpl"
if [[ ! -f "$PKG_TMPL" ]]; then
	fail "package.json.tmpl not found: $PKG_TMPL"
else
	pkg_tmp="$(mktemp "${TMPDIR:-/tmp}/cc-pkgjson.XXXXXX")"
	_sub "$PKG_TMPL" >"$pkg_tmp"
	if python3 -c 'import json,sys; json.load(open(sys.argv[1]))' "$pkg_tmp"; then
		ok "package.json.tmpl parses as JSON after substitution"
	else
		fail "package.json.tmpl is NOT valid JSON after substitution"
	fi
	rm -f "$pkg_tmp"
fi

# --- Check 3: MANIFEST.toml coverage (every scaffolded path classified) ------
printf '\n== Check 3: MANIFEST.toml accounts for every templates/e2e/ path ==\n'

# Mirror lib/cmd-e2e.sh _e2e_manifest_section: print the quoted entries of a
# [section] table, one per line.
_manifest_section() {
	awk -v sect="$1" '
		BEGIN { in_s = 0 }
		/^[[:space:]]*\[/ {
			hdr = $0; sub(/[[:space:]]*#.*/, "", hdr); gsub(/[[:space:]]/, "", hdr)
			in_s = (hdr == "[" sect "]") ? 1 : 0
			next
		}
		in_s {
			line = $0
			while (match(line, /"[^"]*"/)) {
				print substr(line, RSTART + 1, RLENGTH - 2)
				line = substr(line, RSTART + RLENGTH)
			}
		}
	' "$MANIFEST"
}

# Load the three classes into bash 3.2-safe indexed arrays.
SEED=() FRAMEWORK=() PROJECT=()
while IFS= read -r p; do [[ -n "$p" ]] && SEED+=("$p"); done <<EOF
$(_manifest_section seed)
EOF
while IFS= read -r p; do [[ -n "$p" ]] && FRAMEWORK+=("$p"); done <<EOF
$(_manifest_section framework)
EOF
while IFS= read -r p; do [[ -n "$p" ]] && PROJECT+=("$p"); done <<EOF
$(_manifest_section project)
EOF

if [[ ${#SEED[@]} -eq 0 || ${#FRAMEWORK[@]} -eq 0 || ${#PROJECT[@]} -eq 0 ]]; then
	fail "MANIFEST.toml is missing one of [seed]/[framework]/[project] (empty class)"
fi

# _classify_explicit <path> — echo seed|framework|project for an EXPLICIT match
# (precedence seed → framework → project, mirroring _e2e_classify), or empty when
# the path matches NO explicit glob (i.e. it would only resolve via the default).
_classify_explicit() {
	local p="$1" g
	for g in ${SEED[@]+"${SEED[@]}"}; do [[ "$p" == $g ]] && { printf 'seed'; return; }; done
	for g in ${FRAMEWORK[@]+"${FRAMEWORK[@]}"}; do [[ "$p" == $g ]] && { printf 'framework'; return; }; done
	for g in ${PROJECT[@]+"${PROJECT[@]}"}; do [[ "$p" == $g ]] && { printf 'project'; return; }; done
	printf ''
}

# _dest_rel <rel> — strip a trailing `.tmpl` (mirrors _e2e_dest_rel).
_dest_rel() { case "$1" in *.tmpl) printf '%s' "${1%.tmpl}" ;; *) printf '%s' "$1" ;; esac; }

orphans=0
while IFS= read -r rel; do
	[[ -n "$rel" ]] || continue
	destrel="$(_dest_rel "$rel")"
	cls="$(_classify_explicit "$destrel")"
	if [[ -z "$cls" ]]; then
		fail "ORPHANED path (matches no MANIFEST glob): $destrel"
		orphans=$((orphans + 1))
	fi
done <<EOF
$(cd "$TEMPLATE_ROOT" && find . -type f | sed -e 's#^\./##' | LC_ALL=C sort)
EOF
if [[ "$orphans" -eq 0 ]]; then
	ok "every scaffolded path matches an explicit MANIFEST glob"
fi

# --- Check 4: harness shell scripts parse (bash -n) --------------------------
printf '\n== Check 4: run-audit.sh & run-playwright.sh parse (bash -n) ==\n'
for sh in run-audit.sh run-playwright.sh; do
	f="$TEMPLATE_ROOT/$sh"
	if [[ ! -f "$f" ]]; then
		fail "$sh not found: $f"
		continue
	fi
	if bash -n "$f"; then
		ok "$sh passes bash -n"
	else
		fail "$sh has a syntax error (bash -n)"
	fi
done

# --- Verdict -----------------------------------------------------------------
printf '\n'
if [[ "$fails" -ne 0 ]]; then
	printf 'check-template: %d check(s) FAILED\n' "$fails" >&2
	exit 1
fi
printf 'check-template: ALL CHECKS PASSED\n'
exit 0
