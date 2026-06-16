# ADR-004 — Template parameterisation mechanism

| Field | Value |
|-------|-------|
| Status | Proposed |
| Date | 2026-06-16 |
| Deciders | [architect agent] |
| Theme | TH1 |

## Context

The `templates/e2e/` scaffold must adapt to each target project. But "adapt" spans
two very different kinds of value:

- **Deterministic, finite knobs** the bootstrap can sensibly default or ask once:
  app name, backend/frontend ports, health path.
- **Topology and feature knowledge** that requires *understanding the app*: exact
  k8s context and namespace, backend start command, pod labels, auth-mock shape, and
  the entire set of feature-domain test-book chapters (`CH02…CHnn`).

The reference implementations make this split visible: their scripts are riddled with
`# ── CONFIGURE ──` blocks and `// CONFIGURE:` comments, and the
`setup-e2e-cockpit` / `setup-e2e-runbook` **AI skills already exist specifically to
discover and fill those in** by exploring the app (reading `package.json`,
`vite.config.ts`, route files, OpenAPI specs, k8s manifests, or asking the user).

The mission frames four candidate mechanisms:
(a) interactive prompts in the bootstrap; (b) a `.e2e-config.yaml` file;
(c) env vars only; (d) two-phase — generate placeholders, then let the AI skills
fill them in.

Forces:
- The bootstrap must be **fast and deterministic** (NFR-7) and **non-interactive-safe**
  for CI (NFR-1).
- Asking a human to enumerate k8s context, pod labels and feature domains up front is
  exactly the slow, error-prone work the AI skills are built to automate.
- The first smoke run must work **without** any topology knowledge (generic localhost).

## Decision

**A two-tier hybrid, with the two-phase model (d) as the backbone and a thin
config/prompt layer (b+a) for the deterministic tokens only.**

### Tier 1 — Deterministic tokens, resolved at scaffold time
A small fixed token set is substituted into `*.tmpl` files:
`@@APP_NAME@@`, `@@BACKEND_PORT@@`, `@@FRONTEND_PORT@@`, `@@HEALTH_PATH@@`.

Resolution order, first hit wins:
1. **`.e2e-config.yaml`** in the target project, if present (CI/repeatable runs);
2. **Interactive prompt** (only when a TTY is attached and `--yes` not given);
3. **Default** (`APP_NAME` = target dir basename; ports `8000`/`5173`; health `/health`).

A non-interactive run (`--yes` or no TTY) silently takes config-or-defaults, so CI
never blocks (NFR-1).

### Tier 2 — Topology & features, resolved post-scaffold by the AI skills
Everything requiring app understanding is **left as annotated `# ── CONFIGURE ──`
placeholders** that ship runnable defaults, and is completed interactively by:
- `/setup-e2e-cockpit` → real `tmux-cockpit.sh` / `tmux-cockpit-local.sh` + worker overlays;
- `/setup-e2e-runbook` → `test-book/CH02…CHnn` + Playwright spec stubs + `SUMMARY.md`.

The bootstrap prints this handoff explicitly as its final step.

## Consequences

### Positive
- Bootstrap stays dumb-fast and deterministic; the smart, slow, interactive discovery
  happens in the AI skills built for it (clean responsibility split, VP1 §5/§6).
- The first `@smoke` run works immediately with zero topology knowledge.
- Optional `.e2e-config.yaml` makes scaffolding fully reproducible in CI without prompts.
- We avoid forcing a human to hand-enumerate k8s/pod/feature details up front.

### Negative / Trade-offs
- The harness is only *fully* tailored after the developer runs the two AI skills — the
  bootstrap alone does not finish the job. Mitigation: this is intentional and clearly
  signposted; the skeleton is runnable in the meantime.
- A simple custom `@@TOKEN@@` substitution (vs a templating engine) is naive. Mitigation:
  the token set is tiny and fixed; `sed`-style substitution is sufficient and dependency-free.

### Risks
- Token collisions with literal `@@…@@` in source. Mitigation: the `@@NAME@@` sentinel
  is reserved and grep-audited in the template; no source legitimately contains it.

## Alternatives Considered

### (a) Interactive prompts only
- Pros: guided; no file to pre-write.
- Cons: blocks CI; tempts us to prompt for topology values the AI should discover.
- **Rejected as the sole mechanism** but **retained for Tier-1 tokens** behind a TTY check.

### (b) Config file (`.e2e-config.yaml`) only
- Pros: reproducible; reviewable.
- Cons: forces the user to author YAML before first run; awkward for a quick try.
- **Rejected as sole mechanism** but **retained as the first resolution source** for Tier-1.

### (c) Env vars only
- Pros: trivial; CI-friendly.
- Cons: undiscoverable; easy to forget; no record of what was chosen.
- **Rejected** — env vars are still honoured as overrides via `common.sh`, but are not
  the primary interface.

### (d) Pure two-phase (placeholders + AI), no Tier-1 layer
- Pros: simplest bootstrap.
- Cons: even trivial knobs (app name, ports) would require an AI session, wasting the
  agent on deterministic work.
- **Rejected as pure form** — adopted as the backbone *with* a thin Tier-1 layer on top.

## History
- 2026-06-16: Proposed
