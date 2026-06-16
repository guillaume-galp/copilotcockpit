#!/usr/bin/env bash
# run-audit.sh — Governed E2E test runner with audit logging (single entry point).
#
# Wraps run-playwright.sh with:
#   - Component SHA snapshots (git branch/sha/dirty for each E2E_COMPONENTS entry)
#   - Scope declaration (--scope maps to Playwright --grep, e.g. "@smoke")
#   - Per-TC result mapping from JUnit XML via @TC-XXX-NNN title tags
#   - A machine-readable run record  →  runs/RUN-{ts}-{env}.yaml
#   - A compact index row            →  runs/INDEX.md          (one line per run, forever)
#   - A monthly per-TC digest        →  runs/AUDIT-YYYY-MM.md  (full detail, one file/month)
#
# Usage:
#   ./run-audit.sh                              # full suite, auto-detect env
#   ./run-audit.sh --scope "@smoke"             # smoke gate
#   ./run-audit.sh --scope "@smoke or @major"   # smoke + major gate
#   ./run-audit.sh --project chromium           # one project only
#   ./run-audit.sh --label "pre-release" --scope "@smoke"
#   ./run-audit.sh --env staging --scope "@smoke"
#   ./run-audit.sh --help                       # this help
#
# See governance/GOVERNANCE.md for the full maneuver guide.
#
# ── CONFIGURE: git component tracking ────────────────────────────────────────
# List the source components to snapshot in each run record. Format:
#   "name:relative-path-from-PROJECT_DIR"  (space-separated; one entry = one YAML block)
# Examples:
#   E2E_COMPONENTS="app:."                              # monorepo (single component)
#   E2E_COMPONENTS="frontend:frontend backend:backend"  # split repo
# Default: track the project root as a single "app" component.
E2E_COMPONENTS="${E2E_COMPONENTS:-app:.}"

set -euo pipefail

print_usage() {
  sed -n '2,23p' "$0" | sed -E 's/^# ?//'
}

# Portable script dir (no readlink -f; pure-POSIX cd/pwd per portability cheat-sheet).
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd -P)"
RUNS_DIR="$SCRIPT_DIR/runs"

# ── Parse flags ───────────────────────────────────────────────────────────────
SCOPE="all"
PROJECT="chromium"
LABEL=""
ENV_OVERRIDE=""

while [ $# -gt 0 ]; do
  case "$1" in
    --scope)    SCOPE="$2";        shift 2 ;;
    --project)  PROJECT="$2";      shift 2 ;;
    --label)    LABEL="$2";        shift 2 ;;
    --env)      ENV_OVERRIDE="$2"; shift 2 ;;
    --help|-h)  print_usage; exit 0 ;;
    *)
      printf 'Unknown flag: %s  (use --help for usage)\n' "$1" >&2
      exit 2
      ;;
  esac
done

mkdir -p "$RUNS_DIR"

# Derive env file (e.g. --env staging → .env.staging), else default to .env.local.
if [ -n "$ENV_OVERRIDE" ]; then
  ENV_FILE="$SCRIPT_DIR/.env.${ENV_OVERRIDE}"
else
  ENV_FILE="$SCRIPT_DIR/.env.local"
fi

# ── Timestamps (portable forms only — no `date -d`) ──────────────────────────
RUN_TS_FILE=$(date -u +"%Y%m%d-%H%M%S")
RUN_TS_HUMAN=$(date -u +"%Y-%m-%dT%H:%M:%SZ")

# ── Auto-detect environment ───────────────────────────────────────────────────
if [ -n "$ENV_OVERRIDE" ]; then
  DETECTED_ENV="$ENV_OVERRIDE"
elif [ -f "$ENV_FILE" ]; then
  FRONTEND_URL=$(grep -E '^FRONTEND_URL=' "$ENV_FILE" | cut -d= -f2- | tr -d '"' || echo "")
  if printf '%s' "$FRONTEND_URL" | grep -q "localhost"; then
    DETECTED_ENV="local"
  else
    DETECTED_ENV="remote"
  fi
else
  DETECTED_ENV="local"
fi

RUN_ID="RUN-${RUN_TS_FILE}-${DETECTED_ENV}"
RUN_FILE="$RUNS_DIR/${RUN_ID}.yaml"

# ── Read URLs from env file (fall back to generic localhost dev stack) ───────
FE_URL=$(grep -E '^FRONTEND_URL=' "$ENV_FILE" 2>/dev/null | cut -d= -f2- | tr -d '"' || echo "http://localhost")
BE_URL=$(grep -E '^BACKEND_URL='  "$ENV_FILE" 2>/dev/null | cut -d= -f2- | tr -d '"' || echo "http://localhost")
[ -n "$FE_URL" ] || FE_URL="http://localhost"
[ -n "$BE_URL" ] || BE_URL="http://localhost"

# ── Snapshot git metadata for each configured component (E2E_COMPONENTS) ─────
CODE_YAML=""
DISPLAY_SHAS=""

for comp_spec in $E2E_COMPONENTS; do
  comp_name="${comp_spec%%:*}"
  comp_rel="${comp_spec##*:}"
  comp_dir="$PROJECT_DIR/$comp_rel"

  if [ -d "$comp_dir" ]; then
    branch=$(cd "$comp_dir" && git rev-parse --abbrev-ref HEAD 2>/dev/null || echo "unknown")
    sha=$(cd "$comp_dir" && git rev-parse --short HEAD 2>/dev/null || echo "unknown")
    dirty="false"
    ( cd "$comp_dir" && git diff --quiet HEAD 2>/dev/null ) || dirty="true"

    CODE_YAML="${CODE_YAML}  ${comp_name}:
    branch: ${branch}
    sha: ${sha}
    dirty: ${dirty}
"
    DISPLAY_SHAS="${DISPLAY_SHAS}  ${comp_name} SHA: ${sha} (${branch})\n"
  else
    CODE_YAML="${CODE_YAML}  ${comp_name}:
    branch: unknown
    sha: unknown
    dirty: false
"
    DISPLAY_SHAS="${DISPLAY_SHAS}  ${comp_name} SHA: unknown (not found at ${comp_rel})\n"
  fi
done

# ── Playwright image version (portable parse — no `grep -P`) ──────────────────
PW_IMAGE="${PLAYWRIGHT_IMAGE:-mcr.microsoft.com/playwright:v1.60.0-noble}"
PW_VERSION=$(printf '%s' "$PW_IMAGE" | sed -E 's/.*:v([0-9.]+).*/\1/')

# ── Print run header ──────────────────────────────────────────────────────────
printf '\n'
printf '═══════════════════════════════════════════════════════════════════\n'
printf '  %s\n' "$RUN_ID"
printf '  env: %s  scope: %s  project: %s\n' "$DETECTED_ENV" "$SCOPE" "$PROJECT"
[ -n "$LABEL" ] && printf '  label: %s\n' "$LABEL"
printf '%b' "$DISPLAY_SHAS"
printf '═══════════════════════════════════════════════════════════════════\n\n'

# ── Build Playwright args from scope/project ─────────────────────────────────
PW_ARGS=()
[ "$SCOPE" != "all" ] && PW_ARGS+=(--grep "$SCOPE")
[ "$PROJECT" != "all" ] && PW_ARGS+=(--project "$PROJECT")

# ── Run the tests (delegated to the Docker runner) ───────────────────────────
START_EPOCH=$(date -u +%s)
EXIT_CODE=0
cd "$SCRIPT_DIR"
E2E_ENV_FILE="$ENV_FILE" bash "$SCRIPT_DIR/run-playwright.sh" "${PW_ARGS[@]}" || EXIT_CODE=$?
END_EPOCH=$(date -u +%s)
DURATION_S=$(( END_EPOCH - START_EPOCH ))

printf '\nRun complete — exit code: %s — duration: %ss\n\n' "$EXIT_CODE" "$DURATION_S"

# ── Parse JUnit XML → per-TC results (python3 for portability) ───────────────
JUNIT_XML="$SCRIPT_DIR/test-results/junit.xml"

CASES_YAML=$(python3 - "$JUNIT_XML" <<'PYEOF'
import sys, xml.etree.ElementTree as ET, re, os

xml_path = sys.argv[1]
if not os.path.exists(xml_path):
    print("  # JUnit XML not found — no test results to parse")
    sys.exit(0)

root = ET.parse(xml_path).getroot()
cases = []
for tc in root.iter('testcase'):
    name  = tc.get('name', '')
    cname = tc.get('classname', '')
    duration_ms = int(float(tc.get('time', 0)) * 1000)

    file_path = cname.replace(' \u25ba ', '/').replace('\u25ba', '/').strip()
    if not file_path:
        file_path = tc.get('file', 'unknown')

    m = re.search(r'@(TC-[A-Z]+-\d+)', name)
    tc_id = m.group(1) if m else 'unmapped'

    # Playwright JUnit reporter emits <error>; standard JUnit uses <failure>.
    # NOTE: an ElementTree element with no children is falsy, so a
    # <failure message="…">text</failure> would be wrongly treated as absent
    # under `or`. Use explicit `is not None` to classify failures correctly.
    failure = tc.find('failure') if tc.find('failure') is not None else tc.find('error')
    skipped = tc.find('skipped')

    if skipped is not None:
        status, error = 'skipped', None
    elif failure is not None:
        status = 'failed'
        msg = failure.get('message', '') or (failure.text or '')
        error = next((l.strip() for l in msg.splitlines() if l.strip()), msg)[:120]
    else:
        status, error = 'passed', None

    cases.append((tc_id, name, status, duration_ms, file_path, error))

for (tc_id, title, status, dur, fpath, err) in cases:
    print(f"  - id: {tc_id}")
    print('    title: "%s"' % title.replace('"', '\\"'))
    print(f"    status: {status}")
    print(f"    duration_ms: {dur}")
    print(f"    file: {fpath}")
    if err:
        print('    error: "%s"' % err.replace('"', '\\"'))
PYEOF
)

# ── Compute summary counts from the same XML ─────────────────────────────────
SUMMARY_COUNTS=$(python3 - "$JUNIT_XML" <<'PYEOF'
import sys, xml.etree.ElementTree as ET, os

xml_path = sys.argv[1]
if not os.path.exists(xml_path):
    print("total: 0\npassed: 0\nfailed: 0\nskipped: 0\nflaky: 0")
    sys.exit(0)

root = ET.parse(xml_path).getroot()
total = skipped = failed = 0
for tc in root.iter('testcase'):
    total += 1
    if tc.find('skipped') is not None:
        skipped += 1
    elif tc.find('failure') is not None or tc.find('error') is not None:
        failed += 1

passed = total - failed - skipped
print(f"total: {total}")
print(f"passed: {passed}")
print(f"failed: {failed}")
print(f"skipped: {skipped}")
print("flaky: 0")
PYEOF
)

T_TOTAL=$(printf '%s' "$SUMMARY_COUNTS" | grep '^total:'   | awk '{print $2}')
T_PASS=$(printf '%s'  "$SUMMARY_COUNTS" | grep '^passed:'  | awk '{print $2}')
T_FAIL=$(printf '%s'  "$SUMMARY_COUNTS" | grep '^failed:'  | awk '{print $2}')
T_SKIP=$(printf '%s'  "$SUMMARY_COUNTS" | grep '^skipped:' | awk '{print $2}')

if [ "${T_FAIL:-0}" -gt 0 ]; then
  STATUS_ICON="⚠️"
elif [ "${T_TOTAL:-0}" -eq 0 ]; then
  STATUS_ICON="🔵"
else
  STATUS_ICON="✅"
fi

# ── Build an indented copy of the summary for valid YAML nesting ─────────────
SUMMARY_INDENTED=$(printf '%s\n' "$SUMMARY_COUNTS" | sed 's/^/  /')

# ── Write the machine-readable run record ────────────────────────────────────
cat > "$RUN_FILE" <<YAML
run_id: ${RUN_ID}
timestamp: "${RUN_TS_HUMAN}"
label: "${LABEL}"
env: ${DETECTED_ENV}
project: ${PROJECT}
scope: "${SCOPE}"

urls:
  frontend: "${FE_URL}"
  backend:  "${BE_URL}"

code:
${CODE_YAML}
playwright_version: "${PW_VERSION}"
runner: docker
runner_image: "${PW_IMAGE}"

duration_s: ${DURATION_S}
exit_code: ${EXIT_CODE}

summary:
${SUMMARY_INDENTED}
cases:
${CASES_YAML}
YAML

printf 'Run record written → %s\n' "$RUN_FILE"

# ── Update the monthly per-TC digest ─────────────────────────────────────────
MONTH_KEY=$(date -u +"%Y-%m")
DIGEST_FILE="$RUNS_DIR/AUDIT-${MONTH_KEY}.md"
LABEL_STR=""
[ -n "$LABEL" ] && LABEL_STR=" — *${LABEL}*"

AUDIT_ROWS=$(python3 - "$JUNIT_XML" <<'PYEOF'
import sys, xml.etree.ElementTree as ET, re, os

xml_path = sys.argv[1]
if not os.path.exists(xml_path):
    print("| — | — | no test results | — | — |")
    sys.exit(0)

root = ET.parse(xml_path).getroot()
for tc in root.iter('testcase'):
    name = tc.get('name', '')
    dur = f"{int(float(tc.get('time', 0)) * 1000)}ms"

    m = re.search(r'@(TC-[A-Z]+-\d+)', name)
    tc_id = m.group(1) if m else '—'

    failure = tc.find('failure') if tc.find('failure') is not None else tc.find('error')
    skipped = tc.find('skipped')

    if skipped is not None:
        icon, note = '\u23ed', '—'
    elif failure is not None:
        icon = '\u274c'
        msg = failure.get('message', '') or (failure.text or '')
        note = next((l.strip() for l in msg.splitlines() if l.strip()), msg)[:80]
    else:
        icon, note = '\u2705', '—'

    safe_name = name.replace('|', '\\|')[:60]
    safe_note = note.replace('|', '\\|')[:80]
    print(f"| {icon} | `{tc_id}` | {safe_name} | {dur} | {safe_note} |")
PYEOF
)

DIGEST_BLOCK="
---

## ${STATUS_ICON} ${RUN_ID}${LABEL_STR}

> ${RUN_TS_HUMAN} · env: \`${DETECTED_ENV}\` · scope: \`${SCOPE}\` · project: \`${PROJECT}\`
> **${T_PASS}/${T_TOTAL} passed** · ${T_FAIL} failed · ${T_SKIP} skipped · ${DURATION_S}s

| | TC-ID | Title | Duration | Note |
|--|-------|-------|----------|------|
${AUDIT_ROWS}
"

if [ ! -f "$DIGEST_FILE" ]; then
  MONTH_HUMAN=$(date -u +"%B %Y")
  cat > "$DIGEST_FILE" <<HEADER
# E2E Audit Digest — ${MONTH_HUMAN}

Auto-generated by \`run-audit.sh\` — **DO NOT EDIT BY HAND**.
Full run records (per-TC detail) → \`runs/RUN-*.yaml\`
Compact index of all runs → \`runs/INDEX.md\`
HEADER
fi

# Insert newest block just under the 5-line header (temp-file + mv; no sed -i).
DIGEST_HEADER=$(head -n 5 "$DIGEST_FILE")
DIGEST_BODY=$(tail -n +6 "$DIGEST_FILE")
printf '%s\n%s\n%s\n' "$DIGEST_HEADER" "$DIGEST_BLOCK" "$DIGEST_BODY" > "${DIGEST_FILE}.tmp"
mv "${DIGEST_FILE}.tmp" "$DIGEST_FILE"
printf 'Monthly digest updated → %s\n' "$DIGEST_FILE"

# ── Append the compact index row to INDEX.md ─────────────────────────────────
INDEX_FILE="$RUNS_DIR/INDEX.md"
RUN_NUM=1

if [ -f "$INDEX_FILE" ]; then
  LAST_NUM=$(grep -E '^\| [0-9]+' "$INDEX_FILE" | head -n 1 | awk -F'|' '{print $2}' | tr -d ' ' || true)
  [ -n "$LAST_NUM" ] && RUN_NUM=$(( LAST_NUM + 1 ))
fi

TS_SHORT=$(printf '%s' "$RUN_TS_HUMAN" | cut -c1-16)

if [ ! -f "$INDEX_FILE" ]; then
  cat > "$INDEX_FILE" <<'HEADER'
# E2E Run Index

Auto-generated by `run-audit.sh` — **DO NOT EDIT BY HAND**.

Reading guide for agents:
- **This file**: scan for a run by date/label/result — one row per run, always compact
- **Monthly digest**: `runs/AUDIT-YYYY-MM.md` — full per-TC table for that month
- **Run detail**: `runs/RUN-{id}.yaml` — machine-readable per-TC status + error snippets
- **Failures query**: `grep "status: failed" runs/RUN-*.yaml`

Legend: ✅ all passed · ⚠️ some failed · 🔵 no results · ❌ runner error

| # | Run ID | Timestamp | Env | Scope | Result | ✅ | ❌ | ⏭ | s | Label |
|---|--------|-----------|-----|-------|--------|---|---|---|---|-------|
HEADER
fi

LABEL_SAFE="${LABEL:-—}"
NEW_ROW="| ${RUN_NUM} | ${RUN_ID} | ${TS_SHORT} | ${DETECTED_ENV} | ${SCOPE} | ${STATUS_ICON} | ${T_PASS} | ${T_FAIL} | ${T_SKIP} | ${DURATION_S} | ${LABEL_SAFE} |"

# Newest row goes directly under the table separator (temp-file + mv; no sed -i).
SEP_LINE=$(grep -n '^|---' "$INDEX_FILE" | head -n 1 | cut -d: -f1 || true)
if [ -n "$SEP_LINE" ]; then
  head -n "$SEP_LINE" "$INDEX_FILE" > "${INDEX_FILE}.tmp"
  printf '%s\n' "$NEW_ROW" >> "${INDEX_FILE}.tmp"
  tail -n +$(( SEP_LINE + 1 )) "$INDEX_FILE" >> "${INDEX_FILE}.tmp"
  mv "${INDEX_FILE}.tmp" "$INDEX_FILE"
else
  printf '%s\n' "$NEW_ROW" >> "$INDEX_FILE"
fi
printf 'Index updated → %s\n' "$INDEX_FILE"

# ── Print summary ─────────────────────────────────────────────────────────────
printf '\n'
printf '═══════════════════════════════════════════════════════════════════\n'
printf '  %s  %s\n' "$STATUS_ICON" "$RUN_ID"
printf '  %s/%s passed  ·  %s failed  ·  %s skipped  ·  %ss\n' "$T_PASS" "$T_TOTAL" "$T_FAIL" "$T_SKIP" "$DURATION_S"
printf '  index  → runs/INDEX.md\n'
printf '  digest → runs/AUDIT-%s.md\n' "$MONTH_KEY"
printf '  detail → runs/%s.yaml\n' "$RUN_ID"
printf '═══════════════════════════════════════════════════════════════════\n\n'

exit "$EXIT_CODE"
