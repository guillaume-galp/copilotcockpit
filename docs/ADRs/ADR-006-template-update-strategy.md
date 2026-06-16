# ADR-006 — Template update strategy

| Field | Value |
|-------|-------|
| Status | Proposed |
| Date | 2026-06-16 |
| Deciders | [architect agent] |
| Theme | TH1 |

## Context

`copilotcockpit` is the source of truth for the e2e harness, so its templates and
skills will improve over time (a better JUnit parser in `run-audit.sh`, a tighter
`playwright.config.ts`, an updated `GOVERNANCE.md`). Existing projects that were
scaffolded earlier need a way to **receive those improvements** without losing the
work they have layered on top (NFR-5).

A scaffolded `e2e/` mixes two ownership classes:

- **Framework-owned** files we want to keep refreshing: `run-audit.sh`,
  `run-playwright.sh`, `playwright.config.ts`, `global-setup/teardown.ts`,
  `governance/GOVERNANCE.md`, `governance/run-schema.yaml`, `test-book/TC-FORMAT.md`,
  `.github/copilot-instructions.md`.
- **Project-owned** files we must **never** clobber: the developer's `tests/`, their
  feature `test-book/CH02…CHnn` chapters and `SUMMARY.md`, the entire `runs/` audit
  history, the AI-generated `tmux-cockpit*.sh` and `.github/skills/*` overlays, and
  `.env.local`.

There is also a middle class — **seed** files written once and then owned by the
project (`package.json`, `.env.example`, the CH01 smoke chapter and its spec).

How does an existing `e2e/` pull updates without a merge nightmare?

## Decision

**Add an idempotent `bootstrap.sh e2e <dir> --update` mode whose refresh decisions are
driven by an ownership manifest (`templates/e2e/MANIFEST.toml`) classifying every path
as `framework`, `project`, or `seed`.**

```
--update behaviour, per MANIFEST.toml class:
  [framework]  → overwrite with the current template version (backup the old copy first)
  [seed]       → create only if missing; never overwrite
  [project]    → never touch
```

- Before overwriting any framework file, `--update` writes a timestamped backup
  (`<file>.bak-<ts>`) so the operation is reversible, and reports a one-line diff
  summary per changed file.
- `--update --dry-run` lists exactly what would change, touch nothing, and exit 0 —
  letting a developer preview an upgrade.
- `--update` requires the target `e2e/` to already exist (otherwise it points the user
  at a plain `e2e` scaffold), keeping the two modes unambiguous.
- The manifest lives **with the template**, so the *correct* ownership rules travel with
  the version of `copilotcockpit` doing the update.

This is intentionally **not** a git-merge of upstream — it is a deterministic,
class-driven file refresh, which is simpler to reason about and safe to re-run (NFR-1).

## Consequences

### Positive
- Existing projects get framework improvements with one command (NFR-5).
- Project content (tests, feature chapters, audit history, env, AI overlays) is provably
  never overwritten — the manifest makes the contract explicit and auditable.
- `--dry-run` makes upgrades previewable and low-anxiety.
- Backups make every refresh reversible.

### Negative / Trade-offs
- A *framework* file the developer hand-edited locally is overwritten on `--update`
  (its prior version is backed up). Mitigation: framework files are documented as
  copilotcockpit-owned; project-specific changes belong in the `# ── CONFIGURE ──`
  blocks of *project*-class files, not in framework files. `--dry-run` shows the hit first.
- The manifest must be kept accurate as the template evolves. Mitigation: a TH1 spike
  validates the classification against a fixture project; CI can assert every template
  path is classified exactly once.

### Risks
- Glob semantics for `MANIFEST.toml` patterns differ subtly in bash. Mitigation:
  validated by the TH1 manifest spike (see architecture §9) before `--update` is relied on.
- A path that is in neither class (forgotten in the manifest) — ambiguous handling.
  Mitigation: default unclassified paths to **`project` (never touch)** — the safe
  failure mode — and have `doctor`/CI flag unclassified template paths.

## Alternatives Considered

### A. Manual update ("re-read the changelog and hand-apply")
- Pros: zero tooling.
- Cons: nobody does it; harnesses rot and diverge — the exact problem VP1 sets out to
  kill.
- **Rejected** — defeats the "one source of truth that propagates" outcome.

### B. `git subtree` / `git submodule` pull of the template
- Pros: real git history and merge machinery.
- Cons: requires `e2e/` to be wired to the template's git history (it is its own repo per
  [ADR-002](ADR-002-e2e-sub-repo-strategy.md), with the *parent app* as the external
  component, not copilotcockpit); subtree/submodule merges surface conflicts in
  project-owned files we explicitly want to leave alone. Heavy machinery for a file refresh.
- **Rejected** — over-engineered and fights the ownership split; a class-driven copy is
  simpler and safer.

### C. Full re-scaffold (delete and regenerate `e2e/`)
- Pros: trivially "up to date".
- Cons: destroys tests, audit history, env, overlays — catastrophic data loss.
- **Rejected** outright — violates NFR-5 and NFR-1.

## History
- 2026-06-16: Proposed
