# VP1 — `copilotcockpit`: One-command bootstrap for the Copilot E2E testing harness

| Field | Value |
|-------|-------|
| Product | **copilotcockpit** |
| Vision phase | VP1 — E2E Bootstrap (MVP) |
| Maps to theme | TH1 — Bootstrap Tooling |
| Status | Proposed |
| Date | 2026-06-16 |

---

## 1. The one-liner

> **`copilotcockpit`** turns a cold project into a fully-wired, AI-operated,
> self-auditing Playwright E2E cockpit in about ten minutes — with a single
> command and no copy-paste archaeology.

---

## 2. The problem

We have proven, battle-tested E2E harnesses living inside two real products —
`ulysses-portal` and `ulysses-index`. Each of them has, painstakingly assembled by
hand:

- a Dockerised Playwright runner that works on Ubuntu 26 (where bundled browsers refuse to launch);
- a **governed** test runner (`run-audit.sh`) that writes a permanent, three-tier audit trail (`INDEX.md` → `AUDIT-YYYY-MM.md` → `RUN-*.yaml`);
- a Gherkin **test-book** (`test-book/CH*.md`) cross-linked to spec files by `@TC-XXX-NNN` tags;
- a **tmux cockpit** that spins up an overseer plus three AI workers (`worker-dev`, `worker-fix`, `worker-test`) and live log panes;
- a set of Copilot **skills** — global playbooks in `~/.copilot/skills/` plus thin repo-local overlays in `.github/skills/` that teach each agent the project's topology.

The trouble is that **none of this is reusable today**. When a developer wants the
same harness in a third project, they:

1. `cp -r` an `e2e/` directory from whichever sibling repo they remember has the
   newest version — and inherit that project's app name, ports, k8s context and
   stale audit history;
2. hand-edit a dozen `# ── CONFIGURE ──` blocks, hoping they found them all;
3. manually discover that the global skills (`e2e-cockpit`, `e2e-operator`,
   `setup-e2e-*`, `worker-*`) even need to exist in `~/.copilot/skills/`, then
   hand-copy them from yet another machine;
4. forget the `cockpit-wake` binary entirely until a scheduled wake-up silently
   never fires;
5. end up with two subtly divergent copies of "the harness" and no source of truth.

This is **slow, error-prone, and undocumented**. Every new project re-derives the
same setup from folklore. There is no canonical version, no upgrade path, and no
way to answer the question *"is this project's harness up to date?"*.

### Pain, quantified

| Pain | Today | Target |
|------|-------|--------|
| Time to a green smoke run in a new project | half a day of copy-paste-fix | **~10 minutes** |
| Source of truth for the harness | "whichever repo is newest" | **this repo** |
| Global skills install | manual, undocumented | **one command, idempotent** |
| Upgrading an existing harness | re-copy and re-edit by hand | **`--update`, content-preserving** |
| Onboarding a teammate | tribal knowledge | **`git clone && ./bootstrap.sh`** |

---

## 3. Who is this for?

- **The developer starting a new product** who wants the same E2E discipline they
  enjoyed on the last one, without reverse-engineering it.
- **The overseer / tech lead** who wants every project's harness to share one
  versioned source of truth, so improvements propagate instead of forking.
- **The AI agents themselves** (`setup-e2e-cockpit`, `setup-e2e-runbook`,
  `worker-*`) — `copilotcockpit` gives them a known-good skeleton to complete,
  rather than a blank page to invent.

---

## 4. The product in one picture

```
                        ┌────────────────────────────────────┐
                        │         copilotcockpit repo         │
                        │  (canonical source of truth)        │
                        │                                     │
                        │   skills/        templates/e2e/     │
                        │   bin/cockpit-wake                  │
                        │   bootstrap.sh                      │
                        └───────────┬──────────────┬──────────┘
                                    │              │
              (a) global install   │              │  (b) per-project scaffold
                                    ▼              ▼
                  ┌──────────────────────┐   ┌──────────────────────────┐
                  │  ~/.copilot/skills/  │   │  <your-project>/e2e/      │
                  │  ~/.local/bin/       │   │  (its own git sub-repo)   │
                  │                      │   │                          │
                  │  e2e-cockpit         │   │  playwright.config.ts     │
                  │  e2e-operator        │   │  run-audit.sh             │
                  │  setup-e2e-cockpit   │   │  governance/  test-book/  │
                  │  setup-e2e-runbook   │   │  tests/  runs/            │
                  │  worker-dev/fix/test │   │  .github/skills/ overlays │
                  │  cockpit-wake (bin)  │   │  tmux-cockpit*.sh         │
                  └──────────────────────┘   └──────────────────────────┘
```

---

## 5. The two-phase bootstrap

`copilotcockpit` does exactly two jobs. They are independent and each is idempotent.

### Phase (a) — Global skills install

> *"Make my machine able to operate a cockpit."*

```bash
# Cold install (no clone needed) — fetches a versioned release tarball:
bash <(curl -fsSL https://github.com/<org>/copilotcockpit/releases/latest/download/install.sh)

# …or from a local clone:
git clone https://…/copilotcockpit && cd copilotcockpit
./bootstrap.sh global          # or: ./bootstrap.sh global --link  (dev mode)
```

The cold-install one-liner downloads a checksum-verified GitHub Releases tarball and
runs `global` for you — no `git` required (see architecture §8 / ADR-007).

This installs/updates the canonical skill playbooks into `~/.copilot/skills/` and
the `cockpit-wake` binary into `~/.local/bin/`:

- `e2e-cockpit` — **the overseer's playbook** (there is no separate `worker-overseer` skill; `e2e-cockpit` *is* the overseer skill)
- `e2e-operator` — governed test operator (the `worker-test` pane)
- `setup-e2e-cockpit` — AI agent that generates `tmux-cockpit*.sh`
- `setup-e2e-runbook` — AI agent that generates the Gherkin test-book + spec stubs
- `worker-dev` — developer worker role
- `worker-fix` — troubleshooter worker role
- `worker-test` — test-operator worker role (loads `e2e-operator` for its protocol)
- `cockpit-wake` — scheduled-awakening CLI

**Skill ↔ cockpit-pane mapping.** Each pane is primed with its global skill *and* the
matching repo-local overlay from `.github/skills/`:

| tmux pane     | Global skill loaded | Local overlay (`.github/skills/`) |
|---------------|---------------------|-----------------------------------|
| `overseer`    | `e2e-cockpit`       | `e2e-cockpit/SKILL.md`            |
| `worker-test` | `e2e-operator`      | `e2e-operator/SKILL.md`           |
| `worker-dev`  | `worker-dev`        | `worker-dev/SKILL.md`             |
| `worker-fix`  | `worker-fix`        | `worker-fix/SKILL.md`             |

The two `setup-e2e-*` skills are not bound to a standing pane — they are run on
demand (`/setup-e2e-cockpit`, `/setup-e2e-runbook`) to generate the harness.

Re-running it later **updates** the skills to the repo's current version. The repo
*is* the source of truth for these files.

### Phase (b) — Per-project E2E scaffold

> *"Wire E2E into this project."*

```bash
cd copilotcockpit
./bootstrap.sh e2e ~/git/my-new-app
```

This drops a complete, parameterisable `e2e/` directory into the target project —
initialised as **its own git sub-repo** (mirroring how `ulysses-portal/e2e` and
`ulysses-index/e2e` already work) — containing:

- Playwright infra: `package.json`, `playwright.config.ts`, `global-setup.ts`, `global-teardown.ts`
- Governed runner: `run-audit.sh`, `run-playwright.sh`
- Governance: `governance/GOVERNANCE.md`, `governance/run-schema.yaml`, `governance/flaky-known.md`
- Test-book skeleton: `test-book/SUMMARY.md`, `test-book/TC-FORMAT.md`, `test-book/CH01-smoke.md`
- Tests skeleton: `tests/helpers.ts`, `tests/smoke.spec.ts`
- Audit trail structure: `runs/INDEX.md`, `runs/.gitkeep`
- Cockpit placeholders: `tmux-cockpit.sh`, `tmux-cockpit-local.sh`
- Worker overlays: `.github/skills/{e2e-cockpit,e2e-operator,setup-e2e-cockpit,setup-e2e-runbook,worker-dev,worker-fix,worker-test}/SKILL.md`
- Context: `.github/copilot-instructions.md`, `.env.example`, `.gitignore`

The scaffold ships with **sensible placeholders and `# ── CONFIGURE ──` blocks**,
not blanks. The smoke test and runner work immediately against a generic
`localhost` stack. The topology-specific finishing — exact ports, k8s context,
backend start commands, feature-domain chapters — is completed **interactively by
the AI setup skills**, which `copilotcockpit` invites the developer to run next:

```bash
cd ~/git/my-new-app
copilot --yolo                 # then: /setup-e2e-cockpit   (generates tmux scripts)
                               # then: /setup-e2e-runbook    (builds the test-book)
```

This is the key insight: **`copilotcockpit` provides the skeleton; the AI skills
fill in the muscle.** The bootstrap gets you to a runnable harness in minutes; the
setup agents tailor it to the app in the next session.

---

## 6. The journey, end to end

> *Maya has just scaffolded a new internal service, `acme-portal`. She wants the
> same E2E cockpit she had on her last project.*

1. **Install (once per machine).**
   `bash <(curl -fsSL …/releases/latest/download/install.sh)` — or, with a clone,
   `git clone copilotcockpit && cd copilotcockpit && ./bootstrap.sh global`
   → the 7 harness skills (plus the `copilotcockpit-dev` contributor skill) land in
   `~/.copilot/skills/`, and `cockpit-wake` lands on her PATH.
   *(~2 min)*

2. **Scaffold the project.**
   `./bootstrap.sh e2e ~/git/acme-portal`
   → `acme-portal/e2e/` appears, is `git init`-ed, dependencies install, and the
   script prints: *"Smoke harness ready. Next: run `/setup-e2e-cockpit`."* *(~3 min)*

3. **Prove it runs.**
   `cd ~/git/acme-portal && ./e2e/run-audit.sh --scope "@smoke"`
   → the Dockerised runner executes the three placeholder smoke tests and writes
   the first row to `e2e/runs/INDEX.md`. The audit trail is born. *(~1 min)*

4. **Tailor with AI.**
   `copilot --yolo` → `/setup-e2e-cockpit` discovers the app's ports, k8s context
   and backend command, then writes the real `tmux-cockpit*.sh` and worker
   overlays. `/setup-e2e-runbook` reads the app's routes and OpenAPI spec and
   generates `CH02…CHnn` chapters plus spec stubs. *(~4 min of agent time)*

5. **Launch the cockpit.**
   `./e2e/tmux-cockpit-local.sh` → overseer + `worker-dev` + `worker-fix` +
   `worker-test` panes come up, each primed with the global skill *and* the
   freshly-written repo overlay. Maya is now operating an AI test army against her
   brand-new app. *(seconds)*

**Total: ~10 minutes from cold clone to a live, self-auditing cockpit.**

> *Three weeks later, the harness gets a better JUnit parser upstream. Maya runs
> `./bootstrap.sh e2e ~/git/acme-portal --update`. The framework files refresh; her
> test-book chapters, her specs, her `runs/` history and her `.env.local` are left
> untouched.*

---

## 7. Key outcomes

| Outcome | What it means |
|---------|---------------|
| **Self-documenting audit trail** | Every test run is permanently recorded across `INDEX.md` / `AUDIT-YYYY-MM.md` / `RUN-*.yaml`. Anyone can answer "what was tested, when, against which SHAs, with what result?" forever. |
| **Governed test runs** | `run-audit.sh` is the single sanctioned entry point. Metadata, scope, and per-TC results are captured automatically — discipline is the default, not an afterthought. |
| **AI-operated cockpit** | A tmux session of Copilot agents — overseer + dev + fix + test — with live log panes, ready to triage and fix failures. |
| **One source of truth** | Skills and templates live in *this* repo. Improvements made here propagate to every project via `global` re-install and `e2e --update`. |
| **AI-completed setup** | The skeleton is generic and runnable; `setup-e2e-cockpit` and `setup-e2e-runbook` finish the topology- and feature-specific work interactively. |

---

## 8. Non-functional requirements

| # | NFR | Rationale |
|---|-----|-----------|
| NFR-1 | **Idempotent** — re-running any bootstrap command converges to the same state and never destroys user work. | Bootstrap must be safe to run repeatedly during setup and upgrades. |
| NFR-2 | **Cross-platform (Linux + macOS)** — pure POSIX-ish bash; no GNU-only flags that break on macOS BSD tools. | Both OSes are in the developer fleet. |
| NFR-3 | **Minimal dependencies** — only `bash`, `git`, `node`/`npm`, `docker`, and `python3` (already required by the runner and `cockpit-wake`). No package manager, no runtime framework. | The harness must install on a fresh machine with near-zero prerequisites. |
| NFR-4 | **Self-updating skills** — `bootstrap.sh global` both installs and upgrades; a `--link` dev mode symlinks so edits in the repo take effect live. | Skills evolve; the install path must double as the upgrade path. |
| NFR-5 | **Content-preserving updates** — `e2e --update` refreshes framework files while never touching project-owned content (tests, test-book chapters, `runs/`, env files). | Upgrading must be a no-brainer, not a merge nightmare. |
| NFR-6 | **No network at scaffold time** beyond `npm install` and the Playwright Docker pull. | Works behind corporate proxies; deterministic. |
| NFR-7 | **Fast** — global install < 30 s; project scaffold (excluding `npm install`) < 30 s; full cold journey ≈ 10 min. | The whole point is speed. |
| NFR-8 | **Discoverable & self-explaining** — `bootstrap.sh` with no args prints usage; every generated script keeps its `--help` and `# ── CONFIGURE ──` annotations. | A developer who finds the repo should succeed without reading the wiki. |

---

## 9. Explicit non-goals (for VP1)

- **Not** a Playwright/test-framework abstraction — it ships Playwright as-is; it does not wrap or hide it.
- **Not** a CI system — it produces a harness that CI can call (`run-audit.sh --scope @smoke`), but pipelines are out of scope.
- **Not** the place where the AI *content* generation lives — that is owned by the `setup-e2e-*` skills. VP1 ships the skeleton and the install/scaffold/update tooling.
- **Not** multi-language test runners — Playwright + TypeScript only for VP1 (a future VP may template Cypress/pytest variants).
- **Not** a published npm/Homebrew package — VP1 is `git clone && ./bootstrap.sh`. Packaging is a later VP if demand appears.

---

## 10. Success criteria (Definition of "VP1 done")

1. A developer can go from `git clone copilotcockpit` to a **green `@smoke` run in a new project in under 15 minutes**, following only the repo README.
2. `./bootstrap.sh global` installs all 7 harness skills (plus the `copilotcockpit-dev` contributor skill) + `cockpit-wake`, and re-running it updates them — verified idempotent (second run reports "already current" / makes no destructive change).
3. `./bootstrap.sh e2e <dir>` produces an `e2e/` byte-comparable in structure to `ulysses-index/e2e/`, initialised as its own git repo, with a passing smoke harness.
4. `./bootstrap.sh e2e <dir> --update` refreshes framework files and provably leaves `tests/`, `test-book/CH02+`, `runs/`, and `.env.local` untouched.
5. The generated scaffold cleanly hands off to `/setup-e2e-cockpit` and `/setup-e2e-runbook` (the placeholders are exactly what those agents expect to complete).
6. Every technology and structural choice is backed by an ADR (ADR-001 … ADR-006).
