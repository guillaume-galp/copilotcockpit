#!/usr/bin/env bash
# check-codex.sh — Codex repository compatibility checks.
#
# Verifies Codex scaffolding guidance, Codex-visible files, and canonical skill
# hygiene for project-to-runtime parity.
#
# Bash 3.2-compatible.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd -P)"
SKILLS_ROOT="$ROOT/skills"
TEMPLATE_ROOT="$ROOT/templates/e2e"
MANIFEST="$TEMPLATE_ROOT/MANIFEST.toml"

roles="e2e-cockpit e2e-operator setup-e2e-cockpit setup-e2e-runbook worker-dev worker-fix worker-test copilotcockpit-dev"

fail() { printf 'check-codex: FAIL: %s\n' "$*" >&2; return 1; }
ok() { printf 'check-codex: ok:   %s\n' "$*"; }

has_forbidden() {
	file="$1"
	shift
	for bad in "$@"; do
		if grep -q "$bad" "$file"; then
			return 0
		fi
	done
	return 1
}

check_frontmatter() {
	file="$1"
	[ -f "$file" ] || return 1

	first=1
	in_frontmatter=0
	name=""
	description=""
	while IFS= read -r line; do
		if [ "$first" -eq 1 ]; then
			first=0
			if [ "$line" != "---" ]; then
				return 1
			fi
			in_frontmatter=1
			continue
		fi
		[ "$in_frontmatter" -eq 0 ] && continue
		if [ "$line" = "---" ]; then
			break
		fi
		case "$line" in
			name:*) name="$(printf '%s' "${line#name:}")" ;;
			description:*) description="$(printf '%s' "${line#description:}")" ;;
		esac
	done < "$file"
	[ -n "$name" ] && [ -n "$description" ]
}

# --- file existence checks ---------------------------------------------------
printf '== Codex required files ==\n'
[ -f "$ROOT/AGENTS.md" ] || { fail "AGENTS.md missing"; exit 1; }
ok "AGENTS.md exists"

[ -f "$ROOT/.codex/config.toml" ] || { fail ".codex/config.toml missing"; exit 1; }
ok ".codex/config.toml exists"

for role in $roles; do
	[ -f "$ROOT/.agents/skills/$role/SKILL.md" ] || { fail ".agents/skills/$role/SKILL.md missing"; exit 1; }
done
ok "Repo Codex skill overlays exist"

# --- canonical frontmatter checks -------------------------------------------
printf '\n== Canonical SKILL.md frontmatter checks ==\n'
for role in $roles; do
	file="$SKILLS_ROOT/$role/SKILL.md"
	[ -f "$file" ] || { fail "missing canonical skill: $file"; exit 1; }
	if ! check_frontmatter "$file"; then
		fail "$role: SKILL.md missing non-empty frontmatter name/description"
		exit 1
	fi
	ok "$role frontmatter has non-empty name and description"
	done

# --- hardcoded legacy references in canonical global worker skills -------------
printf '\n== Canonical worker skill hardcoding check ==\n'
for f in "$SKILLS_ROOT/worker-dev/SKILL.md" "$SKILLS_ROOT/worker-fix/SKILL.md" "$SKILLS_ROOT/worker-test/SKILL.md"; do
	if has_forbidden "$f" "ulysses-index" "/home/guillaume/git/ulysses-index" "/home/guillaume" "localhost:5002/healthcheck" "@TC-CLI-007" "@cli"; then
		fail "found hardcoded reference in $f"
		exit 1
	fi
done
ok "No removed hardcoded worker references found"

# --- template manifest checks ------------------------------------------------
printf '\n== Template manifest checks ==\n'
[ -f "$MANIFEST" ] || { fail "manifest missing: $MANIFEST"; exit 1; }
for required in 'AGENTS.md' '.codex/config.toml' '.agents/skills/**' '.codex/local.env' '.codex/secrets.example'; do
	if ! grep -Fq "$required" "$MANIFEST"; then
		fail "missing manifest entry: $required"
		exit 1
	fi
done
ok "Manifest includes Codex ownership entries"

# --- template overlay file checks --------------------------------------------
printf '\n== Template overlay files ==\n'
for f in \
	"$TEMPLATE_ROOT/AGENTS.md.tmpl" \
	"$TEMPLATE_ROOT/.codex/config.toml.tmpl" \
	"$TEMPLATE_ROOT/.agents/skills/e2e-cockpit/SKILL.md.tmpl" \
	"$TEMPLATE_ROOT/.agents/skills/e2e-operator/SKILL.md.tmpl" \
	"$TEMPLATE_ROOT/.agents/skills/worker-dev/SKILL.md.tmpl" \
	"$TEMPLATE_ROOT/.agents/skills/worker-fix/SKILL.md.tmpl" \
	"$TEMPLATE_ROOT/.agents/skills/worker-test/SKILL.md.tmpl"; do
	[ -f "$f" ] || { fail "missing template file: $f"; exit 1; }
done
ok "All required Codex template files exist"

printf '\ncheck-codex: ALL CHECKS PASSED\n'
exit 0
