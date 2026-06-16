# ADR-008 — `copilotcockpit-dev` skill & GitOps release flow

| Field | Value |
|-------|-------|
| Status | Proposed |
| Date | 2026-06-16 |
| Deciders | [architect agent] |
| Theme | TH1 |

## Context

`copilotcockpit` is itself a software product (bash + Python + templates) that will
evolve continuously: new skills, runner improvements, template fixes, release-pipeline
tweaks. ADR-007 established a **tag-driven release pipeline** — a push of a `vX.Y.Z`
tag builds and publishes the install tarball. But nothing yet defines **how a change
gets from idea to that tag** in a disciplined, repeatable, auditable way.

Today that delivery process is implicit/tribal: branch however you like, test (or
not), merge (or push to `main`), bump the version (by hand, maybe), tag (if you
remember). For a repo that is *the canonical source of truth* for every project's E2E
harness, that is exactly the kind of folklore-driven process VP1 set out to kill — but
turned inward, on this repo itself.

We want an AI agent operating **inside this repo** to own the full
feature-delivery → release pipeline autonomously, following GitOps best practices, with
a clear test gate at every step and a clear escalation path when it gets stuck.

Forces:
- The delivery process must be **encoded as a runnable, versioned artefact** — not a
  wiki page — so both humans and agents follow the same steps (consistent with
  ADR-001: skills are the canonical, in-repo source of truth for playbooks).
- It must integrate with the **existing** release pipeline (ADR-007), not replace it.
- It needs an **explicit test taxonomy** so "tests pass" is unambiguous.
- Semver must have **one source of truth** to avoid drift.
- Autonomy must be **bounded** — an agent that loops forever fixing CI is worse than
  one that escalates.

## Decision

**Introduce a new Copilot CLI skill, `copilotcockpit-dev`, stored at
`skills/copilotcockpit-dev/SKILL.md` and installed globally to
`~/.copilot/skills/copilotcockpit-dev/` by `bootstrap.sh global` — making it the
**8th** repo-managed skill.** The skill is the canonical runbook for delivering any
change to `copilotcockpit`, encoding the GitOps flow, the test taxonomy, the
conventional-commit → semver mapping, and a bounded escalation policy.

### Branch & commit conventions

- Work on `feature/<slug>` or `fix/<slug>`, branched from `main` at latest HEAD.
  **Never commit directly to `main`** (except the version-bump commit in Phase 3).
- **Conventional Commits** are enforced; the skill validates the message *before*
  pushing. Types: `feat`, `fix`, `perf`, `refactor`, `docs`, `chore`, `test`, `ci`,
  with optional `!` / `BREAKING CHANGE:` footer.

### Conventional commit → version bump mapping

| Commit type | Version bump | Releases? |
|-------------|--------------|-----------|
| `feat:` | **minor** (`0.X.0`) | yes |
| `fix:` / `perf:` | **patch** (`0.0.X`) | yes |
| `feat!:` / any `BREAKING CHANGE:` footer | **major** (`X.0.0`) | yes |
| `chore:` / `docs:` / `refactor:` / `test:` / `ci:` (alone) | **none** | no — skip Phases 3–4 |

### Test taxonomy (the gate)

Five categories, run via a `run-tests.sh <category>` dispatcher:

| # | Category | Tool | Scope | When | Gate |
|---|----------|------|-------|------|------|
| 1 | **Script unit** | `bats` (`tests/unit/cmd-*.bats`) | each `lib/cmd-*.sh` function: idempotency (run twice = same), dry-run = no side-effects, error paths (missing arg, wrong dir) exit with correct code+message | local + CI-on-PR | must pass |
| 2 | **Template integrity** | `tests/template/check-template.sh` | `*.tmpl` have no unresolved tokens post-substitution; `package.json.tmpl` is valid JSON after substitution; `MANIFEST.toml` accounts for **every** file in `templates/e2e/`; `run-audit.sh` & `run-playwright.sh` pass `bash -n` | CI-on-PR | must pass |
| 3 | **Skills lint** | `tests/skills/lint-skills.sh` (+ `python3 -c "import yaml"`) | every `skills/*/SKILL.md` is valid Markdown with parseable frontmatter; `name:`/`description:` exist and are non-empty | CI-on-PR | must pass |
| 4 | **Integration smoke** | `bats` (`tests/integration/smoke.bats`) | `global --dry-run` prints "would copy" for all 8 skills + cockpit-wake; `e2e <tmp> --dry-run` prints the expected scaffold file list; `doctor` exits 0 and reports each prerequisite found/missing | CI-on-PR + post-release | must pass |
| 5 | **Release asset validation** | post-publish CI job | download the published tarball, verify SHA-256, extract, run `global --dry-run` + `doctor` from the extracted dir | CI post-release only | must pass (else fail the release) |

Local pre-commit gate = categories 1–4 (`./run-tests.sh unit|template|skills|integration`).
Category 5 runs only in the release workflow.

### The 12-step flow (encoded as a checklist in the skill)

**Phase 1 — Feature/fix delivery on a branch**
1. Branch `feature/<slug>` or `fix/<slug>` from latest `main`.
2. Implement; then run the local gate: `./run-tests.sh unit && … template && … skills && … integration`.
3. All green → conventional commit, push branch, `gh pr create` against `main`.
4. Monitor CI: `gh pr checks --watch` / `gh run watch` until all checks pass.
5. CI fails → read the log (`gh run view --log-failed`), fix, re-push. **Max 3 auto-fix attempts**, then escalate.

**Phase 2 — Merge gating**
6. PR may merge only when: (a) all CI jobs green, (b) no merge conflicts, (c) PR title follows Conventional Commits.
7. `gh pr merge --squash --auto`. **Never force-push to `main`; never merge with failing checks.**

**Phase 3 — Version bump + tag**
8. `git pull --ff-only origin main`; derive bump type from the squashed commit type (table above). If "none" → stop here.
9. Update `VERSION` (plain semver, e.g. `1.2.3`) and prepend `## v1.2.3 — YYYY-MM-DD` to `CHANGELOG.md` with the squash commit body.
10. Commit `chore: bump version to v1.2.3 [skip ci]`; push directly to `main`.
11. `git tag -a v1.2.3 -m "Release v1.2.3"`; `git push origin v1.2.3`.

**Phase 4 — Release confirmation**
12. The tag triggers `release.yml` (ADR-007). Monitor `gh run watch`; confirm the tarball + `.sha256` assets are published and Category-5 validation is green.

### Escalation policy

Bounded autonomy: **3 auto-fix attempts** at any failing CI/test gate; after the third,
the agent stops, summarises the failure + what it tried, and raises to the human (PR
comment + cockpit message). No silent loops.

### `gh` CLI commands used

`gh pr create`, `gh pr checks --watch`, `gh run watch`, `gh run view --log-failed`,
`gh pr merge --squash --auto`, `gh release view` (confirm assets).

### Single source of semver truth

The **`VERSION`** file (plain semver string) is authoritative. There is no
`package.json` version field to keep in sync. `release.yml` reads the tag; `VERSION`
and the tag are kept equal by step 9–11.

## Consequences

### Positive
- The delivery process is a **versioned, in-repo runbook** — humans and agents follow
  the identical steps; improvements to the process go through the same review as code.
- **Disciplined, auditable history**: linear `main` (squash), Conventional Commits,
  annotated release tags, agent-maintained `CHANGELOG.md`.
- **Unambiguous quality gate**: five named test categories with explicit tools and
  CI timing; "tests pass" has a precise meaning.
- **Bounded autonomy**: the 3-attempt rule prevents runaway loops while still letting
  the agent self-heal common CI failures.
- **Clean integration with ADR-007**: this ADR owns idea→tag; ADR-007 owns tag→release;
  Category 5 validates the published artefact end-to-end.
- **No semver drift**: one `VERSION` file, derived mechanically from commit type.

### Negative / Trade-offs
- Introduces **new dev dependencies** (`bats` for tests, `gh` CLI for the flow) — but
  only for *contributors* to `copilotcockpit`, not for end users installing the harness;
  `bootstrap.sh global`/`e2e` remain dependency-light (P5 preserved for users).
- The agent must reliably parse commit messages to choose the bump; a misclassification
  could cut a wrong-sized release. Mitigation: the mapping is a small explicit table; the
  PR description states "Merging this will trigger release vX.Y.Z" for human review before merge.
- `[skip ci]` on the bump commit is a magic string CI must honour; if a provider ignores
  it, a redundant CI run (not a loop, since it makes no further commits) occurs.
  Mitigation: documented in `ci.yml`; the bump commit changes only `VERSION`/`CHANGELOG.md`.
- More moving parts in the repo (a `tests/` tree, two workflows). Mitigation: this is
  proportionate for a canonical source-of-truth repo and is itself covered by Category 1–2 tests.

### Risks
- An 8th skill widens the install surface; a malformed `copilotcockpit-dev/SKILL.md`
  could break `global`. Mitigation: Category 3 (skills lint) gates it on every PR.
- Auto-merge (`--auto`) merging the moment checks pass could surprise a human mid-review.
  Mitigation: `--auto` still respects branch-protection required reviews; the skill only
  enables it after the agent's own gate is green and the PR body is populated.

## Alternatives Considered

### A. Document the release process in `README`/`CONTRIBUTING.md` (no skill)
- Pros: zero new install surface; familiar.
- Cons: prose is not executable; agents can't reliably follow it; drifts from reality —
  the exact failure mode VP1 targets. Not the canonical, versioned artefact we want.
- **Rejected** — a skill is the in-repo, agent-consumable source of truth (consistent with ADR-001).

### B. Fold the dev workflow into the existing `setup-e2e-*` or a generic skill
- Pros: fewer skills.
- Cons: those skills are about *operating the harness in a target app*; this is about
  *developing copilotcockpit itself* — a different audience and lifecycle. Overloading
  them muddies both.
- **Rejected** — distinct responsibility deserves a distinct skill.

### C. Fully automated release-on-merge (semantic-release style, no agent judgement)
- Pros: zero human/agent steps after merge; deterministic.
- Cons: a heavyweight toolchain (Node `semantic-release` or equivalent) added purely to
  bump versions; removes the agent's ability to write a meaningful changelog and the
  human's pre-merge "this will release vX.Y.Z" checkpoint; conflicts with P5/P1.
- **Rejected** — the agent-driven flow with a `VERSION` file is simpler, dependency-lighter,
  and keeps a human checkpoint, while still being automatable.

### D. Trunk-based with no PRs (commit straight to `main`, tag when ready)
- Pros: fastest; no PR ceremony.
- Cons: no CI gate before code lands on the canonical branch; no review checkpoint; high
  blast radius for the source-of-truth repo.
- **Rejected** — unacceptable risk for a repo every project depends on.

## History
- 2026-06-16: Proposed
