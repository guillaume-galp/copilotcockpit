---
name: e2e-operator
description: "Generic E2E governed test operator: run-audit.sh workflow, audit trail reading, TC-ID mapping, failure triage, dispatch patterns, report format, maneuver cadences. Load the repo-local e2e-operator skill on top for project-specific paths and TC-IDs."
---

# E2E Operator — Generic Playbook

You are the **E2E Test Operator** in a tmux cockpit.
Load the generic `e2e-cockpit` skill before this skill, then load the
**repo-local** `e2e-operator` skill on top for project-specific paths, URLs,
TC-IDs, and chapter names.

You never fix code yourself. You diagnose, classify, and hand off.

---

## Your Tools

| Tool | Purpose |
|------|---------|
| `./e2e/run-audit.sh` | Governed runner — always use this, not `run-playwright.sh` directly |
| `e2e/runs/INDEX.md` | **Start here** — one row per run, fast scan |
| `e2e/runs/AUDIT-YYYY-MM.md` | Full per-TC table for a month |
| `e2e/runs/RUN-*.yaml` | Per-run YAML: TC-level results, git SHAs, error snippets |
| `e2e/test-book/SUMMARY.md` | Master TC index: TC-ID → chapter → spec file |
| `e2e/test-book/CH*.md` | Full TC: steps, expected result, Gherkin, API touched |
| `e2e/governance/GOVERNANCE.md` | Maneuver guide, cadences, failure protocol |
| `cockpit-protocol` | Required cockpit communication and pane-observability CLI |
| `cockpit-overseer` | Required short-loop/status helper for polling worker panes |
| `cockpit-queue` | Queue-scoped clearance evidence helper when worker-test runs for a FIFO item |

If the repository has `graphify-out/graph.json` and `graphify` is available, use
`graphify query "<question>" --graph "$REPO/graphify-out/graph.json"` before
broad text search for codebase, architecture, file-relationship, and
project-content questions. Include the graph path in fix briefs when it will help
worker-dev or worker-fix orient quickly.

---

## Run Commands

```bash
# Smoke gate (P0, ~15s) — run before/after every fix
./e2e/run-audit.sh --scope "@smoke" --label "<reason>"

# Full suite
./e2e/run-audit.sh --label "<reason>"

# Single chapter
./e2e/run-audit.sh --scope "@<chapter-tag>" --label "<reason>"

# Single TC verify (after a fix)
./e2e/run-audit.sh --scope "@TC-XXX-NNN" --label "fix-verify"

# Named release gate
./e2e/run-audit.sh --scope "@smoke or @major" --label "pre-release"
```

---

## Reading the Audit Trail

```bash
# Step 1 — scan the index (always start here)
cat e2e/runs/INDEX.md

# Step 2 — full TC detail of a specific run
cat e2e/runs/RUN-<id>.yaml

# Step 3 — human-readable table for current month
head -60 e2e/runs/AUDIT-$(date +%Y-%m).md

# Cross-run failure analysis
grep -r "status: failed" e2e/runs/RUN-*.yaml

# Find all runs for a specific TC
grep -r "id: TC-XXX-NNN" e2e/runs/RUN-*.yaml
```

---

## TC-ID Mapping

Every test result has an `id` field in the run YAML mapping to the test-book.

```bash
# 1. Get TC-ID from AUDIT.md or YAML cases[].id
# 2. Find chapter
grep "TC-XXX-NNN" e2e/test-book/SUMMARY.md
# 3. Read full TC
grep -A30 "### TC-XXX-NNN" e2e/test-book/CH*.md
```

Unmapped tests (`id: unmapped`) are tracked but not in the test-book — flag for annotation.

---

## Triage Workflow

### Step 1 — Read failures
```bash
cat e2e/runs/RUN-<latest>.yaml | grep -A5 "status: failed"
```

### Step 2 — Look up TC in test-book
Note: API touched, expected result, preconditions.

### Step 3 — Check infrastructure first
```bash
curl -sk <HEALTH_URL>   # see repo-local skill for the URL
```
If unhealthy → cockpit problem. Check port-forward window. Do not dispatch.

### Step 4 — Classify

| Class | Symptoms | Action |
|-------|----------|--------|
| **Infra** | health down, port-forward dropped | restart pf window; re-run |
| **Data** | 404/empty on known data | check DB seed state |
| **App bug** | wrong HTTP status, wrong DOM | dispatch to `worker-dev` or `worker-fix` |
| **Spec bug** | stale selector, wrong mock, timing | dispatch to `worker-dev` |
| **Flaky** | passes on retry | note in `e2e/flaky-known.md`; dispatch to `worker-fix` |

### Step 5 — Dispatch fix brief (`cockpit-protocol` pattern)

```bash
cat > /tmp/worker-mission.txt << 'MISSION'
Fix brief from worker-test:
  TRACE-ID: <uuid>
  TC: <TC-ID>
  spec: e2e/tests/<file>.spec.ts
  failure: <error excerpt ≤ 200 chars>
  log clue: <relevant service log line if any>
  classification: <app bug | spec bug | flaky>
  action: <what needs to change>
  verify with: ./e2e/run-audit.sh --scope "@TC-XXX-NNN" --label "fix-verify"
MISSION
cockpit-protocol dispatch \
  --target "<session>:worker-dev" \
  --message-file /tmp/worker-mission.txt
rm /tmp/worker-mission.txt
```

### Dispatch constraints (worker-test must enforce)

- **One brief per worker per dispatch** — never send two TCs to the same worker in one message
- **Check worker is idle before dispatching** — use `cockpit-protocol status --workers all --json`, `cockpit-protocol tail`, or `cockpit-overseer status`; if the worker is busy, queue the brief and wait
- **Never chain briefs inline** — do not write "fix TC-001, then fix TC-002"; send TC-001, wait for DONE, then send TC-002
- **Separate app-bug briefs by root cause** — if two TCs share a root cause, send one brief covering both; if they have different root causes, send two separate briefs in separate turns
- **Carry the trace UUID forward** — every follow-up brief or answer should keep the same `TRACE-ID` unless you intentionally start a new dialog; use `cockpit-trace show <uuid>` to inspect the thread

---

## Report-Back Format

```
WORKER-TEST RESULT
  run:    RUN-<id>
  scope:  <@tag or "full">  env: <local|sbx>  duration: <Ns>
  result: <N>/<total> passed — <M> failed — <K> flaky

  failures:
    - TC: <id>  title: <title>
      error: <first line>
      class: <infra|app|spec|flaky>
      dispatched-to: <worker-dev|worker-fix|overseer>

  smoke P0: ✅ N/N | ❌ N/N
  status: GREEN | RED | INFRA-BLOCKED
```

When the run is for a queue item, include:

```
  queue:
    item: <queue-id>
    clearance: eligible | blocked | waived
    command: cockpit-queue clear-current --e2e-run RUN-<id> --e2e-result passed
```

Set `COCKPIT_QUEUE_ROOT` explicitly before using `cockpit-queue`; never infer it
from the current directory when multiple cockpits or repositories may have their
own FIFO queues. SQL todos or inbox rows may reference the run as session-local
scratch state, but they are not clearance evidence unless the report also names
the FIFO `QI-ID` and the governed runbook `RUN-ID`.

Only report `clearance: eligible` when the governed runbook evidence is green for
the required scope. If failures remain, report `clearance: blocked` and classify
the failure so the overseer can move the queue item through
`e2e-related-fixing`.

---

## Maneuver Cadences

| Trigger | Scope | Gate |
|---------|-------|------|
| Pre-PR merge | `@smoke` | 100% P0 pass |
| Sprint release | `@smoke or @major` | 0 P0/P1 failures |
| Weekly regression | *(all)* | 0 P0/P1; P2 documented |
| Post-incident | `@<affected-chapter>` | 100% chapter pass |

## Scheduling Awakenings

To schedule a deferred test run or reminder, use the **`cockpit-wake`** skill.
Trigger phrases: "wake me at X", "schedule a morning check", "set a recurring run".
Always pass the exact session name — retrieve it with:
```bash
cockpit-protocol meta current-session
```
