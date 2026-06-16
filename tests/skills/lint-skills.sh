#!/usr/bin/env bash
# tests/skills/lint-skills.sh — Category 3: SKILL.md lint (ADR-008, §9).
#
# Asserts every skills/*/SKILL.md is a valid Markdown skill file:
#   * it opens with a YAML frontmatter block delimited by `---` lines;
#   * that frontmatter parses as YAML (python3 + PyYAML);
#   * it has a non-empty `name:` AND a non-empty `description:`.
# Fails NAMING the offending skill (BDD: "lint rejects empty frontmatter").
#
# Portability (TH1-E6-US1): bash 3.2-safe; no GNU-only flags, no mapfile/assoc
# arrays. PyYAML is a contributor-only dependency (present in CI / dev box).
set -euo pipefail

# --- Resolve repo root portably (no readlink -f) -----------------------------
_ls_self="$0"
if command -v python3 >/dev/null 2>&1; then
	_ls_self="$(python3 -c 'import os,sys; print(os.path.realpath(sys.argv[1]))' "$0")"
fi
ROOT="$(cd "$(dirname "$_ls_self")/../.." && pwd -P)"
SKILLS_ROOT="$ROOT/skills"

if ! command -v python3 >/dev/null 2>&1; then
	printf 'lint-skills: FATAL: python3 is required (PyYAML frontmatter parse)\n' >&2
	exit 1
fi
if ! python3 -c 'import yaml' >/dev/null 2>&1; then
	printf 'lint-skills: FATAL: PyYAML not installed — `pip install pyyaml`\n' >&2
	exit 1
fi
if [[ ! -d "$SKILLS_ROOT" ]]; then
	printf 'lint-skills: FATAL: skills root not found: %s\n' "$SKILLS_ROOT" >&2
	exit 1
fi

fails=0
fail() { printf 'lint-skills: FAIL: %s\n' "$*" >&2; fails=$((fails + 1)); }
ok() { printf 'lint-skills: ok:   %s\n' "$*"; }

# _lint_one <skill-name> <skill-md> — validate one SKILL.md. Prints OK/PROBLEM.
# The python helper exits non-zero with a one-line reason on any problem, naming
# nothing itself (the bash caller owns the skill name in the message).
_lint_one() {
	python3 - "$2" <<'PY'
import sys, yaml

path = sys.argv[1]
try:
    text = open(path, encoding="utf-8").read()
except OSError as e:
    print("cannot read file: %s" % e)
    sys.exit(1)

# Frontmatter must be the very first thing in the file (allow a leading BOM only).
if text.startswith("\ufeff"):
    text = text[1:]
lines = text.splitlines()
if not lines or lines[0].strip() != "---":
    print("missing opening '---' frontmatter delimiter")
    sys.exit(1)

# Find the closing delimiter.
end = None
for i in range(1, len(lines)):
    if lines[i].strip() == "---":
        end = i
        break
if end is None:
    print("missing closing '---' frontmatter delimiter")
    sys.exit(1)

block = "\n".join(lines[1:end])
try:
    meta = yaml.safe_load(block)
except yaml.YAMLError as e:
    print("frontmatter is not parseable YAML: %s" % e)
    sys.exit(1)

if not isinstance(meta, dict):
    print("frontmatter did not parse to a mapping")
    sys.exit(1)

for key in ("name", "description"):
    val = meta.get(key)
    if val is None or (isinstance(val, str) and val.strip() == ""):
        print("empty or missing '%s:'" % key)
        sys.exit(1)

sys.exit(0)
PY
}

count=0
while IFS= read -r md; do
	[[ -n "$md" ]] || continue
	count=$((count + 1))
	# skill name = parent dir name.
	name="$(basename "$(dirname "$md")")"
	if reason="$(_lint_one "$name" "$md")"; then
		ok "$name"
	else
		fail "skill '$name' ($md): $reason"
	fi
done <<EOF
$(find "$SKILLS_ROOT" -type f -name 'SKILL.md' | LC_ALL=C sort)
EOF

if [[ "$count" -eq 0 ]]; then
	fail "no skills/*/SKILL.md files found under $SKILLS_ROOT"
fi

printf '\n'
if [[ "$fails" -ne 0 ]]; then
	printf 'lint-skills: %d skill(s) FAILED\n' "$fails" >&2
	exit 1
fi
printf 'lint-skills: ALL %d SKILL.md files OK\n' "$count"
exit 0
