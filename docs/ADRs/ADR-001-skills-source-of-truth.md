# ADR-001 — Skills source-of-truth strategy

| Field | Value |
|-------|-------|
| Status | Proposed |
| Date | 2026-06-16 |
| Deciders | [architect agent] |
| Theme | TH1 |

## Context

The Copilot E2E harness relies on **exactly seven** global skill playbooks that the
user has installed at `~/.copilot/skills/<role>/SKILL.md`:

| # | Skill | Role in the cockpit |
|---|-------|---------------------|
| 1 | `e2e-cockpit` | **The overseer's skill.** There is *no* separate `worker-overseer` skill — `e2e-cockpit` *is* the overseer playbook (its SKILL.md opens with "You are the **overseer** in a tmux E2E cockpit"). Loaded in the `overseer` pane. |
| 2 | `e2e-operator` | Governed test operator — loaded in the `worker-test` pane. |
| 3 | `setup-e2e-cockpit` | AI setup agent — generates `tmux-cockpit*.sh` + overlays. |
| 4 | `setup-e2e-runbook` | AI setup agent — generates the Gherkin test-book + spec stubs. |
| 5 | `worker-dev` | Developer worker role — `worker-dev` pane. |
| 6 | `worker-fix` | Troubleshooter worker role — `worker-fix` pane. |
| 7 | `worker-test` | Test-operator worker role — `worker-test` pane (loads `e2e-operator` for its protocol). |

(plus the `cockpit-wake` CLI, handled separately in [ADR-005](ADR-005-cockpit-wake-distribution.md)).

This repo manages these seven **E2E-harness** skills — it does not vendor the
general Copilot Build Method skills (`architecture-decisions`, `bdd-stories`, etc.),
which are owned elsewhere.

> Update: [ADR-008](ADR-008-copilotcockpit-dev-skill.md) later adds an **8th**
> repo-managed skill, `copilotcockpit-dev` — a *contributor* runbook for developing
> this repo, distinct from the seven *harness* skills above. The source-of-truth and
> install mechanism decided here apply to it unchanged.

Today these files exist only in the user's home directory. There is **no versioned
source of truth**: if a teammate needs them, or a machine is rebuilt, the only
recovery path is to copy `~/.copilot/skills/` from another machine — undocumented
and unauditable. The vision (VP1 §2, §7) demands a single canonical source from
which installs and upgrades flow.

The question: *should the skills live as **source files committed in this repo**
(the repo being the canonical origin of `~/.copilot/skills/`), or should the repo
merely **document install snippets** (e.g. `curl … > ~/.copilot/skills/…`) that
pull from elsewhere?*

Forces:
- Skills are plain Markdown (`SKILL.md`) — small, diffable, reviewable.
- They evolve; the install path must double as the upgrade path (NFR-4).
- They must install on a machine that may be offline/behind a proxy (NFR-6).
- We want one place where an improvement is made and reviewed.

## Decision

**The skills are stored as source files in `copilotcockpit/skills/<role>/SKILL.md`,
and this repo is the canonical source of truth for `~/.copilot/skills/`.**

`bootstrap.sh global` installs them by **copying** each `skills/<role>/SKILL.md`
into `~/.copilot/skills/<role>/SKILL.md`. Before overwriting a differing existing
file it writes a timestamped backup (`SKILL.md.bak-<ts>`). A `--link` mode instead
**symlinks** the installed file back to the repo, for live skill-authoring.

The seven skills are vendored into the repo's `skills/` directory by copying the
current `~/.copilot/skills/<role>/SKILL.md` contents in as the initial committed
versions, after which the repo — not the home directory — is authoritative.

## Consequences

### Positive
- Single, versioned, reviewable source of truth; skill changes go through normal git review.
- Install path == upgrade path: `global` both installs and updates (NFR-4).
- Works offline — no network fetch needed (NFR-6).
- A new machine is one `git clone && ./bootstrap.sh global` away from a full install.
- `--link` mode gives skill authors a tight edit-test loop.

### Negative / Trade-offs
- The repo now carries copies of the skills, which can drift from a user's locally
  hand-edited `~/.copilot/skills/*`. Mitigation: `global` backs up before overwrite;
  `bootstrap.sh doctor` reports drift.
- The user's `~/.copilot/skills/` also contains *general* Copilot Build Method skills
  (`architecture-decisions`, `the-copilot-build-method`, etc.) that this repo does
  **not** manage — `global` touches only the seven E2E-harness skills, leaving the
  others untouched, to avoid becoming a fork of unrelated playbooks.

### Risks
- **Stale vendored copy** if upstream skills change outside this repo. Mitigation:
  treat this repo as the origin going forward; `doctor` surfaces divergence so it is
  noticed and reconciled deliberately.

## Alternatives Considered

### A. Documented `curl`-install snippets (no source in repo)
- Pros: nothing to keep in sync; tiny repo.
- Cons: requires network and a hosting origin; not reviewable as code; no offline
  install; the "source of truth" just moves to some gist/URL.
- **Rejected because** it fails NFR-6 (offline) and defeats the "one source of truth"
  goal — the files would live nowhere we control and review.

### B. Separate dedicated `copilot-skills` repo, consumed as a submodule
- Pros: clean separation; skills reusable beyond the cockpit.
- Cons: submodule friction; two repos to clone/track for one bootstrap; over-engineered
  for seven Markdown files at MVP.
- **Rejected because** it adds operational weight disproportionate to the problem
  (P1 simplicity). May be revisited if the skill set grows large and is shared widely.

### C. Symlink-only install (no copy mode)
- Pros: zero drift — `~/.copilot/skills` always equals the repo.
- Cons: breaks if the repo is moved/deleted; surprising for users who expect their
  home dir to be self-contained.
- **Rejected as the default** but **kept as `--link`** for authors who want it.

## History
- 2026-06-16: Proposed
