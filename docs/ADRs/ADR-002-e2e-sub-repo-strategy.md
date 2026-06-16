# ADR-002 — `e2e/` as a git sub-repo vs. a plain directory

| Field | Value |
|-------|-------|
| Status | Proposed |
| Date | 2026-06-16 |
| Deciders | [architect agent] |
| Theme | TH1 |

## Context

`bootstrap.sh e2e <target>` scaffolds an `e2e/` directory into a target project.
A structural decision must be made: is `e2e/` a **plain directory committed to the
parent project's git history**, or is it **initialised as its own independent git
repository** (a `.git/` of its own inside `e2e/`)?

**Evidence from the field:** both reference implementations already chose the
independent-repo model — `ulysses-portal/e2e/.git/` and `ulysses-index/e2e/.git/`
are real, separate git repositories with their own history. The audit trail
(`runs/RUN-*.yaml`, `INDEX.md`, monthly digests) is committed there, and the
harness explicitly snapshots the *parent* project's component SHAs via
`E2E_COMPONENTS` in `run-audit.sh` — i.e. the e2e repo records, but does not share,
the app's history.

Forces:
- The audit trail is high-churn (a YAML + index/digest edit per test run). Keeping
  that out of the parent's history keeps the app repo clean.
- E2E may want its **own remote, own CI, own access control** (test engineers vs app
  developers).
- The `run-audit.sh` design already treats the parent app as an *external*
  component to snapshot, not as the same repo.
- Counter-force: a nested `.git/` inside a directory that the parent might try to
  commit can confuse contributors ("why is `e2e/` empty in the parent?").

## Decision

**`bootstrap.sh e2e <target>` initialises `<target>/e2e/` as its own independent
git repository** (`git init` inside `e2e/`), matching both reference projects.

- The scaffold runs `git init` in `e2e/`, writes the `e2e/.gitignore`, and makes an
  initial commit of the skeleton.
- The bootstrap **does not** automatically add `e2e/` to the parent repo, nor
  configure a submodule — it leaves the integration choice to the developer and
  documents the two supported options:
  1. **Ignore in parent** — add `/e2e/` to the parent `.gitignore` (simplest;
     e2e history lives only locally or on its own remote).
  2. **Submodule** — `git submodule add <e2e-remote> e2e` once an e2e remote exists.
- A `--no-git` flag is provided to scaffold a plain directory for users who
  explicitly want the parent to own the files.

## Consequences

### Positive
- Matches the proven, existing topology — generated projects look like the references.
- The noisy audit trail stays out of the parent app's history.
- E2E can have an independent remote, CI pipeline, and reviewers.
- `run-audit.sh`'s component-SHA snapshotting model is consistent (parent = external).

### Negative / Trade-offs
- A nested `.git/` can surprise contributors; the parent shows `e2e/` as empty/ignored
  unless a submodule is configured. Mitigation: bootstrap prints a clear explanation
  and the two integration options; `.github/copilot-instructions.md` documents it.
- Two `git status` contexts to reason about. Mitigation: this is already the norm in
  both reference projects, so it is familiar.

### Risks
- A developer commits the parent with `e2e/` accidentally staged as a gitlink with no
  remote. Mitigation: bootstrap suggests adding `/e2e/` to the parent `.gitignore` by
  default and prints the exact line.

## Alternatives Considered

### A. Plain directory committed to the parent repo
- Pros: one history; no nested-repo confusion; trivial for contributors.
- Cons: pollutes the app history with per-run audit churn; cannot give E2E its own
  remote/CI/access control; diverges from both reference implementations.
- **Rejected** as the default because it loses the independent audit/CI benefits the
  references rely on — but **offered as `--no-git`** for teams who prefer it.

### B. Always add `e2e/` as a git submodule of the parent
- Pros: parent tracks an explicit e2e commit pointer; clean separation *and* linkage.
- Cons: requires an e2e remote to exist *before* scaffolding; submodules are famously
  fiddly; forces a workflow on day one when the developer may not have a remote yet.
- **Rejected as automatic behaviour** (too much friction at scaffold time) but
  **documented as the recommended path** once an e2e remote exists.

## History
- 2026-06-16: Proposed
