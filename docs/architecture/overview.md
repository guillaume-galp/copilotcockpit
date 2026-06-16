# Architecture — `copilotcockpit`

> Status: Proposed · Date: 2026-06-16 · Theme: TH1 · Deciders: [architect agent]
> Implements: [VP1](../vision_of_product/VP1-e2e-bootstrap/VP1.md)

This document describes the architecture of the `copilotcockpit` bootstrap repo:
what lives in it, how the two bootstrap phases work, how the e2e template is
parameterised, and how idempotency and updates are guaranteed.

Every significant decision is recorded as an ADR and linked inline.

---

## 1. Design principles

| # | Principle | Consequence in the design |
|---|-----------|---------------------------|
| P1 | **Simplicity first.** | Plain bash + git + a vendored single-file Python script. No build step, no runtime framework, no package to publish (VP1). |
| P2 | **One source of truth.** | The skills and the e2e template live *here*. Installs and scaffolds are *copies from* this repo; updates flow *from* this repo. ([ADR-001](../ADRs/ADR-001-skills-source-of-truth.md)) |
| P3 | **Skeleton, not solution.** | The template ships runnable placeholders + `# ── CONFIGURE ──` blocks. Topology- and feature-specific completion is delegated to the AI `setup-e2e-*` skills. ([ADR-004](../ADRs/ADR-004-template-parameterization.md)) |
| P4 | **Idempotent & non-destructive.** | Re-running any command converges; updates refresh framework files and never touch project-owned content. ([ADR-006](../ADRs/ADR-006-template-update-strategy.md)) |
| P5 | **No external dependencies** beyond what the harness already needs: `bash`, `git`, `node`/`npm`, `docker`, `python3`. | Installs on a near-fresh machine; works behind corporate proxies. |
| P6 | **Self-explaining.** | `bootstrap.sh` with no args prints usage; every script keeps `--help`. |

---

## 2. Repository structure of `copilotcockpit`

```
copilotcockpit/
├── bootstrap.sh                  # single entry point / dispatcher (ADR-003)
├── install.sh                    # cold-install wrapper: fetch+verify+extract+run (ADR-007)
├── run-tests.sh                  # dev test dispatcher: unit|template|skills|integration (ADR-008)
├── VERSION                       # single source of semver truth (ADR-008)
├── README.md                     # quickstart: cold one-liner / clone → global → e2e
├── CHANGELOG.md                  # per-version notes (agent-maintained; release body, §8/§9)
│
├── .github/
│   └── workflows/
│       ├── ci.yml                # PR checks: test categories 1–4 (§9, ADR-008)
│       └── release.yml           # tag-driven release pipeline + cat-5 (§8, ADR-007/008)
│
├── lib/                          # bootstrap implementation (sourced by bootstrap.sh)
│   ├── common.sh                 # logging, OS detection, idempotency helpers
│   ├── cmd-global.sh             # `bootstrap.sh global`  → installs skills + cockpit-wake
│   ├── cmd-e2e.sh                # `bootstrap.sh e2e <dir>` → scaffolds e2e/ (+ --update)
│   └── cmd-doctor.sh             # `bootstrap.sh doctor`  → verifies prerequisites + install state
│
├── tests/                        # dev test suites (ADR-008) — contributor-only
│   ├── unit/cmd-*.bats           # cat 1: per-command bats unit tests
│   ├── template/check-template.sh# cat 2: templates/e2e/ integrity
│   ├── skills/lint-skills.sh     # cat 3: SKILL.md markdown + frontmatter lint
│   └── integration/smoke.bats    # cat 4: global/e2e/doctor --dry-run smoke
│
├── skills/                       # CANONICAL source of the global skills (ADR-001)
│   ├── e2e-cockpit/SKILL.md      # THE OVERSEER skill (no separate worker-overseer)
│   ├── e2e-operator/SKILL.md     # worker-test's protocol
│   ├── setup-e2e-cockpit/SKILL.md
│   ├── setup-e2e-runbook/SKILL.md
│   ├── worker-dev/SKILL.md
│   ├── worker-fix/SKILL.md
│   ├── worker-test/SKILL.md
│   └── copilotcockpit-dev/SKILL.md  # 8th skill: GitOps delivery runbook (ADR-008)
│
├── bin/
│   └── cockpit-wake              # vendored single-file Python CLI (ADR-005)
│
├── templates/
│   └── e2e/                      # the full parameterisable e2e/ scaffold (ADR-002/004)
│       ├── MANIFEST.toml         # framework-owned vs project-owned classification (ADR-006)
│       ├── package.json.tmpl
│       ├── playwright.config.ts
│       ├── global-setup.ts
│       ├── global-teardown.ts
│       ├── run-audit.sh
│       ├── run-playwright.sh
│       ├── tmux-cockpit.sh           # placeholder, completed by /setup-e2e-cockpit
│       ├── tmux-cockpit-local.sh     # placeholder, completed by /setup-e2e-cockpit
│       ├── .env.example.tmpl
│       ├── .gitignore
│       ├── governance/
│       │   ├── GOVERNANCE.md
│       │   ├── run-schema.yaml
│       │   └── flaky-known.md
│       ├── test-book/
│       │   ├── SUMMARY.md
│       │   ├── TC-FORMAT.md
│       │   └── CH01-smoke.md
│       ├── tests/
│       │   ├── helpers.ts
│       │   └── smoke.spec.ts
│       ├── runs/
│       │   ├── INDEX.md
│       │   └── .gitkeep
│       └── .github/
│           ├── copilot-instructions.md.tmpl
│           └── skills/                 # thin repo overlays (one SKILL.md per role)
│               ├── e2e-cockpit/SKILL.md.tmpl
│               ├── e2e-operator/SKILL.md.tmpl
│               ├── setup-e2e-cockpit/SKILL.md.tmpl
│               ├── setup-e2e-runbook/SKILL.md.tmpl
│               ├── worker-dev/SKILL.md.tmpl
│               ├── worker-fix/SKILL.md.tmpl
│               └── worker-test/SKILL.md.tmpl
│
└── docs/                         # The Copilot Build Method artefacts
    ├── vision_of_product/VP1-e2e-bootstrap/VP1.md
    ├── architecture/overview.md   (this file)
    ├── ADRs/ADR-00X-*.md
    └── plan/{backlog.yaml,session-log.md}
```

> **Note on `*.tmpl`:** files whose content depends on per-project values carry a
> `.tmpl` suffix and pass through token substitution at scaffold time. Files with
> no project-specific tokens (e.g. `run-audit.sh`, `governance/*`) are copied
> verbatim. See §6.

---

## 3. Component diagram

```
        DEVELOPER
            │  ./bootstrap.sh <subcommand> [args]
            ▼
   ┌────────────────────┐
   │    bootstrap.sh    │  dispatcher: parses subcommand, sources lib/common.sh,
   │   (entry point)    │  delegates to the matching lib/cmd-*.sh        (ADR-003)
   └─────┬───────┬──────┘
         │       │
   global│       │ e2e <dir> [--update]
         ▼       ▼
 ┌──────────────┐  ┌────────────────────────────────────────────────┐
 │ cmd-global.sh│  │                  cmd-e2e.sh                     │
 └──────┬───────┘  └───────────┬──────────────────────┬─────────────┘
        │                      │                      │
        │ copy/symlink         │ copy template        │ --update: refresh
        ▼                      ▼                      ▼ framework files only
 ┌──────────────────┐   ┌──────────────────┐   ┌──────────────────────┐
 │ ~/.copilot/skills│   │ <project>/e2e/   │   │ MANIFEST.toml drives  │
 │ ~/.local/bin/    │   │ (git init)       │   │ owned/skip decision   │
 │   cockpit-wake   │   │ npm install      │   │       (ADR-006)       │
 └──────────────────┘   └────────┬─────────┘   └──────────────────────┘
   (ADR-001, ADR-005)            │
                                 │ handoff
                                 ▼
                    ┌────────────────────────────────┐
                    │  copilot --yolo                 │
                    │   /setup-e2e-cockpit  ──► tmux-cockpit*.sh + overlays
                    │   /setup-e2e-runbook  ──► test-book CH02+ + spec stubs
                    └────────────────────────────────┘
                          (AI skills complete the topology)
```

---

## 4. Phase (a) — Global skills install (`bootstrap.sh global`)

**Goal:** make `~/.copilot/skills/` and `~/.local/bin/` reflect this repo's
canonical versions.

```
for role in e2e-cockpit e2e-operator setup-e2e-cockpit setup-e2e-runbook \
            worker-dev worker-fix worker-test copilotcockpit-dev:
    install  skills/<role>/SKILL.md   →  ~/.copilot/skills/<role>/SKILL.md
install      bin/cockpit-wake          →  ~/.local/bin/cockpit-wake   (chmod +x)
ensure       ~/.local/bin on PATH      (advise the user if not)
```

> **Eight skills.** The seven E2E-harness skills plus `copilotcockpit-dev` — the
> GitOps delivery runbook for contributors to *this* repo ([ADR-008](../ADRs/ADR-008-copilotcockpit-dev-skill.md)).
> `copilotcockpit-dev` is not bound to a cockpit pane; it is loaded by an agent
> working inside the `copilotcockpit` repo. See §9.

- **Default mode = copy.** A backup of any pre-existing differing file is taken
  (`SKILL.md.bak-<ts>`) before overwrite, so the operation is reversible.
- **`--link` mode = symlink** each `SKILL.md` back to the repo, so edits made in
  `copilotcockpit/skills/` take effect live (skill-development workflow).
- **Idempotent:** a file already identical to source is skipped and reported as
  `already current`; PATH advice is printed only when needed.
- **Source = local clone by default**, or a **versioned GitHub Releases tarball** on a
  cold machine via `global --from-release <latest|vX.Y.Z>` — see §8 and
  [ADR-007](../ADRs/ADR-007-github-release-distribution.md).

### Skill ↔ cockpit-pane mapping

The cockpit binds standing panes to skills as follows. **`e2e-cockpit` is the
overseer's skill** — there is deliberately no `worker-overseer` skill. Each pane is
primed with its global skill plus the matching repo-local overlay (`.github/skills/`),
which the `setup-e2e-cockpit` skill generates per project.

| tmux pane     | Global skill loaded | Local overlay (`.github/skills/`) |
|---------------|---------------------|-----------------------------------|
| `overseer`    | `e2e-cockpit`       | `e2e-cockpit/SKILL.md`            |
| `worker-test` | `e2e-operator`      | `e2e-operator/SKILL.md`           |
| `worker-dev`  | `worker-dev`        | `worker-dev/SKILL.md`             |
| `worker-fix`  | `worker-fix`        | `worker-fix/SKILL.md`             |

The two `setup-e2e-*` skills are run on demand (not bound to a standing pane) to
generate the cockpit scripts and the test-book.

Rationale for copy-as-source-of-truth vs curl-snippets: [ADR-001](../ADRs/ADR-001-skills-source-of-truth.md).
Rationale for vendoring `cockpit-wake`: [ADR-005](../ADRs/ADR-005-cockpit-wake-distribution.md).

---

## 5. Phase (b) — Per-project e2e scaffold (`bootstrap.sh e2e <dir>`)

**Goal:** drop a complete, runnable `e2e/` sub-repo into the target project.

```
1. validate <dir> is a directory (and ideally a git repo)
2. if <dir>/e2e exists and not --update  → refuse, point at --update  (NFR-1)
3. resolve parameters  (config file → prompt → defaults)              (ADR-004)
4. copy templates/e2e/ → <dir>/e2e/      (verbatim files + token-substituted *.tmpl)
5. git init <dir>/e2e                     (independent history)        (ADR-002)
6. (cd <dir>/e2e && npm install)          (Playwright deps)
7. print handoff: run /setup-e2e-cockpit then /setup-e2e-runbook
```

Key choices:

- **`e2e/` is initialised as its own git repo** — matching both reference projects
  where `e2e/.git/` already exists. It can later be pushed to a separate remote or
  attached as a submodule. ([ADR-002](../ADRs/ADR-002-e2e-sub-repo-strategy.md))
- The scaffold's **smoke test and runner work immediately** against a generic
  `localhost` stack — no AI required to get the first green row in `runs/INDEX.md`.
- **`--update`** re-runs step 4 in refresh mode, honouring `MANIFEST.toml`.
  ([ADR-006](../ADRs/ADR-006-template-update-strategy.md))

---

## 6. Parameterisation model

Two tiers of parameterisation, by design ([ADR-004](../ADRs/ADR-004-template-parameterization.md)):

### Tier 1 — Deterministic tokens (resolved at scaffold time)

A small, finite set of values that the bootstrap can sensibly default or prompt for.
They are substituted into `*.tmpl` files:

| Token | Default | Used in |
|-------|---------|---------|
| `@@APP_NAME@@` | basename of target dir | `package.json`, copilot-instructions, overlay headers |
| `@@BACKEND_PORT@@` | `8000` | `.env.example`, smoke defaults |
| `@@FRONTEND_PORT@@` | `5173` | `.env.example` |
| `@@HEALTH_PATH@@` | `/health` | smoke spec, runner preflight comments |

Resolution order: **`.e2e-config.yaml` (if present) → interactive prompt → default.**
A non-interactive run (`--yes` / no TTY) takes defaults silently, so CI and
scripted use never block.

### Tier 2 — Topology & features (resolved by AI, post-scaffold)

Everything that requires *understanding the app* — exact k8s context, backend start
command, pod labels, auth-mock shape, the feature-domain test-book chapters — is
**left as annotated `# ── CONFIGURE ──` placeholders** and completed interactively
by `setup-e2e-cockpit` and `setup-e2e-runbook`. The bootstrap deliberately does not
try to guess these; the AI skills already know how to discover them.

This split keeps the bootstrap dumb-fast and deterministic while letting the smart,
slow, interactive work happen where it belongs.

---

## 7. Idempotency & safety model (NFR-1, NFR-5)

| Situation | Behaviour |
|-----------|-----------|
| `global` run twice | Identical files reported `already current`; differing files backed up then replaced. |
| `e2e <dir>` when `e2e/` already exists | Refuses; instructs to use `--update`. No clobber. |
| `e2e <dir> --update` | Refreshes only files classified `framework` in `MANIFEST.toml`; `project`-owned paths (`tests/`, `test-book/CH0[2-9]*`, `runs/`, `.env.local`, generated `tmux-cockpit*.sh`) are never overwritten. |
| Interrupted scaffold | Scaffold writes into a staging dir then atomically moves into place; a half-finished run leaves no partial `e2e/`. |
| `.env.local` present | Never touched by any command (gitignored, project secret). |

### `MANIFEST.toml` (ownership classification)

```toml
# templates/e2e/MANIFEST.toml — drives `e2e --update` refresh decisions (ADR-006)

[framework]            # always refreshed on --update (copilotcockpit owns these)
paths = [
  "run-audit.sh", "run-playwright.sh",
  "playwright.config.ts", "global-setup.ts", "global-teardown.ts",
  "governance/GOVERNANCE.md", "governance/run-schema.yaml",
  "test-book/TC-FORMAT.md",
  ".github/copilot-instructions.md",
]

[project]              # never overwritten on --update (the project owns these)
paths = [
  "tests/**", "test-book/CH0[2-9]*.md", "test-book/CH1*.md",
  "test-book/SUMMARY.md", "runs/**",
  "tmux-cockpit.sh", "tmux-cockpit-local.sh",   # AI-generated per project
  ".github/skills/**",                          # AI-generated overlays
  ".env.local", ".env.*",
]

[seed]                 # written on first scaffold, NOT refreshed (safe to edit)
paths = [
  "package.json", ".env.example", ".gitignore",
  "test-book/CH01-smoke.md", "tests/smoke.spec.ts", "tests/helpers.ts",
]
```

---

## 8. CI/CD & releases

Cold install (a new machine / teammate with no clone) is the most important
onboarding path (VP1 §6 step 1). It is served by a versioned **GitHub Releases
tarball** ([ADR-007](../ADRs/ADR-007-github-release-distribution.md)), produced by a
release workflow in this repo.

### Release trigger

A workflow at `.github/workflows/release.yml` triggers **only** on push of a
semver release tag on the main line:

```yaml
on:
  push:
    tags:
      - 'v[0-9]+.[0-9]+.[0-9]+'     # v1.0.0, v2.3.1 — release tags only
```

- It does **not** trigger on branch pushes, nor on pre-release tags
  (`v1.0.0-rc1`, `v1.0.0-beta`) — the `v<M.m.f>` glob excludes any tag with a
  `-suffix`.
- Tag the main branch (`git tag v1.2.3 && git push origin v1.2.3`) to cut a release.

### Workflow steps

```
1. Checkout repo (actions/checkout)
2. Assemble the install-surface tarball:
       copilotcockpit-${TAG}.tar.gz
   containing exactly:  bootstrap.sh  lib/  skills/  bin/  templates/  README.md
   (no docs/, no .git/, no CI — the install surface only, per ADR-007)
3. Compute SHA-256:
       sha256sum copilotcockpit-${TAG}.tar.gz > copilotcockpit-${TAG}.tar.gz.sha256
4. Create the GitHub Release (gh CLI):
       gh release create "${TAG}" \
         --title  "copilotcockpit ${TAG}" \
         --notes-file <changelog-section-or-default> \
         --latest \
         copilotcockpit-${TAG}.tar.gz \
         copilotcockpit-${TAG}.tar.gz.sha256 \
         install.sh
   - Body: the CHANGELOG.md section for ${TAG} if present, else a default
     "Bootstrap toolkit release" note.
   - `--latest` makes this the release that the stable
     `releases/latest/download/<asset>` redirect resolves to (no API call needed
     by the one-liner).
5. Publish install.sh as an asset (the minimal curl-extract-run wrapper).
```

A post-publish smoke job (recommended) extracts the freshly-built tarball into a
temp dir and runs `./copilotcockpit/bootstrap.sh global --dry-run` to prove the
artefact is self-sufficient before the release is announced.

### Tarball assembly (sketch)

```bash
TAG="${GITHUB_REF_NAME}"               # e.g. v1.2.3
STAGE="copilotcockpit"
mkdir -p "$STAGE"
cp -r bootstrap.sh lib skills bin templates README.md "$STAGE"/
tar -czf "copilotcockpit-${TAG}.tar.gz" "$STAGE"
```

### The one-liner cold install (developer-facing)

Two equivalent entry points, both pinned to `latest` via the GitHub redirect (so no
API call and no rate limit):

```bash
# Minimal wrapper (recommended in the README)
bash <(curl -fsSL https://github.com/<org>/copilotcockpit/releases/latest/download/install.sh)

# Explicit form (what install.sh does under the hood)
curl -fsSL https://github.com/<org>/copilotcockpit/releases/latest/download/copilotcockpit.tar.gz | tar -xz
./copilotcockpit/bootstrap.sh global
```

`install.sh` is a tiny, auditable wrapper: download the tarball + its `.sha256`,
**verify the checksum**, extract, then exec `bootstrap.sh global "$@"`. It performs no
logic of its own beyond fetch-verify-extract-run, so the trusted code path stays in
`bootstrap.sh`.

> **Boundary note.** This workflow releases *`copilotcockpit` itself*. Generating a CI
> pipeline **for a scaffolded project's `e2e/`** is out of scope for VP1 (§12) — the
> harness merely exposes `run-audit.sh --scope @smoke` for a project's own CI to call.

The post-publish validation referenced above is **test Category 5** (§9): it downloads
the published tarball, verifies its `.sha256`, extracts, and runs `global --dry-run` +
`doctor` from the extracted dir — failing the release if the artefact is broken.

---

## 9. Developer workflow & test strategy

How a change gets from idea to a published release. This is the **inward-facing**
counterpart to the outward-facing harness: it disciplines development of
`copilotcockpit` itself, and is encoded as a runnable skill —
[`copilotcockpit-dev`](../ADRs/ADR-008-copilotcockpit-dev-skill.md), the **8th**
repo-managed skill (§2, §4).

### The `copilotcockpit-dev` skill

`skills/copilotcockpit-dev/SKILL.md` is the canonical, in-repo runbook for delivering a
change. It is installed globally by `bootstrap.sh global` alongside the seven harness
skills, but unlike them it is **not bound to a cockpit pane** — an agent working inside
this repo loads it to own the full feature → release pipeline autonomously, with a
bounded escalation policy (3 auto-fix attempts at any gate, then raise to the human).
Full design, the 12-step flow, and the commit→bump mapping live in
[ADR-008](../ADRs/ADR-008-copilotcockpit-dev-skill.md).

### `tests/` structure + `run-tests.sh`

```
run-tests.sh  <unit|template|skills|integration|all>     # dev test dispatcher
tests/
├── unit/cmd-global.bats  cmd-e2e.bats  cmd-doctor.bats   # cat 1 — bats
├── template/check-template.sh                            # cat 2 — template integrity
├── skills/lint-skills.sh                                 # cat 3 — SKILL.md lint
└── integration/smoke.bats                                # cat 4 — dry-run smoke
```

These are **contributor-only** dependencies (`bats`, `gh`); end users installing the
harness need none of them — `bootstrap.sh` stays dependency-light (P5).

### The five test categories

| # | Category | Tool | Scope (what it proves) | CI gate |
|---|----------|------|------------------------|---------|
| 1 | Script unit | `bats` (`tests/unit/`) | each `lib/cmd-*.sh`: idempotency, dry-run = no side-effects, error paths exit right | local + CI-on-PR |
| 2 | Template integrity | `tests/template/check-template.sh` | `*.tmpl` fully substituted, `package.json.tmpl` valid JSON, `MANIFEST.toml` covers every template file, runners pass `bash -n` | CI-on-PR |
| 3 | Skills lint | `tests/skills/lint-skills.sh` | every `SKILL.md` valid Markdown + parseable frontmatter (`name`/`description` non-empty) | CI-on-PR |
| 4 | Integration smoke | `bats` (`tests/integration/`) | `global --dry-run` lists 8 skills + cockpit-wake; `e2e <tmp> --dry-run` lists scaffold; `doctor` exits 0 | CI-on-PR + post-release |
| 5 | Release asset validation | post-publish CI job | download published tarball, verify SHA-256, extract, `global --dry-run` + `doctor` | CI post-release (release.yml) |

`ci.yml` runs categories 1–4 on every PR push; `release.yml` runs category 5 after
publishing (§8). Local pre-commit gate = categories 1–4 via `run-tests.sh`.

### GitOps flow (branch → PR → merge → tag → release)

```
 feature/<slug> or fix/<slug>  (branched from latest main)
        │  run-tests.sh unit|template|skills|integration  (local gate, all green)
        │  conventional commit  →  git push  →  gh pr create
        ▼
 ┌─────────────────┐   ci.yml: cat 1–4
 │   PR vs main    │──────────────────────►  green?  ──no──► fix (≤3 tries) ──► escalate
 └────────┬────────┘                            │yes
          │ gh pr merge --squash --auto         │  (no conflicts, conventional title)
          ▼
 ┌─────────────────┐  derive bump from commit type (feat→minor, fix→patch, !→major)
 │   main (linear) │  update VERSION + CHANGELOG.md
 └────────┬────────┘  commit "chore: bump version to vX.Y.Z [skip ci]"  → push main
          │ git tag -a vX.Y.Z  →  git push origin vX.Y.Z
          ▼
 ┌─────────────────┐  release.yml (ADR-007): build tarball + sha256 + gh release --latest
 │  Release vX.Y.Z │  then cat-5 validation; agent confirms assets via gh release view
 └─────────────────┘
```

`chore:` / `docs:` / `refactor:` / `test:` / `ci:` commits bump nothing and skip the
tag/release phases.

### `VERSION` + `CHANGELOG.md` ownership

- **`VERSION`** (plain semver string, e.g. `1.2.3`) is the **single source of semver
  truth** — there is no `package.json` version field to keep in sync. The `copilotcockpit-dev`
  agent derives the bump mechanically from the Conventional Commit type and keeps
  `VERSION` equal to the pushed tag.
- **`CHANGELOG.md`** is **agent-maintained**: a new `## vX.Y.Z — YYYY-MM-DD` section is
  prepended from the squash-commit body during Phase 3; `release.yml` sources that
  section into the GitHub Release body (§8). No manual changelog edits are required.

See [ADR-008](../ADRs/ADR-008-copilotcockpit-dev-skill.md) for the full rationale,
escalation policy, and alternatives.

---

## 10. Technology choices (summary)

| Concern | Choice | Why | ADR |
|---------|--------|-----|-----|
| Skills storage | Source files in `skills/`, copied on install | Versioned, reviewable, single source of truth | ADR-001 |
| `e2e/` git model | Own git sub-repo (`git init` inside `e2e/`) | Matches both references; independent history & CI | ADR-002 |
| CLI shape | Single `bootstrap.sh` dispatcher + `lib/cmd-*.sh` | One discoverable entry point, composable internals | ADR-003 |
| Parameterisation | Two-tier: tokens at scaffold + AI for topology | Fast & deterministic where possible, smart where needed | ADR-004 |
| `cockpit-wake` | Vendored single-file Python script → `~/.local/bin` | It already *is* a 344-line stdlib script; no build needed | ADR-005 |
| Updates | `--update` flag driven by `MANIFEST.toml` ownership | Refresh framework, preserve project content | ADR-006 |
| Cold install | Versioned GitHub Releases tarball + `install.sh` one-liner | Deterministic, checksum-verified, no clone needed | ADR-007 |
| Release CI | `.github/workflows/release.yml` on `v*.*.*` tags via `gh` CLI | Tag-driven, reproducible artefacts | ADR-007 |
| Dev workflow | `copilotcockpit-dev` skill + GitOps branch→PR→tag flow | Disciplined, auditable, agent-ownable delivery | ADR-008 |
| Dev tests | `bats` + shell checks via `run-tests.sh`; `ci.yml` gate | Five named categories; unambiguous quality gate | ADR-008 |
| Test runner | Playwright in Docker (`run-playwright.sh`) | Proven on Ubuntu 26 where bundled browsers fail | inherited from references |
| Language | Bash + Python3 (stdlib) + Node/Playwright | Already required by the harness; nothing new | P5 |

---

## 11. Risks & spikes

| Risk | Mitigation / Spike |
|------|--------------------|
| macOS BSD `sed`/`grep`/`readlink` differ from GNU and break bash scripts (NFR-2). | **Spike (TH1):** validate `bootstrap.sh` + generated scripts on macOS; prefer portable constructs; add a `doctor` check. |
| `MANIFEST.toml` glob semantics in bash are fiddly (NFR-5 correctness). | **Spike (TH1):** prove the framework/project/seed classification with a fixture project before relying on `--update`. |
| Drift between `copilotcockpit/skills/*` and a teammate's hand-edited `~/.copilot/skills/*`. | `global` backs up before overwrite; `doctor` reports drift; `--link` mode for skill authors. |
| `cockpit-wake` depends on `at`/`cron` being installed. | `doctor` checks for `at`/`cron`; the skill already documents the dependency. |
| Cold-install tarball drifts from the repo layout / is missing a file (ADR-007). | Post-publish CI smoke job extracts the tarball and runs `global --dry-run`; `install.sh` verifies the `.sha256` before extracting. |
| `copilotcockpit-dev` agent misclassifies the version bump (wrong release size) (ADR-008). | Bump table is explicit; PR body states "Merging triggers release vX.Y.Z" for human review before squash-merge. |

---

## 12. Out of scope (VP1)

CI pipeline templates **for scaffolded projects' `e2e/`**, npm/Homebrew packaging,
non-Playwright runners, and the AI content-generation logic itself (owned by the
`setup-e2e-*` skills). See VP1 §9.

> Note: this excludes the *generated harness's* CI. The release CI for
> `copilotcockpit` itself (§8, ADR-007) **is** in scope for VP1.
