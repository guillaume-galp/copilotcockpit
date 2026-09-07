#!/usr/bin/env bash
# tests/unit/check-interruption-matrix.sh — TH3.E1.US6 deterministic test gate.
#
# Mechanically proves that the declared coverage registry
# (tests/unit/control-interruption-matrix.tsv):
#
#   1. covers every row of architecture section 8.4 and every required
#      concurrent interleaving named there;
#   2. names a real test that really exists in the named bats suite;
#   3. targets a real named fault-hook boundary that bin/cockpit_control.py
#      actually invokes;
#   4. coordinates deterministically — a declared boundary plus a real
#      coordinated process plus a SIGKILL or a barrier read — so a regression
#      that coordinates only by arbitrary sleeps is rejected;
#   5. proves liveness after the interruption by completing a later mutation.
#
# Portability: bash 3.2 / macOS safe (no associative arrays, no mapfile, no
# GNU-only flags). Operational logs go to stderr with a `cc:`-style prefix.
set -euo pipefail

_self="$0"
if command -v python3 >/dev/null 2>&1; then
	_self="$(python3 -c 'import os,sys; print(os.path.realpath(sys.argv[1]))' "$0")"
fi
UNIT_DIR="$(cd "$(dirname "$_self")" && pwd -P)"
ROOT="$(cd "$UNIT_DIR/../.." && pwd -P)"

REGISTRY="$UNIT_DIR/control-interruption-matrix.tsv"
SUITE_DIR="$UNIT_DIR"
MODULES="$ROOT/bin/cockpit_control.py $ROOT/bin/cockpit_control_locks.py $ROOT/bin/cockpit_control_journal.py"
ARCHITECTURE="$ROOT/docs/architecture/overseer-control-plane.md"
ENTRIES_ONLY=0

PREFIX="interruption-matrix"

usage() {
	cat <<'EOF'
Usage: tests/unit/check-interruption-matrix.sh [options]

  --registry FILE      declared coverage registry (default: tests/unit/control-interruption-matrix.tsv)
  --suite-dir DIR      directory holding the named bats suites (default: tests/unit)
  --module FILE        add one module that may declare fault-hook boundaries
  --architecture FILE  architecture document whose section 8.4 is authoritative
  --entries-only       check registry entries only; skip section 8.4 completeness
  -h, --help           show this help
EOF
}

while [ "$#" -gt 0 ]; do
	case "$1" in
	--registry)
		REGISTRY="$2"
		shift 2
		;;
	--suite-dir)
		SUITE_DIR="$2"
		shift 2
		;;
	--module)
		MODULES="$MODULES $2"
		shift 2
		;;
	--architecture)
		ARCHITECTURE="$2"
		shift 2
		;;
	--entries-only)
		ENTRIES_ONLY=1
		shift
		;;
	-h | --help)
		usage
		exit 0
		;;
	*)
		printf '%s: unknown option: %s\n' "$PREFIX" "$1" >&2
		usage >&2
		exit 2
		;;
	esac
done

failures=0
fail() {
	failures=$((failures + 1))
	printf '%s: rejected: %s\n' "$PREFIX" "$*" >&2
}

for required in "$REGISTRY" "$ARCHITECTURE"; do
	if [ ! -f "$required" ]; then
		printf '%s: missing required input: %s\n' "$PREFIX" "$required" >&2
		exit 2
	fi
done
for module in $MODULES; do
	if [ ! -f "$module" ]; then
		printf '%s: missing required module: %s\n' "$PREFIX" "$module" >&2
		exit 2
	fi
done
if [ ! -d "$SUITE_DIR" ]; then
	printf '%s: missing suite directory: %s\n' "$PREFIX" "$SUITE_DIR" >&2
	exit 2
fi

WORK="$(mktemp -d "${TMPDIR:-/tmp}/interruption-matrix.XXXXXX")"
trap 'rm -rf "$WORK"' EXIT INT TERM

# --- registry (comments and blank lines removed) -----------------------------
awk -F'\t' '
	/^[[:space:]]*#/ { next }
	/^[[:space:]]*$/ { next }
	{ print }
' "$REGISTRY" >"$WORK/entries.tsv"

if [ ! -s "$WORK/entries.tsv" ]; then
	printf '%s: registry declares no coverage: %s\n' "$PREFIX" "$REGISTRY" >&2
	exit 2
fi

# --- required interleavings named by architecture section 8.4 ----------------
REQUIRED_INTERLEAVINGS="acquire-acquire acquire-repair release-acquire repair-acquire bounded-timeout replacement-preservation"

interleaving_phrase() {
	case "$1" in
	acquire-acquire) printf 'acquire/acquire' ;;
	acquire-repair) printf 'acquire/repair' ;;
	release-acquire) printf 'release/acquire' ;;
	repair-acquire) printf 'repair/acquire' ;;
	bounded-timeout) printf 'bounded timeout' ;;
	replacement-preservation) printf 'replacement preservation' ;;
	*) printf '%s' "$1" ;;
	esac
}

# --- section 8.4 text and boundary rows --------------------------------------
awk '
	/^### 8\.4/ { inside = 1 }
	inside && /^## [0-9]/ { exit }
	inside { print }
' "$ARCHITECTURE" >"$WORK/section-8-4.txt"

awk '
	function slugify(value,   text) {
		text = tolower(value)
		gsub(/[^a-z0-9]+/, "-", text)
		sub(/^-+/, "", text)
		sub(/-+$/, "", text)
		return text
	}
	/^### 8\.4/ { inside = 1; next }
	inside && /^#/ { exit }
	inside && /^\|/ {
		if ($0 ~ /^\|[[:space:]]*-+/) { next }
		line = $0
		sub(/^\|[[:space:]]*/, "", line)
		cut = index(line, "|")
		if (cut == 0) { next }
		cell = substr(line, 1, cut - 1)
		sub(/[[:space:]]+$/, "", cell)
		if (tolower(cell) == "boundary") { next }
		if (cell == "") { next }
		print slugify(cell)
	}
' "$ARCHITECTURE" >"$WORK/architecture-boundaries.txt"

if [ "$ENTRIES_ONLY" -eq 0 ]; then
	if [ ! -s "$WORK/architecture-boundaries.txt" ]; then
		printf '%s: could not read section 8.4 boundaries from %s\n' "$PREFIX" "$ARCHITECTURE" >&2
		exit 2
	fi

	awk -F'\t' '$1 == "boundary" { print $2 }' "$WORK/entries.tsv" | sort -u >"$WORK/registered-boundaries.txt"
	sort -u "$WORK/architecture-boundaries.txt" >"$WORK/required-boundaries.txt"

	while IFS= read -r boundary; do
		[ -n "$boundary" ] || continue
		if ! grep -Fxq "$boundary" "$WORK/registered-boundaries.txt"; then
			fail "architecture section 8.4 boundary '$boundary' has no declared test"
		fi
	done <"$WORK/required-boundaries.txt"

	while IFS= read -r boundary; do
		[ -n "$boundary" ] || continue
		if ! grep -Fxq "$boundary" "$WORK/required-boundaries.txt"; then
			fail "registry declares boundary '$boundary' that architecture section 8.4 does not define"
		fi
	done <"$WORK/registered-boundaries.txt"

	awk -F'\t' '$1 == "interleaving" { print $2 }' "$WORK/entries.tsv" | sort -u >"$WORK/registered-interleavings.txt"
	for interleaving in $REQUIRED_INTERLEAVINGS; do
		phrase="$(interleaving_phrase "$interleaving")"
		if ! grep -Fq "$phrase" "$WORK/section-8-4.txt"; then
			fail "architecture section 8.4 no longer names the required interleaving '$phrase'"
		fi
		if ! grep -Fxq "$interleaving" "$WORK/registered-interleavings.txt"; then
			fail "required interleaving '$interleaving' has no declared test"
		fi
	done

	while IFS= read -r interleaving; do
		[ -n "$interleaving" ] || continue
		found=0
		for required in $REQUIRED_INTERLEAVINGS; do
			[ "$required" = "$interleaving" ] && found=1
		done
		if [ "$found" -eq 0 ]; then
			fail "registry declares interleaving '$interleaving' that architecture section 8.4 does not require"
		fi
	done <"$WORK/registered-interleavings.txt"
fi

# --- per-entry deterministic-coordination checks -----------------------------
checked=0
while IFS="$(printf '\t')" read -r kind key suite test_name coordination; do
	[ -n "${kind:-}" ] || continue
	checked=$((checked + 1))
	label="$key -> $suite::$test_name"

	case "$kind" in
	boundary | interleaving) ;;
	*)
		fail "$label declares unknown kind '$kind'"
		continue
		;;
	esac

	if [ -z "${key:-}" ] || [ -z "${suite:-}" ] || [ -z "${test_name:-}" ] || [ -z "${coordination:-}" ]; then
		fail "$label is missing a required registry column"
		continue
	fi

	suite_path="$SUITE_DIR/$suite"
	if [ ! -f "$suite_path" ]; then
		fail "$label names a suite that does not exist: $suite_path"
		continue
	fi

	occurrences="$(awk -v name="$test_name" '
		{ line = $0; sub(/^[[:blank:]]+/, "", line) }
		line == "@test \"" name "\" {" { count++ }
		END { print count + 0 }
	' "$suite_path")"
	if [ "$occurrences" -ne 1 ]; then
		fail "$label names a test declared $occurrences times in $suite"
		continue
	fi

	awk -v name="$test_name" '
		{ line = $0; sub(/^[[:blank:]]+/, "", line) }
		!inside && line == "@test \"" name "\" {" { inside = 1; next }
		inside && $0 == "}" { exit }
		inside { print }
	' "$suite_path" >"$WORK/body.txt"

	if ! grep -Fq "\"$coordination\"" "$WORK/body.txt"; then
		fail "$label never references its declared boundary \"$coordination\""
	fi

	hooks_declared=0
	for module in $MODULES; do
		if grep -Eq '_lock_transition_fault|_event_publication_fault|_ledger_projection_fault' "$module"; then
			hooks_declared=1
			break
		fi
	done
	if [ "$hooks_declared" -eq 0 ]; then
		fail "no configured module declares fault hooks"
	fi
	boundary_declared=0
	for module in $MODULES; do
		if grep -Fq "_fault(\"$coordination\"" "$module"; then
			boundary_declared=1
			break
		fi
	done
	if [ "$boundary_declared" -eq 0 ]; then
		fail "$label targets boundary \"$coordination\", which no configured module invokes"
	fi

	has_process=1
	grep -Eq 'subprocess\.(Popen|run)\(' "$WORK/body.txt" || has_process=0
	has_kill=1
	grep -Eq '\.kill\(\)|os\.kill\(' "$WORK/body.txt" || has_kill=0
	has_barrier=1
	grep -Fq 'stdout.readline()' "$WORK/body.txt" || has_barrier=0
	has_sleep=1
	grep -Eq 'time\.sleep\(|(^|[^[:alnum:]_-])sleep[[:space:]]+[0-9.]' "$WORK/body.txt" || has_sleep=0

	deterministic=1
	[ "$has_process" -eq 1 ] || deterministic=0
	if [ "$has_kill" -eq 0 ] && [ "$has_barrier" -eq 0 ]; then
		deterministic=0
	fi
	if [ "$kind" = "boundary" ] && [ "$has_kill" -eq 0 ]; then
		deterministic=0
	fi

	if [ "$deterministic" -eq 0 ]; then
		if [ "$has_sleep" -eq 1 ]; then
			fail "$label coordinates only by timing; a barrier or fault hook must target boundary \"$coordination\""
		else
			fail "$label has no deterministic coordination: it needs a real coordinated process and a SIGKILL or barrier read at boundary \"$coordination\""
		fi
	fi

	if ! grep -Eq 'publish_control_event\(|publish-event' "$WORK/body.txt"; then
		fail "$label proves no liveness: a later writer must complete a mutation after the interruption"
	fi
done <"$WORK/entries.tsv"

if [ "$failures" -ne 0 ]; then
	printf '%s: %d declared coverage problem(s) in %s\n' "$PREFIX" "$failures" "$REGISTRY" >&2
	exit 1
fi

printf '%s: %d declared interruption boundaries and interleavings are deterministically proven\n' "$PREFIX" "$checked"
